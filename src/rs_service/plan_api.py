"""5 项选星任务的 HTTP 层。

求解是 CPU/网络混合工作（传播、覆盖网格、Open-Meteo、CBC），不能放在请求线程里：
``POST /plans`` 只负责校验与入队，单 worker 线程按顺序执行，前端用 ``GET /plans/{id}``
轮询。任务状态保存在进程内，重启即丢；报告文件由后续 PR 落盘，不依赖任务状态。
"""

from __future__ import annotations

import copy
import threading
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from .coverage_engine.adapter import (
    PlanError,
    PlanSpec,
    collect_candidates,
    run_plan,
    tle_age_hours,
)
from .db.models import Aoi, TleCurrent, TleRecord
from .db.session import session_scope
from .report import find_run_dir, list_reports, report_root, write_report

router = APIRouter(prefix="/api/v1", tags=["plans"])

TASKS: tuple[dict[str, Any], ...] = (
    {
        "operation": "revisit",
        "supports_auto_expand": False,
        "label": "区域重访",
        "required": ["revisit_hours"],
        "advanced": [],
        "description": "给定区域与重访时间 T，求最少卫星；窗口自动取 min(3T, 168h)，面积目标固定 100%。",
    },
)

ADVANCED_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "grid_size_km", "label": "网格大小 km", "type": "number", "default": 5},
    {"name": "detection_step_s", "label": "检测步长 s", "type": "number", "default": 10},
)

DEFAULT_SENSOR_TYPES = ["optical"]

_REQUIRED: dict[str, tuple[str, ...]] = {
    item["operation"]: tuple(item["required"]) for item in TASKS
}


class PlanCreate(BaseModel):
    """v2.0.0 唯一场景：区域重访。窗口、面积目标、指向都由服务端固定。"""

    operation: Literal["revisit"]
    aoi_code: str | None = None
    aoi_wkt: str | None = None
    start_utc: datetime | None = None
    revisit_hours: Literal[24, 48, 72] = 24
    sensor_types: list[Literal["optical"]] = Field(default=DEFAULT_SENSOR_TYPES, min_length=1)
    max_resolution_m: float = Field(default=10.0, gt=0, le=10.0)
    cloud_filter: bool = False
    cloud_threshold_pct: float = Field(default=70.0, gt=0, le=100)
    max_windows: int = Field(default=10, ge=1, le=100)
    max_weak_areas: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _check(self) -> PlanCreate:
        if bool(self.aoi_code) == bool(self.aoi_wkt):
            raise ValueError("aoi_code 与 aoi_wkt 必须二选一")
        if self.sensor_types != ["optical"]:
            raise ValueError("revisit 目前只支持光学载荷")
        return self

    def to_spec(self) -> PlanSpec:
        start = self.start_utc or datetime.now(UTC)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        horizon = min(3.0 * float(self.revisit_hours), 168.0)
        return PlanSpec(
            operation="revisit",
            aoi_code=self.aoi_code,
            aoi_wkt=self.aoi_wkt,
            start_utc=start,
            horizon_h=horizon,
            auto_expand=False,
            sensor_types=tuple(self.sensor_types),
            max_resolution_m=self.max_resolution_m,
            pointing="nadir",
            grid_size_km=5.0,
            detection_step_s=10,
            decision_step_s=60,
            selection_time_limit_s=30,
            greedy_time_limit_s=60,
            temporal_coverage_target_pct=None,
            mrt_target_minutes=float(self.revisit_hours) * 60.0,
            n_budget=None,
            area_target_pct=100.0,
            fallback_area_target_pct=100.0,
            allow_area_fallback=False,
            exact_budget=None,
            max_windows=self.max_windows,
            max_weak_areas=self.max_weak_areas,
            cloud_filter=self.cloud_filter,
            cloud_threshold_pct=self.cloud_threshold_pct,
        )


_STAGE_BASE = {
    "prepare": 0.0,
    "grid": 1.0,
    "opportunities": 2.0,
    "cloud": 67.0,
    "selection": 72.0,
    "metrics": 97.0,
    "playback": 99.0,
    "done": 100.0,
}
_STAGE_WIDTH = {"opportunities": 65.0, "selection": 25.0}


def _progress_pct(stage: str, current: int | None, total: int | None) -> float:
    """把阶段内的 current/total 折算成 0–100 的总进度。"""

    if stage == "done":
        return 100.0
    if stage == "failed":
        return 0.0
    base = _STAGE_BASE.get(stage, 0.0)
    width = _STAGE_WIDTH.get(stage)
    if width is not None and total:
        fraction = max(0.0, min(1.0, float(current or 0) / float(total)))
        return min(99.0, base + width * fraction)
    if stage == "cloud":
        return 67.0 if current is None else 72.0
    return base


