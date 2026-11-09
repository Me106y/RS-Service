"""FlickCD 变化检测推理。

代码 vendored 自 `SIRIGeo/backend/flickcd*`（同一作者提供），只保留推理需要的
网络定义、归一化、256 滑窗与均值融合；去掉了原项目的 global_path/imio/GDAL
依赖，读写改走 OpenCV，输出路径由调用方决定。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from .flickcd import FlickCD

WINDOW = 256
STRIDE = 128  # 50% 重叠，重叠区取均值
MAX_SIDE = 2048
THRESHOLD = 0.5
NOISE_AREA_PX = 50
# RGB，与训练一致（注意不是六通道拼接）。
MEAN = np.array([0.406, 0.456, 0.485], dtype=np.float32)
STD = np.array([0.225, 0.224, 0.229], dtype=np.float32)

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


def _positions(length: int) -> list[int]:
    if length <= WINDOW:
        return [0]
    starts = list(range(0, length - WINDOW + 1, STRIDE))
    if starts[-1] != length - WINDOW:
        starts.append(length - WINDOW)
    return starts


def _preprocess(image: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return (rgb - MEAN) / STD


def _load_model(model_path: str, device: str) -> torch.nn.Module:
    key = (model_path, device)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(model_path)
    if not path.is_file():
        raise FileNotFoundError(f"模型文件不存在：{model_path}")
    model = FlickCD((4, 4, 8), (4, 4, 8), load_pretrained=False)
    state = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and isinstance(state.get("model"), dict):
        state = state["model"]
    model.load_state_dict(state, strict=True)
    model.to(device).eval()
    _CACHE[key] = model
    return model


def warmup(model_path: str, device: str) -> None:
    _load_model(model_path, device)


@torch.no_grad()
def detect(
    before_path: str | Path,
    after_path: str | Path,
    model_path: str | Path,
    *,
    device: str = "cpu",
    max_side: int = MAX_SIDE,
    time_budget_s: float = 55.0,
) -> dict[str, Any]:
    """双时相变化检测；返回 mask、统计与耗时。超时抛 TimeoutError。"""
    started = time.monotonic()
    before = _read_image(before_path)
    after = _read_image(after_path)
    before, resized_before = _limit_side(before, max_side)
    after, resized_after = _limit_side(after, max_side)
    height, width = before.shape[:2]
    if after.shape[:2] != (height, width):
        after = cv2.resize(after, (width, height), interpolation=cv2.INTER_LINEAR)

    x1 = _preprocess(before)
    x2 = _preprocess(after)
    pad_h = max(0, WINDOW - height)
    pad_w = max(0, WINDOW - width)
    if pad_h or pad_w:
        x1 = cv2.copyMakeBorder(x1, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT_101)
        x2 = cv2.copyMakeBorder(x2, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT_101)
    padded_h, padded_w = x1.shape[:2]

    model = _load_model(str(model_path), device)
    accumulator = np.zeros((padded_h, padded_w), dtype=np.float32)
    counter = np.zeros((padded_h, padded_w), dtype=np.float32)
    coords = [(y, x) for y in _positions(padded_h) for x in _positions(padded_w)]
    batch_size = 16 if device == "cuda" else 4

    for start in range(0, len(coords), batch_size):
        if time.monotonic() - started > time_budget_s:
            raise TimeoutError("变化检测超时（>55s），请缩小图像或稍后重试。")
        chunk = coords[start : start + batch_size]
        patch1 = np.stack(
            [x1[y : y + WINDOW, x : x + WINDOW] for y, x in chunk]
        ).transpose(0, 3, 1, 2)
        patch2 = np.stack(
            [x2[y : y + WINDOW, x : x + WINDOW] for y, x in chunk]
        ).transpose(0, 3, 1, 2)
        t1 = torch.from_numpy(np.ascontiguousarray(patch1)).to(device)
        t2 = torch.from_numpy(np.ascontiguousarray(patch2)).to(device)
        mask1, _, _ = model(t1, t2)
        probabilities = mask1[:, 0].detach().cpu().numpy()
        for index, (y, x) in enumerate(chunk):
            accumulator[y : y + WINDOW, x : x + WINDOW] += probabilities[index]
            counter[y : y + WINDOW, x : x + WINDOW] += 1.0

    probability = (accumulator / np.maximum(counter, 1.0))[:height, :width]
    changed = probability > THRESHOLD
    mask = (changed.astype(np.uint8)) * 255
    components = 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(
        changed.astype(np.uint8), connectivity=8
    )
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= NOISE_AREA_PX:
            components += 1

    changed_px = int(changed.sum())
    return {
        "mask": mask,
        "probability": probability,
        "width": width,
        "height": height,
        "changed_px": changed_px,
        "changed_ratio": changed_px / float(height * width) if height and width else 0.0,
        "components": components,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "resized": resized_before or resized_after,
        "device": device,
    }
