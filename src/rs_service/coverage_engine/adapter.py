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

import copy
import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from shapely import wkt as shapely_wkt
from shapely.geometry import mapping, shape
from siriagent_skill.facade import execute_request  # noqa: E402
from siriagent_skill.schemas import SkillInputError, normalize_request  # noqa: E402
from sqlalchemy import select
from sqlalchemy.orm import Session

from rs_service.db.models import Aoi, Satellite, Sensor, TleCurrent, TleRecord
from rs_service.db.session import session_scope

from ..astro import cloud

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
    cloud_filter: bool = True
    cloud_threshold_pct: float = 70.0
    auto_expand: bool = False


@dataclass(frozen=True)
class Candidate:
    norad_id: int
    name: str
    sensor_class: str
    resolution_m: float
    swath_km: float
    omm: dict[str, Any]
    tle_epoch_utc: str
    tle_age_hours: float


def tle_age_hours(epoch_utc: str | None, now: datetime | None = None) -> float | None:
    if not epoch_utc:
        return None
    try:
        moment = datetime.fromisoformat(epoch_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return (reference - moment).total_seconds() / 3600.0


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
        age_hours = tle_age_hours(tle.epoch_utc)
        if age_hours is None:
            blocked.append(_blocked(sat.sat_id, sat.name, sensor_type, "缺少 TLE 时间"))
            continue
        if age_hours > 168.0:
            blocked.append(
                _blocked(
                    sat.sat_id,
                    sat.name,
                    sensor_type,
                    f"TLE 年龄 {age_hours:.1f} 小时（>7 天），请先同步",
                )
            )
            continue
        candidates.append(
            Candidate(
                norad_id=int(sat.sat_id),
                name=sat.name,
                sensor_class=sensor_class,
                resolution_m=resolution_m,
                swath_km=swath_km,
                omm=json.loads(tle.omm_json),
                tle_epoch_utc=tle.epoch_utc,
                tle_age_hours=age_hours,
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
    centroid = geometry.centroid
    return (
        adcode,
        name,
        {"type": "FeatureCollection", "features": [feature]},
        float(centroid.y),
        float(centroid.x),
    )


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


def _filter_windows(
    windows: dict[tuple[int, int], tuple[Any, ...]],
    series: list[tuple[datetime, float]],
    threshold_pct: float,
) -> tuple[dict[tuple[int, int], tuple[Any, ...]], int]:
    """按窗口中点云量剔除访问；同一 10 分钟桶只插值一次。"""

    kept: dict[tuple[int, int], tuple[Any, ...]] = {}
    dropped = 0
    cache: dict[datetime, float | None] = {}
    for key, items in windows.items():
        keep = []
        for window in items:
            middle = window.start_utc + (window.end_utc - window.start_utc) / 2
            bucket = middle.replace(
                minute=(middle.minute // 10) * 10, second=0, microsecond=0
            )
            if bucket not in cache:
                cache[bucket] = cloud.cloud_at(series, middle)
            value = cache[bucket]
            if value is not None and value > threshold_pct:
                dropped += 1
                continue
            keep.append(window)
        if keep:
            kept[key] = tuple(keep)
    return kept, dropped


def _apply_cloud(
    store: Any,
    spec: PlanSpec,
    lat: float,
    lon: float,
    stats: dict[str, Any],
) -> Any:
    series = cloud.series_for(lat, lon, spec.start_utc, max(1, int(spec.horizon_h) + 1))
    if not series:
        stats.update(
            filtered=False,
            reason="Open-Meteo 不可用，本次未过滤云量",
        )
        return store
    kept, dropped = _filter_windows(store.windows, series, spec.cloud_threshold_pct)
    filtered = copy.copy(store)
    filtered.windows = kept
    filtered.metadata = dict(store.metadata)
    filtered.metadata["cloud"] = {
        "threshold_pct": spec.cloud_threshold_pct,
        "dropped_windows": dropped,
    }
    stats.update(
        filtered=True,
        threshold_pct=spec.cloud_threshold_pct,
        dropped_windows=dropped,
        reason=None,
    )
    return filtered


def _aoi_outline(aoi: dict[str, Any]) -> list[list[float]]:
    geometry = shape(aoi["features"][0]["geometry"])
    if geometry.geom_type == "MultiPolygon":
        geometry = max(geometry.geoms, key=lambda item: item.area)
    return [[round(x, 6), round(y, 6)] for x, y in geometry.exterior.coords]


def _playback(
    store: Any,
    selected_indices: Iterable[int],
    start_utc: datetime,
) -> dict[str, Any]:
    """从机会窗口重建"逐格点首次覆盖"时间轴，供旧报告的地图画布回放。"""

    selected = {int(item) for item in selected_indices}
    cells = store.grid.cells
    first: dict[int, tuple[datetime, int]] = {}
    for (sat_index, grid_index), windows in store.windows.items():
        if sat_index not in selected or not windows:
            continue
        moment = min(item.start_utc for item in windows)
        current = first.get(grid_index)
        if current is None or moment < current[0]:
            first[grid_index] = (moment, sat_index)

    names = {
        int(store.satellites[index].norad_id): store.satellites[index].name
        for index in selected
    }
    palette = ["#48c7d9", "#f0a52b", "#8b7ff0", "#5fbf8f", "#e0574a", "#c084fc", "#7dd3fc", "#d6b25e"]
    palette_light = ["#0e7490", "#b45309", "#6d28d9", "#15803d", "#b91c1c", "#7e22ce", "#0369a1", "#a16207"]
    colors: dict[str, str] = {}
    colors_light: dict[str, str] = {}
    for position, norad in enumerate(sorted(names)):
        colors[str(norad)] = palette[position % len(palette)]
        colors_light[str(norad)] = palette_light[position % len(palette_light)]

    grouped: dict[tuple[int, datetime], list[list[float]]] = {}
    for grid_index, (moment, sat_index) in first.items():
        cell = cells[grid_index]
        bucket = moment.replace(second=0, microsecond=0)
        grouped.setdefault((sat_index, bucket), []).append(
            [round(cell.representative_lon, 4), round(cell.representative_lat, 4)]
        )

    events = []
    for (sat_index, moment) in sorted(grouped, key=lambda item: item[1]):
        norad = int(store.satellites[sat_index].norad_id)
        events.append(
            {
                "sat_id": norad,
                "label": (moment + timedelta(hours=8)).strftime("%m-%d %H:%M"),
                "points": grouped[(sat_index, moment)],
            }
        )
    uncovered = [
        [round(cell.representative_lon, 4), round(cell.representative_lat, 4)]
        for index, cell in enumerate(cells)
        if index not in first
    ]
    return {
        "events": events,
        "uncovered": uncovered,
        "total": len(cells),
        "colors": colors,
        "colors_light": colors_light,
        "start_label": (start_utc + timedelta(hours=8)).strftime("%m-%d %H:%M"),
    }


def _attach_report_data(
    result: dict[str, Any],
    candidates: list[Candidate],
    aoi: dict[str, Any],
    store: Any,
) -> None:
    selection = (result.get("selection") or {}).get("recommended_result") or {}
    selected_ids = [int(item) for item in selection.get("selected_sat_ids") or []]
    profiles = {item.norad_id: item for item in candidates}
    table = {int(item["norad_id"]): item for item in result.get("satellite_table") or []}
    sats = []
    for position, norad in enumerate(selected_ids):
        row = table.get(norad, {})
        profile = profiles.get(norad)
        sats.append(
            {
                "sat_id": norad,
                "name": row.get("satellite") or (profile.name if profile else str(norad)),
                "sensor_label": profile.sensor_class if profile else "—",
                "resolution_m": profile.resolution_m if profile else None,
                "swath_km": profile.swath_km if profile else None,
                "passes": row.get("city_pass_count"),
                "cumulative_minutes": row.get("cumulative_city_intersection_minutes"),
                "first_window": row.get("first_window_utc"),
                "rank": position + 1,
            }
        )
    start = datetime.fromisoformat(
        result["input_summary"]["start_utc"].replace("Z", "+00:00")
    )
    result["report_data"] = {
        "aoi_outline": _aoi_outline(aoi),
        "playback": _playback(store, selection.get("selected_sat_indices") or [], start),
        "sats": sats,
        "selected_ids": selected_ids,
        "selected_count": len(selected_ids),
    }


def _annotate_result(
    result: dict[str, Any],
    candidates: list[Candidate],
    blocked: list[dict[str, Any]],
    cloud_stats: dict[str, Any],
    tle_info: dict[str, Any],
) -> dict[str, Any]:
    recommended = (result.get("selection") or {}).get("recommended_result") or {}
    result["targets"] = {
        "temporal_coverage_pct": recommended.get("temporal_coverage_target_pct"),
        "area_pct": recommended.get("area_target_pct"),
        "mrt_minutes": recommended.get("mrt_target_minutes"),
    }
    result["tle"] = tle_info
    result["blocked_satellites"] = blocked
    result["candidate_count"] = len(candidates)
    result["blocked_count"] = len(blocked)
    result["cloud"] = cloud_stats
    assumptions = [
        item
        for item in (result.get("assumptions") or [])
        if "不包含云量" not in str(item)
    ]
    if cloud_stats.get("filtered"):
        assumptions.append(
            "云量按 Open-Meteo 逐小时 cloud_cover >"
            f"{cloud_stats.get('threshold_pct')}% 剔除访问；云量为网格预报值，"
            "不代表成像时刻的实测云量。"
        )
    else:
        assumptions.append(f"本次未过滤云量：{cloud_stats.get('reason') or '未启用'}。")
    if tle_info.get("max_age_hours") is not None:
        suffix = "，结果标记 degraded" if tle_info.get("degraded") else ""
        assumptions.append(
            f"TLE 最大年龄 {tle_info['max_age_hours']:g} 小时；"
            f"候选最新 TLE epoch {tle_info.get('latest_epoch_utc')}{suffix}。"
        )
    result["assumptions"] = assumptions
    notes = result.setdefault("notes", [])
    if isinstance(notes, list):
        notes.append(
            "候选来自 RS-Service 数据库：分辨率取自 sensor.raw_spec、幅宽取自 sensor.swath_km；"
            f"沿轨半角固定 {ALONG_TRACK_HALF_ANGLE_DEG:g}°（场景假设）。"
        )
        if blocked:
            notes.append(f"未参与选星 {len(blocked)} 颗，原因见 blocked_satellites。")
        if cloud_stats.get("filtered"):
            notes.append(
                "云量按 Open-Meteo 逐小时 cloud_cover "
                f">{cloud_stats.get('threshold_pct')}% 剔除访问（丢弃窗口 "
                f"{cloud_stats.get('dropped_windows')} 个）。"
            )
        else:
            notes.append(f"云量未过滤：{cloud_stats.get('reason') or '未启用'}。")
    return result


def _run_once(spec: PlanSpec) -> dict[str, Any]:
    """按单一窗口执行一次规划。"""

    cloud_stats: dict[str, Any] = {"filtered": False, "reason": "未启用"}
    with session_scope() as session:
        adcode, _name, aoi, lat, lon = _resolve_aoi(session, spec)
        candidates, blocked = collect_candidates(
            session, spec.sensor_types, spec.max_resolution_m
        )
        if not candidates:
            if blocked and all("TLE" in item.get("reason", "") for item in blocked):
                raise PlanError(
                    "tle_stale",
                    "TLE 已超过 7 天，请先运行 `rs-service sync` 再规划",
                    blocked_satellites=blocked,
                )
            raise PlanError(
                "no_candidates",
                "当前传感器类型与分辨率筛选下没有可用候选卫星",
                blocked_satellites=blocked,
            )
        ages = [item.tle_age_hours for item in candidates]
        max_age = max(ages) if ages else 0.0
        tle_info = {
            "latest_epoch_utc": max(item.tle_epoch_utc for item in candidates),
            "max_age_hours": round(max_age, 1),
            "min_age_hours": round(min(ages), 1) if ages else None,
            "degraded": max_age > 72.0,
            "stale": False,
        }
        root = _write_workspace(candidates, aoi, adcode)
        try:
            payload = _payload(spec, candidates, adcode)
            try:
                request = normalize_request(payload)
            except SkillInputError as exc:
                raise PlanError("bad_params", str(exc)) from exc
            captured: dict[str, Any] = {}

            def _capture(store: Any) -> Any:
                if spec.cloud_filter:
                    store = _apply_cloud(store, spec, lat, lon, cloud_stats)
                captured["store"] = store
                return store

            result = execute_request(request, root=root, window_filter=_capture)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    _annotate_result(result, candidates, blocked, cloud_stats, tle_info)
    if captured.get("store") is not None:
        _attach_report_data(result, candidates, aoi, captured["store"])
    return result


def _horizons(spec: PlanSpec) -> list[float]:
    horizons = [float(spec.horizon_h)]
    if spec.auto_expand and spec.operation in {
        "task1",
        "task_1_min_satellites_temporal",
    }:
        # 168h 在 65 星/775 网格上会跑数分钟，自动扩窗只到 72h；7 天由用户手动选。
        for extra in (72.0,):
            if extra > spec.horizon_h + 1e-9 and all(
                abs(extra - item) > 1e-9 for item in horizons
            ):
                horizons.append(extra)
    return horizons


def run_plan(spec: PlanSpec) -> dict[str, Any]:
    """执行规划；task1 可自动扩窗到 72h，返回实际使用的窗口。"""

    horizons = _horizons(spec)
    last: dict[str, Any] | None = None
    for attempt, horizon in enumerate(horizons, 1):
        current = (
            spec
            if abs(horizon - spec.horizon_h) < 1e-9
            else replace(spec, horizon_h=horizon)
        )
        result = _run_once(current)
        expanded = abs(horizon - spec.horizon_h) > 1e-9
        result["window"] = {
            "requested_horizon_h": spec.horizon_h,
            "used_horizon_h": horizon,
            "expanded": expanded,
            "attempts": attempt,
        }
        reason = None
        if spec.auto_expand:
            if result.get("feasible"):
                reason = "当前窗口已达标，无需扩窗" if not expanded else "自动扩窗后达标"
            elif not expanded and spec.horizon_h >= 72.0 - 1e-9:
                reason = "当前窗口已是自动扩窗上限 72h；7 天请手动设 168h"
            elif not expanded:
                reason = "当前窗口未达标，但没有更长的自动档位"
            else:
                reason = "已自动扩窗到 72h，仍未达标"
        result["auto_expand"] = {
            "requested": bool(spec.auto_expand),
            "supported": spec.operation
            in {"task1", "task_1_min_satellites_temporal"},
            "expanded": expanded,
            "attempts": attempt,
            "reason": reason,
        }
        if expanded:
            result.setdefault("assumptions", []).append(
                f"自动扩窗：请求 {spec.horizon_h:g}h，实际 {horizon:g}h；"
                "这是不同窗口下的结果，报告按实际窗口展示。"
            )
        last = result
        if result.get("feasible"):
            break
    assert last is not None
    return last


__all__ = [
    "ALONG_TRACK_HALF_ANGLE_DEG",
    "Candidate",
    "PlanError",
    "PlanSpec",
    "collect_candidates",
    "run_plan",
    "tle_age_hours",
]
