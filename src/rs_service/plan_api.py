"""5 项选星任务的 HTTP 层。

求解是 CPU/网络混合工作（传播、覆盖网格、Open-Meteo、CBC），不能放在请求线程里：
``POST /plans`` 只负责校验与入队，单 worker 线程按顺序执行，前端用 ``GET /plans/{id}``
轮询。任务状态保存在进程内，重启即丢；报告文件由后续 PR 落盘，不依赖任务状态。
"""

from __future__ import annotations

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
from .report import report_root, write_report

router = APIRouter(prefix="/api/v1", tags=["plans"])

TASKS: tuple[dict[str, Any], ...] = (
    {
        "operation": "task1",
        "supports_auto_expand": True,
        "label": "最少星达到覆盖率",
        "required": [],
        "advanced": ["area_target_pct", "fallback_area_target_pct"],
        "description": "给定面积覆盖目标（默认 90%），求最少需要几颗星；按“至少成像一次”判定。",
    },
    {
        "operation": "task2",
        "supports_auto_expand": False,
        "label": "最少星达到重访",
        "required": ["mrt_target_minutes"],
        "advanced": [],
        "description": "给定 P95 重访上限，求最少需要几颗星。",
    },
    {
        "operation": "task3",
        "supports_auto_expand": False,
        "label": "给定星数最大化达标面积",
        "required": ["n_budget", "temporal_coverage_target_pct"],
        "advanced": ["exact_budget", "area_target_pct"],
        "description": "星数固定，求能达到目标覆盖率的面积最大。",
    },
    {
        "operation": "task4",
        "supports_auto_expand": False,
        "label": "给定星数最小化面积分位 MRT",
        "required": ["n_budget"],
        "advanced": ["area_target_pct", "mrt_area_quantile_pct"],
        "description": "星数固定，求面积加权的分位 MRT 最小。",
    },
    {
        "operation": "task5",
        "supports_auto_expand": False,
        "label": "给定星数最小化 ART",
        "required": ["n_budget"],
        "advanced": [],
        "description": "星数固定，求面积加权平均重访时间最小（greedy）。",
    },
)

ADVANCED_FIELDS: tuple[dict[str, Any], ...] = (
    {"name": "area_target_pct", "label": "面积目标 %", "type": "number", "default": None},
    {"name": "fallback_area_target_pct", "label": "面积回退 %", "type": "number", "default": 90},
    {"name": "exact_budget", "label": "精确星数预算", "type": "boolean", "default": None},
    {"name": "grid_size_km", "label": "网格大小 km", "type": "number", "default": 5},
    {"name": "detection_step_s", "label": "检测步长 s", "type": "number", "default": 10},
    {"name": "selection_time_limit_s", "label": "选星时限 s", "type": "number", "default": 30},
)

DEFAULT_SENSOR_TYPES = ["optical", "hyperspectral", "sar"]

_REQUIRED: dict[str, tuple[str, ...]] = {
    item["operation"]: tuple(item["required"]) for item in TASKS
}


