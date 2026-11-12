"""解译服务：图像清单、路径校验、结果落盘（变化检测 / 目标检测 / 语义分割 / 图像处理）。"""

from __future__ import annotations

import json
import logging
import os
import secrets
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import infer, object_detect, semantic_seg

log = logging.getLogger("rs_service.interpret")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SIRIAGENT_ROOT = PROJECT_ROOT.parent / "SIRIAgent"
SIRIAGENT_ROOT = Path(
    os.environ.get("SIRIAGENT_ROOT", str(DEFAULT_SIRIAGENT_ROOT))
).resolve()
WORKSPACE = SIRIAGENT_ROOT / "workspace"
IMAGES = WORKSPACE / "images"
UPLOADS = WORKSPACE / "uploads"
DEMO = SIRIAGENT_ROOT / "demo" / "images"
REPORT_ROOT = SIRIAGENT_ROOT / "reports" / "interpret"
MODEL_PATH = PROJECT_ROOT / "data" / "models" / "change_detection" / "best_model.pth"
OBJECT_MODEL_PATH = PROJECT_ROOT / "data" / "models" / "object_detection" / "yolox_s.pth"
SEMANTIC_MODEL_PATH = (
    PROJECT_ROOT / "data" / "models" / "semantic_segmentation" / "sparsemask_openearthmap.onnx"
)
ALLOWED_ROOTS = (WORKSPACE, DEMO)
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
ACCURACY_NOTE = (
    "变化检测为 FlickCD 模型估计（256 滑窗、阈值 0.5），结果需人工复核；"
    "输入限制最大边 2048。"
)
OBJECT_ACCURACY_NOTE = (
    "目标检测为 YOLOX-S（COCO 80 类预训练）估计，置信度阈值 0.25 / NMS 0.45，"
    "结果需人工复核；输入限制最大边 2048。"
)
SEMANTIC_ACCURACY_NOTE = (
    "语义分割为 SparseMask（OpenEarthMap 8 类预训练）估计，逐像素类别需人工复核；"
    "输入等比缩到长边 1024 后推理，再最近邻上采样回原尺寸。"
)
BJ = timezone(timedelta(hours=8))

_device: str | None = None


def device() -> str:
    global _device
    if _device is None:
        override = os.environ.get("RS_INTERPRET_DEVICE", "").strip()
        if override:
            _device = override
        else:
            import torch

            _device = "cuda" if torch.cuda.is_available() else "cpu"
    return _device


def warmup_safe() -> None:
    try:
        infer.warmup(str(MODEL_PATH), device())
        log.info("interpret model warmup ok device=%s", device())
    except Exception as exc:  # noqa: BLE001 - 预加载失败不阻塞星历服务
        log.warning("interpret model warmup failed: %s", exc)
    try:
        if OBJECT_MODEL_PATH.is_file():
            object_detect.warmup(str(OBJECT_MODEL_PATH), device())
            log.info("object detection model warmup ok")
    except Exception as exc:  # noqa: BLE001
        log.warning("object detection warmup failed: %s", exc)
    try:
        if SEMANTIC_MODEL_PATH.is_file():
            semantic_seg.warmup(str(SEMANTIC_MODEL_PATH))
            log.info("semantic segmentation model warmup ok")
    except Exception as exc:  # noqa: BLE001
        log.warning("semantic segmentation warmup failed: %s", exc)


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def _relative(path: Path) -> str:
    return path.relative_to(SIRIAGENT_ROOT).as_posix()


def list_workspace_images() -> list[dict[str, Any]]:
    """列出购买、上传与示例图；只给元数据，不返回二进制。"""
    items: list[dict[str, Any]] = []
    for base, kind in ((IMAGES, "purchase"), (UPLOADS, "upload"), (DEMO, "demo")):
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if not folder.is_dir():
                continue
            for file in sorted(folder.iterdir()):
                if not file.is_file() or file.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                entry: dict[str, Any] = {
                    "kind": kind,
                    "group": folder.name,
                    "name": file.name,
                    "path": _relative(file),
                    "bytes": file.stat().st_size,
                }
                size = _png_size(file)
                if size is not None:
                    entry["width"], entry["height"] = size
                items.append(entry)
    return items


