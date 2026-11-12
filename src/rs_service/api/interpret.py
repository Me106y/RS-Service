"""遥感解译 REST 端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..interpret import service

router = APIRouter(prefix="/api/v1/interpret", tags=["interpret"])


class ChangeDetectionRequest(BaseModel):
    before_path: str
    after_path: str


class ObjectDetectionRequest(BaseModel):
    image_path: str


class SemanticSegmentationRequest(BaseModel):
    image_path: str


class ImageProcessRequest(BaseModel):
    image_path: str
    operator: str
    level: str | None = None


@router.get("/images")
def list_images() -> dict[str, Any]:
    return {
        "ok": True,
        "items": service.list_workspace_images(),
        "accuracy_note": service.ACCURACY_NOTE,
    }


@router.post("/change-detection")
def change_detection(body: ChangeDetectionRequest) -> dict[str, Any]:
    try:
        return service.run_change_detection(body.before_path, body.after_path)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/semantic-segmentation")
def semantic_segmentation(body: SemanticSegmentationRequest) -> dict[str, Any]:
    try:
        return service.run_semantic_segmentation(body.image_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/object-detection")
def object_detection(body: ObjectDetectionRequest) -> dict[str, Any]:
    try:
        return service.run_object_detection(body.image_path)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