class PlanCreate(BaseModel):
    """前端提交的完整规划参数；字段与 5 个任务共用。"""

    operation: Literal[
        "task1", "task2", "task3", "task4", "task5"
    ]
    aoi_code: str | None = None
    aoi_wkt: str | None = None
    start_utc: datetime | None = None
    horizon_h: float = Field(default=72.0, gt=0, le=168.0)
    auto_expand: bool = False
    sensor_types: list[Literal["optical", "hyperspectral", "sar"]] = Field(
        default=DEFAULT_SENSOR_TYPES, min_length=1
    )
    max_resolution_m: float = Field(default=10.0, gt=0, le=10_000.0)
    pointing: Literal["nadir", "targeted"] = "nadir"
    grid_size_km: float = Field(default=5.0, gt=0, le=50.0)
    detection_step_s: int = Field(default=10, ge=1, le=3600)
    selection_time_limit_s: int = Field(default=30, ge=1, le=3600)
    temporal_coverage_target_pct: float | None = Field(default=None, gt=0, le=100)
    mrt_target_minutes: float | None = Field(default=None, ge=0)
    n_budget: int | None = Field(default=None, ge=1, le=500)
    area_target_pct: float | None = Field(default=None, gt=0, le=100)
    fallback_area_target_pct: float | None = Field(default=None, gt=0, le=100)
    allow_area_fallback: bool = True
    exact_budget: bool | None = None
    max_windows: int = Field(default=10, ge=1, le=100)
    max_weak_areas: int = Field(default=10, ge=1, le=100)
    cloud_filter: bool = True
    cloud_threshold_pct: float = Field(default=70.0, gt=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> PlanCreate:
        if bool(self.aoi_code) == bool(self.aoi_wkt):
            raise ValueError("aoi_code 与 aoi_wkt 必须二选一")
        missing = [name for name in _REQUIRED[self.operation] if getattr(self, name) is None]
        if missing:
            raise ValueError(f"缺少任务必填字段：{', '.join(missing)}")
        if self.auto_expand and self.operation != "task1":
            raise ValueError("只有 task1 支持自动扩窗")
        return self

    def to_spec(self) -> PlanSpec:
        start = self.start_utc or datetime.now(UTC)
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        area_target = self.area_target_pct
        if area_target is None:
            area_target = 90.0
        fallback_area_target = self.fallback_area_target_pct
        if fallback_area_target is None:
            # 旧引擎默认 fallback=90；严格目标低于 90 时会把 fallback 顶到严格目标之上。
            fallback_area_target = min(90.0, float(area_target))
        return PlanSpec(
            operation=self.operation,
            aoi_code=self.aoi_code,
            aoi_wkt=self.aoi_wkt,
            start_utc=start,
            horizon_h=self.horizon_h,
            auto_expand=self.auto_expand,
            sensor_types=tuple(self.sensor_types),
            max_resolution_m=self.max_resolution_m,
            pointing=self.pointing,
            grid_size_km=self.grid_size_km,
            detection_step_s=self.detection_step_s,
            selection_time_limit_s=self.selection_time_limit_s,
            # task1 按面积口径：至少成像一次即达标。旧客户端传的 90% 单格时间阈值
            # 在 72 h 内不可达，会把达标面积压成 0%，这里统一忽略。
            temporal_coverage_target_pct=(
                0.001 if self.operation == "task1" else self.temporal_coverage_target_pct
            ),
            mrt_target_minutes=self.mrt_target_minutes,
            n_budget=self.n_budget,
            area_target_pct=area_target,
            fallback_area_target_pct=fallback_area_target,
            allow_area_fallback=self.allow_area_fallback,
            exact_budget=self.exact_budget,
            max_windows=self.max_windows,
            max_weak_areas=self.max_weak_areas,
            cloud_filter=self.cloud_filter,
            cloud_threshold_pct=self.cloud_threshold_pct,
        )


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
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._trim()
        self._executor.submit(self._run, job_id, spec)
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def _trim(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        for key in list(self._jobs)[: len(self._jobs) - self._max_jobs]:
            self._jobs.pop(key, None)

    def _run(self, job_id: str, spec: PlanSpec) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
        try:
            result = run_plan(spec)
            with self._lock:
                job["status"] = "completed"
                job["result"] = result
        except PlanError as exc:
            with self._lock:
                job["status"] = "failed"
                job["error"] = {"code": exc.code, "message": exc.message, **exc.details}
        except Exception as exc:  # noqa: BLE001 - 任务线程必须把失败写回任务
            with self._lock:
                job["status"] = "failed"
                job["error"] = {"code": "execution_error", "message": str(exc)}


JOBS = JobStore()


@router.get("/plan-options")
def plan_options() -> dict[str, Any]:
    """向导渲染所需的全部静态选项与当前数据能力。"""

    with session_scope() as session:
        aois = session.scalars(select(Aoi).order_by(Aoi.level, Aoi.code)).all()
        candidates, blocked = collect_candidates(session, tuple(DEFAULT_SENSOR_TYPES), 10_000)
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
        "sensor_types": [
            {"value": "optical", "label": "光学"},
            {"value": "hyperspectral", "label": "高光谱"},
            {"value": "sar", "label": "SAR"},
        ],
        "defaults": {
            "horizon_h": 72,
            "auto_expand": False,
            "sensor_types": DEFAULT_SENSOR_TYPES,
            "max_resolution_m": 10,
            "pointing": "nadir",
            "grid_size_km": 5,
            "detection_step_s": 10,
            "selection_time_limit_s": 30,
            "allow_area_fallback": True,
            "max_windows": 10,
            "max_weak_areas": 10,
            "cloud_filter": True,
            "cloud_threshold_pct": 70,
        },
        "aoi": [
            {"code": item.code, "name": item.name, "level": item.level, "adcode": item.adcode}
            for item in aois
        ],
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
    path = report_root() / job_id / "report.html"
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "report_not_found", "job_id": job_id},
        )
    return FileResponse(path)


__all__ = ["JOBS", "PlanCreate", "router"]
