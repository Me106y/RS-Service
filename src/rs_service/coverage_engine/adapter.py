"""RS-Service 数据 → 区域覆盖引擎的适配层。

引擎（``coverage_engine/vendor``）吃的是项目目录里的 TLE、GeoJSON 边界和
逐星传感器 profile。这里把数据库里的 ``satellite`` / ``sensor`` /
``tle_current`` / ``aoi`` 写成一次性的临时工作目录，再调用引擎入口。

数据口径（与参考后端严格对齐）：

- 分辨率从 ``sensor.raw_spec`` 解析，幅宽取 ``sensor.swath_km``；缺一项就
  不参与选星，并给出原因。
- 沿轨半角固定 60°，这是参考后端自己的场景假设，不是载荷实测值。
- SAR 需要原生入射角带；数据库没有，因此按参考后端的规则阻断，不退回默认带。
- 微波载荷不是成像载荷，直接排除。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely import wkt as shapely_wkt
from shapely.geometry import mapping, shape
from siriagent_skill.runner import run_skill  # noqa: E402
from sqlalchemy import select
from sqlalchemy.orm import Session

from rs_service.db.models import Aoi, Satellite, Sensor, TleCurrent, TleRecord
from rs_service.db.session import session_scope

# 触发 vendor 的 sys.path 注入，必须在导入 siriagent_skill 之前。
from . import VENDOR_DIR  # noqa: F401

ALONG_TRACK_HALF_ANGLE_DEG = 60.0
_UI_SENSOR_TO_CLASS = {
    "optical": "可见光",
    "hyperspectral": "高光谱",
    "sar": "SAR",
}
_RESOLUTION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m\b")


class PlanError(ValueError):
    """可直接映射成 API 错误码的规划错误。"""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class PlanSpec:
    operation: str
    aoi_code: str | None = None
    aoi_wkt: str | None = None
    start_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
    horizon_h: float = 72.0
    sensor_types: tuple[str, ...] = ("optical", "hyperspectral", "sar")
    max_resolution_m: float = 10.0
    pointing: str = "nadir"
    grid_size_km: float = 5.0
    detection_step_s: int = 10
    decision_step_s: int = 60
    min_simultaneous: int = 1
    method: str | None = None
    selection_time_limit_s: int = 30
    temporal_coverage_target_pct: float | None = None
    mrt_target_minutes: float | None = None
    n_budget: int | None = None
    area_target_pct: float | None = None
    fallback_area_target_pct: float | None = None
    allow_area_fallback: bool = True
    exact_budget: bool | None = None
    mrt_area_quantile_pct: float = 90.0
    max_windows: int = 10
    max_weak_areas: int = 10


@dataclass(frozen=True)
class Candidate:
    norad_id: int
    name: str
    sensor_class: str
    resolution_m: float
    swath_km: float
    omm: dict[str, Any]


def _parse_resolution_m(raw_spec: str | None) -> float | None:
    if not raw_spec:
        return None
    match = _RESOLUTION_RE.search(raw_spec)
    return float(match.group(1)) if match else None


def _blocked(norad_id: int, name: str, sensor_type: str, reason: str) -> dict[str, Any]:
    return {
        "norad_id": int(norad_id),
        "name": name,
        "sensor_type": sensor_type,
        "reason": reason,
    }


def collect_candidates(
    session: Session,
    sensor_types: Iterable[str],
    max_resolution_m: float = 10.0,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """把数据库中可参与选星的卫星整理成引擎候选。"""

    wanted = {_UI_SENSOR_TO_CLASS[item] for item in sensor_types if item in _UI_SENSOR_TO_CLASS}
    rows = session.execute(
        select(Satellite, Sensor, TleRecord)
        .join(Sensor, Sensor.sat_id == Satellite.sat_id)
        .join(TleCurrent, TleCurrent.sat_id == Satellite.sat_id)
        .join(TleRecord, TleRecord.id == TleCurrent.tle_id)
    ).all()

    candidates: list[Candidate] = []
    blocked: list[dict[str, Any]] = []
    for sat, sensor, tle in rows:
        sensor_type = (sensor.sensor_type or "").strip()
        sensor_class = _UI_SENSOR_TO_CLASS.get(sensor_type)
        if sensor_class is None:
            blocked.append(_blocked(sat.sat_id, sat.name, sensor_type, "非成像载荷，不参与选星"))
            continue
        if sensor_class not in wanted:
            continue
        if not tle.omm_json:
            blocked.append(_blocked(sat.sat_id, sat.name, sensor_type, "缺少 OMM/TLE"))
            continue
        if sensor_class == "SAR":
            blocked.append(
                _blocked(
                    sat.sat_id,
                    sat.name,
                    sensor_type,
                    "SAR 缺少原生入射角带；按参考引擎口径阻断，不退回默认带",
                )
            )
            continue
        swath_km = float(sensor.swath_km) if sensor.swath_km else None
        if not swath_km:
            blocked.append(_blocked(sat.sat_id, sat.name, sensor_type, "缺少幅宽"))
            continue
        resolution_m = _parse_resolution_m(sensor.raw_spec)
        if resolution_m is None:
            blocked.append(_blocked(sat.sat_id, sat.name, sensor_type, "缺少分辨率"))
            continue
        if resolution_m > max_resolution_m:
            continue
        candidates.append(
            Candidate(
                norad_id=int(sat.sat_id),
                name=sat.name,
                sensor_class=sensor_class,
                resolution_m=resolution_m,
                swath_km=swath_km,
                omm=json.loads(tle.omm_json),
            )
        )
    return candidates, blocked


def _resolve_aoi(session: Session, spec: PlanSpec) -> tuple[str, str, dict[str, Any]]:
    """返回 (adcode, 名称, GeoJSON FeatureCollection)。只允许浙江省内。"""

    province = session.scalar(select(Aoi).where(Aoi.code == "330000"))
    if province is None or not province.geom_wkt:
        raise PlanError("aoi_unavailable", "数据库里没有浙江省边界，无法校验范围")

    if spec.aoi_wkt:
        try:
            geometry = shapely_wkt.loads(spec.aoi_wkt)
        except Exception as exc:  # noqa: BLE001 - shapely 的异常类型不统一
            raise PlanError("bad_aoi", "AOI WKT 无法解析", detail=str(exc)) from exc
        if geometry.is_empty or geometry.area <= 0:
            raise PlanError("bad_aoi", "AOI 面积必须大于 0")
        province_shape = shape(mapping(shapely_wkt.loads(province.geom_wkt)))
        if not geometry.intersects(province_shape):
            raise PlanError("aoi_out_of_zhejiang", "本版本只支持浙江省内的范围")
        digest = hashlib.sha1(geometry.wkb).hexdigest()[:10]
        adcode = f"custom_{digest}"
        name = "自定义范围"
    else:
        if not spec.aoi_code:
            raise PlanError("missing_params", "缺少 aoi_code 或 aoi_wkt")
        row = session.scalar(
            select(Aoi).where((Aoi.code == spec.aoi_code) | (Aoi.adcode == spec.aoi_code))
        )
        if row is None or not row.geom_wkt:
            raise PlanError("aoi_not_found", f"未知 AOI：{spec.aoi_code}")
        geometry = shapely_wkt.loads(row.geom_wkt)
        adcode = str(row.adcode or row.code)
        name = row.name

    feature = {
        "type": "Feature",
        "properties": {
            "adcode": adcode,
            "name": name,
            "geometry_crs": "EPSG:4326",
            "boundary_version": "rs-service-aoi",
        },
        "geometry": mapping(geometry),
    }
    return adcode, name, {"type": "FeatureCollection", "features": [feature]}


def _write_workspace(
    candidates: list[Candidate],
    aoi: dict[str, Any],
    adcode: str,
) -> Path:
    root = Path(tempfile.mkdtemp(prefix="rs-plan-"))
    geo_dir = root / "data" / "geo"
    tle_dir = root / "data" / "tle"
    geo_dir.mkdir(parents=True)
    tle_dir.mkdir(parents=True)
    (geo_dir / f"{adcode}.json").write_text(
        json.dumps(aoi, ensure_ascii=False), encoding="utf-8"
    )
    payload = {"satellites": [item.omm for item in candidates]}
    (tle_dir / "rs.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _payload(
    spec: PlanSpec,
    candidates: list[Candidate],
    adcode: str,
) -> dict[str, Any]:
    overrides = []
    for item in candidates:
        override: dict[str, Any] = {
            "norad_id": item.norad_id,
            "sensor_type": "Rectangular",
            "swath_km": item.swath_km,
            "along_track_half_angle_deg": ALONG_TRACK_HALF_ANGLE_DEG,
            "min_elevation_deg": 0.0,
        }
        if spec.pointing == "targeted":
            override["pointing"] = {"type": "Targeted"}
        overrides.append(override)

    start = spec.start_utc.astimezone(UTC).replace(microsecond=0)
    payload: dict[str, Any] = {
        "operation": spec.operation,
        "city_adcode": adcode,
        "start_utc": start.isoformat().replace("+00:00", "Z"),
        "analysis_horizon_h": spec.horizon_h,
        "tle_path": "data/tle/rs.json",
        "tle_source": "jilin1_test",
        "profile_slug": "rs_service_db_v1",
        "satellite_ids": [item.norad_id for item in candidates],
        "satellite_profile_overrides": overrides,
        "catalog_sensor_type": "全部",
        "max_resolution_m": 10_000.0,
        "grid_size_km": spec.grid_size_km,
        "detection_step_s": spec.detection_step_s,
        "decision_step_s": spec.decision_step_s,
        "min_simultaneous": spec.min_simultaneous,
        "selection_time_limit_s": spec.selection_time_limit_s,
        "allow_area_fallback": spec.allow_area_fallback,
        "mrt_area_quantile_pct": spec.mrt_area_quantile_pct,
        "max_windows": spec.max_windows,
        "max_weak_areas": spec.max_weak_areas,
    }
    for key in (
        "method",
        "temporal_coverage_target_pct",
        "mrt_target_minutes",
        "n_budget",
        "area_target_pct",
        "fallback_area_target_pct",
        "exact_budget",
    ):
        value = getattr(spec, key)
        if value is not None:
            payload[key] = value
    return payload


def _annotate_result(
    result: dict[str, Any],
    candidates: list[Candidate],
    blocked: list[dict[str, Any]],
) -> dict[str, Any]:
    result["blocked_satellites"] = blocked
    result["candidate_count"] = len(candidates)
    result["blocked_count"] = len(blocked)
    notes = result.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            "候选来自 RS-Service 数据库：分辨率取自 sensor.raw_spec、幅宽取自 sensor.swath_km；"
            f"沿轨半角固定 {ALONG_TRACK_HALF_ANGLE_DEG:g}°（场景假设）。"
        )
        if blocked:
            notes.append(f"未参与选星 {len(blocked)} 颗，原因见 blocked_satellites。")
    return result


def run_plan(spec: PlanSpec) -> dict[str, Any]:
    """执行一次规划；返回引擎结果（含 blocked_satellites / candidate_count）。"""

    with session_scope() as session:
        adcode, _name, aoi = _resolve_aoi(session, spec)
        candidates, blocked = collect_candidates(
            session, spec.sensor_types, spec.max_resolution_m
        )
        if not candidates:
            raise PlanError(
                "no_candidates",
                "当前传感器类型与分辨率筛选下没有可用候选卫星",
                blocked_satellites=blocked,
            )
        root = _write_workspace(candidates, aoi, adcode)
        try:
            payload = _payload(spec, candidates, adcode)
            result = run_skill(payload, root=root)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return _annotate_result(result, candidates, blocked)


__all__ = [
    "ALONG_TRACK_HALF_ANGLE_DEG",
    "Candidate",
    "PlanError",
    "PlanSpec",
    "collect_candidates",
    "run_plan",
]