class JobStore:
    """单进程内的任务表；只保留最近 ``max_jobs`` 条。"""

    def __init__(self, max_jobs: int = 50) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plan")
        self._max_jobs = max_jobs

    def create(self, spec: PlanSpec) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "progress": {
                "stage": "queued",
                "label": "排队中",
                "current": None,
                "total": None,
                "pct": 0.0,
                "elapsed_s": 0.0,
                "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            },
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._trim()
        self._executor.submit(self._run, job_id, spec)
        return copy.deepcopy(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def _trim(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        for key in list(self._jobs)[: len(self._jobs) - self._max_jobs]:
            self._jobs.pop(key, None)

    def _set_progress(
        self,
        job_id: str,
        stage: str,
        label: str,
        current: int | None,
        total: int | None,
    ) -> None:
        now = datetime.now(UTC)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
            elapsed = max(0.0, (now - created).total_seconds())
            previous = (job.get("progress") or {}).get("pct", 0.0)
            pct = float(previous) if stage == "failed" else _progress_pct(stage, current, total)
            job["progress"] = {
                "stage": stage,
                "label": label,
                "current": current,
                "total": total,
                "pct": round(min(100.0, max(0.0, pct)), 1),
                "elapsed_s": round(elapsed, 1),
                "updated_at": now.isoformat().replace("+00:00", "Z"),
            }

    def _run(self, job_id: str, spec: PlanSpec) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
        self._set_progress(job_id, "prepare", "读取卫星与 TLE", None, None)
        try:
            result = run_plan(
                spec,
                progress=lambda stage, label, current, total: self._set_progress(
                    job_id, stage, label, current, total
                ),
            )
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                job["status"] = "completed"
                job["result"] = result
            self._set_progress(job_id, "done", "完成", None, None)
        except PlanError as exc:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = {"code": exc.code, "message": exc.message, **exc.details}
            self._set_progress(job_id, "failed", "失败", None, None)
        except Exception as exc:  # noqa: BLE001 - 任务线程必须把失败写回任务
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = {"code": "execution_error", "message": str(exc)}
            self._set_progress(job_id, "failed", "失败", None, None)


JOBS = JobStore()


@router.get("/plan-options")
def plan_options() -> dict[str, Any]:
    """向导渲染所需的全部静态选项与当前数据能力。"""

    with session_scope() as session:
        aois = session.scalars(
            select(Aoi).where(Aoi.level.in_(("city", "district"))).order_by(Aoi.level, Aoi.code)
        ).all()
        candidates, blocked = collect_candidates(session, tuple(DEFAULT_SENSOR_TYPES), 10.0)
        latest_epoch = session.scalar(
            select(func.max(TleRecord.epoch_utc)).join(
                TleCurrent, TleCurrent.tle_id == TleRecord.id
            )
        )
        oldest_epoch = session.scalar(
            select(func.min(TleRecord.epoch_utc)).join(
                TleCurrent, TleCurrent.tle_id == TleRecord.id
            )
        )
    reasons = Counter(item["reason"] for item in blocked)
    age = tle_age_hours(oldest_epoch) if oldest_epoch else None
    tle = {
        "latest_epoch_utc": latest_epoch,
        "oldest_epoch_utc": oldest_epoch,
        "max_age_hours": round(age, 1) if age is not None else None,
        "degraded": age is not None and age > 72.0,
        "stale": age is not None and age > 168.0 and not candidates,
    }
    return {
        "ok": True,
        "tasks": list(TASKS),
        "advanced": list(ADVANCED_FIELDS),
        "revisit": {
            "hours": [24, 48, 72],
            "default_hours": 24,
            "window_rule": "min(3T, 168)",
            "area_target_pct": 100,
            "cloud_filter_default": False,
        },
        "sensor_types": [
            {"value": "optical", "label": "光学"},
        ],
        "defaults": {
            "sensor_types": DEFAULT_SENSOR_TYPES,
            "max_resolution_m": 10,
            "grid_size_km": 5,
            "detection_step_s": 10,
            "max_windows": 10,
            "max_weak_areas": 10,
            "cloud_filter": False,
            "cloud_threshold_pct": 70,
        },
        "aoi": [
            {"code": item.code, "name": item.name, "level": item.level, "adcode": item.adcode}
            for item in aois
        ],
        "aoi_limits": {
            "max_area_km2": 20_000,
            "levels": ["city", "district", "custom"],
        },
        "capability": {
            "plannable": len(candidates),
            "blocked": len(blocked),
            "blocked_by_reason": dict(reasons),
        },
        "tle": tle,
    }


@router.post("/plans", status_code=status.HTTP_202_ACCEPTED)
def create_plan(payload: PlanCreate) -> dict[str, Any]:
    job = JOBS.create(payload.to_spec())
    return {
        "ok": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "status_url": f"/api/v1/plans/{job['job_id']}",
    }


@router.get("/plans/history")
def plan_history(operation: str | None = None, limit: int = 200) -> dict[str, Any]:
    """历史报告列表。只读 ``meta.json``，按生成时间倒序。

    路径要在 ``/plans/{job_id}`` 之前声明，否则 ``history`` 会被当成 job_id。
    """

    return {
        "ok": True,
        "root": str(report_root()),
        **list_reports(report_root(), operation=operation, limit=limit),
    }


@router.get("/plans/{job_id}")
def get_plan(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "plan_not_found", "job_id": job_id},
        )
    return {"ok": True, **job}


@router.post("/plans/{job_id}/report")
def create_report(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "plan_not_found", "job_id": job_id},
        )
    if job["status"] != "completed" or not job.get("result"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "plan_not_completed", "job_id": job_id, "status": job["status"]},
        )
    info = write_report(job["result"], job_id)
    return {"ok": True, **info, "report_url": f"/api/v1/plans/{job_id}/report"}


@router.get("/plans/{job_id}/report")
def get_report(job_id: str) -> FileResponse:
    run_dir = find_run_dir(report_root(), job_id)
    path = run_dir / "report.html" if run_dir else None
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_not_found", "job_id": job_id},
        )
    return FileResponse(path)


__all__ = ["JOBS", "PlanCreate", "router"]
