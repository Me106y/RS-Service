from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from rs_service.interpret import service


@pytest.fixture()
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把解译根目录切到临时目录，避免读写真实 SIRIAgent 工作区。"""
    workspace = tmp_path / "workspace"
    images = workspace / "images"
    uploads = workspace / "uploads"
    demo = tmp_path / "demo" / "images"
    reports = tmp_path / "reports" / "interpret"
    for folder in (images, uploads, demo, reports):
        folder.mkdir(parents=True)
    monkeypatch.setattr(service, "SIRIAGENT_ROOT", tmp_path)
    monkeypatch.setattr(service, "WORKSPACE", workspace)
    monkeypatch.setattr(service, "IMAGES", images)
    monkeypatch.setattr(service, "UPLOADS", uploads)
    monkeypatch.setattr(service, "DEMO", demo)
    monkeypatch.setattr(service, "REPORT_ROOT", reports)
    monkeypatch.setattr(service, "ALLOWED_ROOTS", (workspace, tmp_path / "demo"))
    return tmp_path


def _write_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.zeros((64, 96, 3), dtype=np.uint8)
    image[16:48, 24:72] = (0, 200, 255)
    ok, buffer = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(buffer.tobytes())


def test_resolve_image_allows_demo(isolate: Path) -> None:
    image = isolate / "demo" / "images" / "object_detection" / "sample.png"
    _write_image(image)
    resolved = service._resolve_image("demo/images/object_detection/sample.png")
    assert resolved == image.resolve()


def test_resolve_image_rejects_outside_roots(isolate: Path) -> None:
    outside = isolate / "secret.png"
    _write_image(outside)
    with pytest.raises(PermissionError):
        service._resolve_image(str(outside))


def test_list_workspace_images_marks_demo(isolate: Path) -> None:
    _write_image(isolate / "workspace" / "images" / "building-change" / "image1.png")
    _write_image(isolate / "demo" / "images" / "object_detection" / "sample.png")
    items = service.list_workspace_images()
    kinds = {item["kind"] for item in items}
    assert kinds == {"purchase", "demo"}


@pytest.mark.skipif(
    not service.OBJECT_MODEL_PATH.is_file(),
    reason="YOLOX-S 权重未就绪（scripts/fetch_models.py yolox）",
)
def test_run_object_detection_writes_result(isolate: Path) -> None:
    image = isolate / "demo" / "images" / "object_detection" / "sample.png"
    _write_image(image)
    payload = service.run_object_detection("demo/images/object_detection/sample.png")
    assert payload["ok"] is True
    assert payload["count"] >= 0
    assert payload["accuracy_note"]
    annotated = service.SIRIAGENT_ROOT / payload["annotated_path"]
    assert annotated.is_file()


@pytest.mark.skipif(
    not service.SEMANTIC_MODEL_PATH.is_file(),
    reason="SparseMask 权重未就绪（scripts/fetch_models.py sparsemask）",
)
def test_run_semantic_segmentation_writes_result(isolate: Path) -> None:
    image = isolate / "demo" / "images" / "semantic_segmentation" / "sample.png"
    _write_image(image)
    payload = service.run_semantic_segmentation(
        "demo/images/semantic_segmentation/sample.png"
    )
    assert payload["ok"] is True
    assert payload["class_count"] >= 1
    assert len(payload["classes"]) == 8
    assert abs(sum(item["ratio"] for item in payload["classes"]) - 1.0) < 1e-3
    assert payload["accuracy_note"]
    color_map = service.SIRIAGENT_ROOT / payload["color_map_path"]
    overlay = service.SIRIAGENT_ROOT / payload["overlay_path"]
    assert color_map.is_file() and overlay.is_file()
