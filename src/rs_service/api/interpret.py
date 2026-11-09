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