def _resolve_image(raw: str) -> Path:
    if not raw or not raw.strip():
        raise ValueError("图像路径不能为空。")
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        candidate = SIRIAGENT_ROOT / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError(f"图像不存在：{raw}") from exc
    if not any(resolved.is_relative_to(root) for root in ALLOWED_ROOTS):
        raise PermissionError("只允许读取 workspace/ 或 demo/ 下的图像。")
    if not resolved.is_file() or resolved.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("只支持 PNG / JPG 图像。")
    return resolved


def _new_run_id() -> tuple[str, Path]:
    run_id = f"int-{datetime.now(BJ):%Y%m%d%H%M%S}-{secrets.token_hex(3)}"
    output_dir = REPORT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    return run_id, output_dir


def run_object_detection(image_path: str) -> dict[str, Any]:
    """同步执行一次目标检测，结果写到 SIRIAgent 的 reports/interpret/。"""
    image = _resolve_image(image_path)
    if not OBJECT_MODEL_PATH.is_file():
        raise FileNotFoundError(f"目标检测权重不存在：{OBJECT_MODEL_PATH}")
    run_id, output_dir = _new_run_id()
    result = object_detect.detect(
        image,
        OBJECT_MODEL_PATH,
        device=device(),
        max_side=object_detect.MAX_SIDE,
    )
    annotated_path = output_dir / "object_annotated.png"
    object_detect.write_png(annotated_path, result.pop("annotated"))
    payload: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "image_path": _relative(image),
        "annotated_path": _relative(annotated_path),
        "width": result["width"],
        "height": result["height"],
        "count": result["count"],
        "class_count": result["class_count"],
        "classes": result["classes"],
        "score_mean": result["score_mean"],
        "score_min": result["score_min"],
        "detections": result["detections"],
        "resized": result["resized"],
        "elapsed_ms": result["elapsed_ms"],
        "device": result["device"],
        "accuracy_note": OBJECT_ACCURACY_NOTE,
    }
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def run_semantic_segmentation(image_path: str) -> dict[str, Any]:
    """同步执行一次语义分割，结果写到 SIRIAgent 的 reports/interpret/。"""
    image = _resolve_image(image_path)
    if not SEMANTIC_MODEL_PATH.is_file():
        raise FileNotFoundError(f"语义分割权重不存在：{SEMANTIC_MODEL_PATH}")
    run_id, output_dir = _new_run_id()
    result = semantic_seg.segment(image, SEMANTIC_MODEL_PATH)
    color_path = output_dir / "semantic_seg_map.png"
    overlay_path = output_dir / "semantic_seg_overlay.png"
    semantic_seg.write_png(color_path, result.pop("color_map"))
    semantic_seg.write_png(overlay_path, result.pop("overlay"))
    payload: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "image_path": _relative(image),
        "color_map_path": _relative(color_path),
        "overlay_path": _relative(overlay_path),
        "width": result["width"],
        "height": result["height"],
        "classes": result["classes"],
        "class_count": result["class_count"],
        "elapsed_ms": result["elapsed_ms"],
        "accuracy_note": SEMANTIC_ACCURACY_NOTE,
    }
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def run_change_detection(before_path: str, after_path: str) -> dict[str, Any]:
    """同步执行一次变化检测，结果写到 SIRIAgent 的 reports/interpret/。"""
    before = _resolve_image(before_path)
    after = _resolve_image(after_path)
    run_id, output_dir = _new_run_id()

    result = infer.detect(
        before,
        after,
        MODEL_PATH,
        device=device(),
        max_side=infer.MAX_SIDE,
        time_budget_s=55.0,
    )
    mask_path = output_dir / "mask.png"
    infer.write_png(mask_path, result.pop("mask"))

    payload: dict[str, Any] = {
        "ok": True,
        "run_id": run_id,
        "mask_path": _relative(mask_path),
        "before_path": _relative(before),
        "after_path": _relative(after),
        "width": result["width"],
        "height": result["height"],
        "changed_px": result["changed_px"],
        "changed_ratio": result["changed_ratio"],
        "components": result["components"],
        "elapsed_ms": result["elapsed_ms"],
        "resized": result["resized"],
        "device": result["device"],
        "accuracy_note": ACCURACY_NOTE,
    }
    (output_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload
