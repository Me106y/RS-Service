"""SparseMask + OpenEarthMap 8 类语义分割推理（Phase 3 M3）。

- 权重：`data/models/semantic_segmentation/sparsemask_openearthmap.onnx`
  （MIT 代码 / OpenEarthMap 8 类预训练，11.9MB，来自 hf-mirror `geobase/sparsemask`）；
- 类别与调色板随权重同目录 `sparsemask_config.json` 分发（缺省内置 8 类表）；
- 预处理与权重一致：等比缩到长边 1024、/255、OpenEarthMap 均值方差归一化、
  反射填充到 32 的整数倍；
- 输出 [1,8,H/2,W/2] → argmax → 最近邻上采样回原尺寸；
- 结果显示为 8 类调色板色图 + 半透明叠加图，各类面积占比由代码统计。

训练数据谱系 CC BY-NC-SA，限所内非商用使用与分发（PRD R2）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

MAX_SIDE = 1024
PAD_MULTIPLE = 32
IMAGE_MEAN = np.array([0.4325, 0.4483, 0.3879], dtype=np.float32)
IMAGE_STD = np.array([0.0195, 0.0169, 0.0179], dtype=np.float32)

# OpenEarthMap 8 类（与权重同源，RGB 调色板）
OEM_CLASSES = [
    {"id": 0, "name": "裸地", "en": "bareland", "color": [128, 0, 0]},
    {"id": 1, "name": "灌草丛", "en": "rangeland", "color": [0, 255, 0]},
    {"id": 2, "name": "已开发区域", "en": "developed space", "color": [192, 192, 192]},
    {"id": 3, "name": "道路", "en": "road", "color": [255, 255, 255]},
    {"id": 4, "name": "林地", "en": "tree", "color": [49, 139, 87]},
    {"id": 5, "name": "水体", "en": "water", "color": [0, 0, 255]},
    {"id": 6, "name": "耕地", "en": "agriculture land", "color": [127, 255, 0]},
    {"id": 7, "name": "建筑物", "en": "buildings", "color": [255, 0, 0]},
]

_SESSIONS: dict[str, Any] = {}


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


def class_table(model_path: str | Path) -> list[dict[str, Any]]:
    """优先读权重同目录的 sparsemask_config.json，缺省用内置 8 类表。"""
    cfg = Path(model_path).parent / "sparsemask_config.json"
    if not cfg.is_file():
        return [dict(item) for item in OEM_CLASSES]
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        names, colors = data["classes"], data["colors"]
    except (OSError, KeyError, ValueError):
        return [dict(item) for item in OEM_CLASSES]
    zh = {item["en"]: item["name"] for item in OEM_CLASSES}
    return [
        {"id": index, "name": zh.get(name, name), "en": name, "color": list(colors[index])}
        for index, name in enumerate(names)
    ]


def _session(model_path: str) -> Any:
    session = _SESSIONS.get(model_path)
    if session is None:
        import onnxruntime as ort

        session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        _SESSIONS[model_path] = session
    return session


def warmup(model_path: str) -> None:
    _session(model_path)


def _preproc(rgb: np.ndarray, side: int = MAX_SIDE) -> tuple[np.ndarray, float, tuple[int, int]]:
    """等比缩放 + 反射填充到 32 的整数倍，返回 (blob, 缩放比, 填充量)。"""
    height, width = rgb.shape[:2]
    scale = min(1.0, side / max(height, width))
    new_w, new_h = max(1, int(round(width * scale))), max(1, int(round(height * scale)))
    resized = cv2.resize(
        rgb, (new_w, new_h), interpolation=cv2.INTER_AREA
    ).astype(np.float32) / 255.0
    pad_r = (-new_h) % PAD_MULTIPLE
    pad_c = (-new_w) % PAD_MULTIPLE
    if pad_r or pad_c:
        resized = cv2.copyMakeBorder(
            resized, 0, pad_r, 0, pad_c, cv2.BORDER_REFLECT_101
        )
    blob = (resized - IMAGE_MEAN) / IMAGE_STD
    return np.ascontiguousarray(blob.transpose(2, 0, 1)[None]), scale, (pad_r, pad_c)


def _colorize(label_map: np.ndarray, classes: list[dict[str, Any]]) -> np.ndarray:
    palette = np.zeros((len(classes), 3), dtype=np.uint8)
    for item in classes:
        palette[item["id"]] = item["color"][::-1]  # RGB → BGR
    return palette[label_map]


def segment(image_path: str | Path, model_path: str | Path) -> dict[str, Any]:
    """单图语义分割；返回类别色图、叠加图、类别统计与耗时。"""
    started = time.monotonic()
    image = _read_image(image_path)
    height, width = image.shape[:2]
    blob, _scale, (pad_r, pad_c) = _preproc(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    session = _session(str(model_path))
    logits = session.run(None, {session.get_inputs()[0].name: blob})[0][0]
    half = logits.argmax(axis=0).astype(np.uint8)
    if pad_r or pad_c:
        half = half[: half.shape[0] - pad_r // 2, : half.shape[1] - pad_c // 2]
    label_map = cv2.resize(half, (width, height), interpolation=cv2.INTER_NEAREST)

    classes = class_table(model_path)
    color_map = _colorize(label_map, classes)
    overlay = cv2.addWeighted(image, 0.55, color_map, 0.45, 0)

    total = float(height * width)
    class_stats = []
    for item in classes:
        count = int((label_map == item["id"]).sum())
        class_stats.append(
            {
                "id": item["id"],
                "name": item["name"],
                "en": item["en"],
                "color": item["color"],
                "px": count,
                "ratio": round(count / total, 6) if total else 0.0,
            }
        )

    return {
        "color_map": color_map,
        "overlay": overlay,
        "width": width,
        "height": height,
        "classes": class_stats,
        "class_count": sum(1 for item in class_stats if item["px"] > 0),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
