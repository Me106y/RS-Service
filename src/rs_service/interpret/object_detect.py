"""YOLOX-S 目标检测推理（Phase 3 M2，vendored 自 SIRIGeo）。

- 权重：`data/models/object_detection/yolox_s.pth`（Megvii 官方 COCO 预训练，
  Apache-2.0）；
- 网络源码：`interpret/yolox/`（官方 yolox-0.3.0 sdist 的最小 vendor 子集，
  相对导入已改写，剥离 loguru）；
- 预处理与官方 demo 一致：等比缩放到 640×640、灰边 114 填充、不归一化；
- 输出：标注框 BGR 图 + 检测框列表 + 统计；数字由调用方落盘，模型只出结果。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from .yolox.data.coco_classes import COCO_CLASSES
from .yolox.models.yolo_head import YOLOXHead
from .yolox.models.yolo_pafpn import YOLOPAFPN
from .yolox.models.yolox import YOLOX
from .yolox.utils.boxes import postprocess

TEST_SIZE = 640
CONF_THRE = 0.25
NMS_THRE = 0.45
PAD_VALUE = 114.0
DEPTH, WIDTH = 0.33, 0.50  # YOLOX-S 配置
IN_CHANNELS = [256, 512, 1024]
NUM_CLASSES = len(COCO_CLASSES)
MAX_SIDE = 2048

_CACHE: dict[tuple[str, str], torch.nn.Module] = {}


def _read_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"图像读取失败：{path}")
    return image


def write_png(path: str | Path, array: np.ndarray) -> None:
    ok, buffer = cv2.imencode(".png", array)
    if not ok:
        raise ValueError(f"图像编码失败：{path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buffer.tofile(str(path))


def _load_model(model_path: str, device: str) -> torch.nn.Module:
    key = (model_path, device)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"模型文件不存在：{model_path}")
    model = YOLOX(
        YOLOPAFPN(DEPTH, WIDTH, in_channels=IN_CHANNELS),
        YOLOXHead(NUM_CLASSES, width=WIDTH, in_channels=IN_CHANNELS),
    )
    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    state = ckpt.get("model", ckpt) if isinstance(ckpt, dict) else ckpt
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    _CACHE[key] = model
    return model


def warmup(model_path: str, device: str) -> None:
    _load_model(model_path, device)


def _limit_side(image: np.ndarray, max_side: int) -> tuple[np.ndarray, bool]:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image, False
    scale = max_side / longest
    resized = cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, True


def _preproc(image: np.ndarray, size: int = TEST_SIZE) -> tuple[np.ndarray, float]:
    """等比缩放 + 灰边填充（YOLOX 官方不做归一化，直接喂 0-255）。"""
    padded = np.ones((size, size, 3), dtype=np.float32) * PAD_VALUE
    ratio = min(size / image.shape[0], size / image.shape[1])
    new_w, new_h = int(image.shape[1] * ratio), int(image.shape[0] * ratio)
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR).astype(
        np.float32
    )
    padded[:new_h, :new_w] = resized
    return np.ascontiguousarray(padded.transpose(2, 0, 1)), ratio


def _palette(index: int) -> tuple[int, int, int]:
    hue = int(179 * index / max(1, NUM_CLASSES))
    bgr = cv2.cvtColor(np.uint8([[[hue, 200, 255]]]), cv2.COLOR_HSV2BGR)[0][0]
    return int(bgr[0]), int(bgr[1]), int(bgr[2])


def draw_detections(image: np.ndarray, detections: list[dict[str, Any]]) -> np.ndarray:
    canvas = image.copy()
    thick = max(2, int(round(max(canvas.shape[:2]) / 400.0)))
    font_scale = max(0.45, min(canvas.shape[:2]) / 1200.0)
    for det in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in det["box"]]
        color = _palette(det["class_id"])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thick)
        label = f"{det['label']} {det['score']:.2f}"
        (tw, th), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1
        )
        top = max(0, y1 - th - baseline - 4)
        cv2.rectangle(
            canvas, (x1, top), (x1 + tw + 6, top + th + baseline + 4), color, -1
        )
        cv2.putText(
            canvas,
            label,
            (x1 + 3, top + th + 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return canvas


@torch.no_grad()
def detect(
    image_path: str | Path,
    model_path: str | Path,
    *,
    device: str = "cpu",
    max_side: int = MAX_SIDE,
    conf_thre: float = CONF_THRE,
    nms_thre: float = NMS_THRE,
) -> dict[str, Any]:
    """单图目标检测；返回标注图、检测框列表与统计。"""
    started = time.monotonic()
    image = _read_image(image_path)
    original_h, original_w = image.shape[:2]
    image, resized = _limit_side(image, max_side)
    height, width = image.shape[:2]

    model = _load_model(str(model_path), device)
    blob, ratio = _preproc(image)
    outputs = model(torch.from_numpy(blob).unsqueeze(0).to(device))
    raw = postprocess(outputs, NUM_CLASSES, conf_thre, nms_thre)[0]

    detections: list[dict[str, Any]] = []
    if raw is not None:
        for row in raw.cpu().numpy():
            cls_id = int(row[6])
            score = float(row[4] * row[5])
            box = [
                min(max(v / ratio, 0.0), width if i % 2 == 0 else height)
                for i, v in enumerate(row[:4])
            ]
            detections.append(
                {
                    "label": COCO_CLASSES[cls_id],
                    "class_id": cls_id,
                    "score": round(score, 6),
                    "box": [round(float(v), 2) for v in box],
                }
            )
        detections.sort(key=lambda d: d["score"], reverse=True)

    annotated = draw_detections(image, detections)
    scores = [d["score"] for d in detections]
    classes = sorted({d["label"] for d in detections})
    return {
        "annotated": annotated,
        "detections": detections,
        "width": width,
        "height": height,
        "original_width": original_w,
        "original_height": original_h,
        "count": len(detections),
        "class_count": len(classes),
        "classes": classes,
        "score_mean": round(float(np.mean(scores)), 6) if scores else 0.0,
        "score_min": round(float(np.min(scores)), 6) if scores else 0.0,
        "resized": resized,
        "device": device,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
