"""图像处理三算子（Phase 3 M4，口径对齐 SIRIGeo v2.11）。

在册算子：
    grayscale  灰度图      —— BGR → 单通道灰度（带 alpha 铺白底）
    canny      边缘检测    —— 5×5 高斯平滑后 Canny 双阈值（soft/normal/sharp）
    denoise    降噪        —— 轻度高斯 / 中度中值 / 强 NL-Means

与 Qt 无关的纯处理层：输入图像路径 + 算子 + 档位 → 结果图数组与元数据。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

OPERATORS = (
    {"id": "grayscale", "name": "灰度图", "desc": "转为单通道灰度影像", "levels": []},
    {
        "id": "canny",
        "name": "边缘检测（Canny）",
        "desc": "检测影像边缘，输出黑白线稿",
        "levels": ["soft", "normal", "sharp"],
    },
    {
        "id": "denoise",
        "name": "降噪",
        "desc": "平滑噪点，保留主体结构",
        "levels": ["light", "medium", "strong"],
    },
)
OPERATOR_NAMES = {item["id"]: item["name"] for item in OPERATORS}

DENOISE_LEVELS = (
    ("light", "轻度（高斯 3×3）"),
    ("medium", "中度（中值 5×5）"),
    ("strong", "强（NL-Means）"),
)
CANNY_LEVELS = (
    ("soft", "柔和（30 / 90）", 30, 90),
    ("normal", "标准（50 / 150）", 50, 150),
    ("sharp", "锐利（80 / 200）", 80, 200),
)
DEFAULT_CANNY = "normal"
DEFAULT_DENOISE = "light"

DISPLAY_NAMES = {"grayscale": "灰度", "canny": "边缘", "denoise": "降噪"}


def _read(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"图像读取失败：{path}")
    return image


def write_png(path: str | Path, array: np.ndarray) -> None:
    ok, buffer = cv2.imencode(".png", array)
    if not ok:
        raise ValueError(f"图像编码失败：{path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buffer.tofile(str(path))


def _to_bgr(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.shape[2] == 1:
        return cv2.cvtColor(image[:, :, 0], cv2.COLOR_GRAY2BGR)
    return image


def _flatten_alpha(image: np.ndarray) -> np.ndarray:
    """带 alpha 的影像铺白底（避免透明区变黑）。"""
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3:4].astype(np.float32) / 255.0
        rgb = image[:, :, :3].astype(np.float32)
        white = np.full_like(rgb, 255.0)
        return (rgb * alpha + white * (1.0 - alpha)).astype(np.uint8)
    return image


def to_grayscale(image: np.ndarray) -> np.ndarray:
    bgr = _to_bgr(_flatten_alpha(image))
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def canny_edges(image: np.ndarray, level: str = DEFAULT_CANNY) -> np.ndarray:
    thresholds = {key: (lo, hi) for key, _label, lo, hi in CANNY_LEVELS}
    lo, hi = thresholds.get(level, thresholds[DEFAULT_CANNY])
    bgr = _to_bgr(_flatten_alpha(image))
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    return cv2.Canny(blurred, lo, hi)


def denoise(image: np.ndarray, level: str = DEFAULT_DENOISE) -> np.ndarray:
    bgr = _to_bgr(_flatten_alpha(image))
    if level == "medium":
        return cv2.medianBlur(bgr, 5)
    if level == "strong":
        if bgr.ndim == 3:
            return cv2.fastNlMeansDenoisingColored(bgr, None, 10, 10, 7, 21)
        return cv2.fastNlMeansDenoising(bgr, None, 10, 7, 21)
    return cv2.GaussianBlur(bgr, (3, 3), 0)


def process(
    image_path: str | Path,
    operator: str,
    level: str | None = None,
) -> dict[str, Any]:
    """执行一个算子，返回结果图数组、算子/档位与耗时；非法算子抛 ValueError。"""
    if operator not in OPERATOR_NAMES:
        raise ValueError(
            f"未知处理算子：{operator}（在册：{'、'.join(OPERATOR_NAMES)}）"
        )
    started = time.monotonic()
    image = _read(image_path)

    level_name = ""
    if operator == "grayscale":
        result = to_grayscale(image)
        level = None
    elif operator == "canny":
        level = level or DEFAULT_CANNY
        level_name = {key: label for key, label, _lo, _hi in CANNY_LEVELS}.get(
            level, level
        )
        result = canny_edges(image, level)
    else:
        level = level or DEFAULT_DENOISE
        level_name = dict(DENOISE_LEVELS).get(level, level)
        result = denoise(image, level)

    height, width = result.shape[:2]
    return {
        "output": result,
        "operator": operator,
        "operator_name": OPERATOR_NAMES[operator],
        "level": level,
        "level_name": level_name,
        "detail": next(item["desc"] for item in OPERATORS if item["id"] == operator),
        "width": width,
        "height": height,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
