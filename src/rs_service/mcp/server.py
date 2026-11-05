from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from .. import __version__
from ..astro import planner
from ..astro.service import all_positions, passes_for_sat, position_now, track_for_sat
from ..db.models import Aoi, Satellite, Sensor, SyncRun, SyncState, TleCurrent
from ..db.session import session_scope
from ..settings import settings

ACCURACY_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"

mcp = MCPServer(
    name="rs-service",
    version=__version__,
    instructions=(
        "Ephemeris service for SIRIAgent. All positions come from TLE/SGP4; "
        "along-track error is about 1 km per day. Use 5 deg elevation for visibility "
        "and 10 deg for imaging; optical imaging also needs sun elevation > 10 deg."
    ),
)


PARAM_SCHEMA = {
    "mode": {"type": "string", "required": True,
             "options": list(planner.MODES),
             "desc": "任务模式"},
    "aoi": {"type": "string|array", "required": True,
            "desc": "adcode / 名称 / POLYGON WKT，可传列表取并集"},
    "max_resolution_m": {"type": "number", "required": True, "unit": "m",
                         "desc": "分辨率不差于此值（数值越小越清晰）"},
    "target_coverage_pct": {"type": "number", "required_when": ["min_sats_for_coverage"],
                            "range": [1, 100], "unit": "%",
                            "desc": "目标覆盖率；只有 min_sats_for_coverage 必填，其余模式覆盖率是输出"},
    "n_sats": {"type": "integer", "required_when": ["max_coverage_for_n", "min_wait_for_n"],
               "range": [1, 20]},
    "target_max_gap_hours": {"type": "number", "required_when": ["min_sats_for_revisit"], "unit": "h"},
    "sensor_types": {"type": "array", "default": ["optical", "sar", "hyperspectral"],
                     "options": ["optical", "sar", "hyperspectral"]},
    "hours": {"type": "integer", "default": 72, "max": planner.MAX_HOURS,
              "desc": "计算时间窗（小时）。给定 N 颗的模式按此窗口出结果，不自动扩窗"},
    "start": {"type": "string", "default": "now", "format": "ISO8601 UTC"},
    "optical_sun_min_deg": {"type": "number", "default": 10.0, "unit": "°",
                            "desc": "光学/高光谱成像的太阳高度门限；夜间访问不计入覆盖"},
    "extend": {"type": "boolean", "default": True,
               "desc": "仅 min_sats_for_coverage 生效：达不到目标时自动扩窗 72h→7d→14d"},
}


def _missing(err: planner.PlanError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": err.code,
        "missing": err.extra.get("missing", []),
        "schema": {k: v for k, v in PARAM_SCHEMA.items() if k in err.extra.get("missing", [])},
        **{k: v for k, v in err.extra.items() if k != "missing"},
        "accuracy_note": ACCURACY_NOTE,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@mcp.tool()
def plan_coverage(
    mode: str | None = None,
    aoi: str | list[str] | None = None,
    max_resolution_m: float | None = None,
    target_coverage_pct: float | None = None,
    n_sats: int | None = None,
    target_max_gap_hours: float | None = None,
    sensor_types: list[str] | None = None,
    hours: int = 72,
    start: str | None = None,
    extend: bool = True,
    persist: bool = True,
    cloud_filter: bool = True,
    cloud_threshold: float = 70.0,
    optical_sun_min_deg: float | None = None,
) -> dict[str, Any]:
    """求解卫星成图编成方案：给定覆盖率/重访/星数，算出最少需要哪几颗、能覆盖多少。

    `min_sats_for_coverage` 需要 target_coverage_pct；`min_sats_for_revisit` 需要
    target_max_gap_hours；`max_coverage_for_n` / `min_wait_for_n` 需要 n_sats 和 hours，
    覆盖率是输出（传了也会被忽略），并按给定窗口计算、不自动扩窗。

    参数不全时不猜，返回 {"ok": false, "error": "missing_params", "missing": [...], "schema": {...}}。
    """
    if not mode:
        return _missing(planner.PlanError("missing_params", missing=["mode"]))
    t0 = None
    if start:
        try:
            t0 = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return {"ok": False, "error": "bad_start", "start": start, "accuracy_note": ACCURACY_NOTE}
    try:
        with session_scope() as session:
            data = planner.solve(
                session,
                mode=mode, aoi=aoi or "", max_resolution_m=max_resolution_m or 0,
                target_coverage_pct=target_coverage_pct,
                n_sats=n_sats, target_max_gap_hours=target_max_gap_hours,
                sensor_types=sensor_types, hours=min(hours, planner.MAX_HOURS),
                start=t0, extend=extend, persist=persist,
                cloud_filter=cloud_filter, cloud_threshold=cloud_threshold,
                optical_sun_min_deg=optical_sun_min_deg,
            )
    except planner.PlanError as err:
        if err.code == "missing_params":
            return _missing(err)
        return {"ok": False, "error": err.code, **err.extra, "accuracy_note": ACCURACY_NOTE}
    return {
        "ok": True,
        "data": data,
        "accuracy_note": ACCURACY_NOTE,
        "stale": False,
        "degraded": False,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@mcp.tool()
def list_satellites(category: str | None = None, orbit: str | None = None) -> dict[str, Any]:
    """List the 102 domestic LEO satellites, optionally filtered by category or orbit regime."""
    with session_scope() as session:
        stmt = select(Satellite)
        if category:
            stmt = stmt.where(Satellite.category == category)
        if orbit:
            stmt = stmt.where(Satellite.orbit_regime == orbit)
        rows = list(session.scalars(stmt.order_by(Satellite.sat_id)))
        return {
            "count": len(rows),
            "items": [
                {
                    "norad": row.sat_id,
                    "name": row.name,
                    "category": row.category,
                    "orbit_regime": row.orbit_regime,
                    "payload_public": row.payload_public,
                }
                for row in rows
            ],
            "accuracy_note": ACCURACY_NOTE,
        }


@mcp.tool()
def list_aoi(level: str | None = None, query: str | None = None) -> dict[str, Any]:
    """List supported AOIs (only Zhejiang is in scope). Use these as the question options."""
    with session_scope() as session:
        stmt = select(Aoi).order_by(Aoi.code)
        if level:
            stmt = stmt.where(Aoi.level == level)
        rows = list(session.scalars(stmt))
    if query:
        needle = query.strip().lower()
        rows = [
            row
            for row in rows
            if needle in (row.name or "").lower() or needle in (row.adcode or "")
        ]
    return {
        "ok": True,
        "count": len(rows),
        "scope": "zhejiang_only",
        "items": [
            {
                "code": row.code,
                "name": row.name,
                "level": row.level,
                "adcode": row.adcode,
                "bbox": [row.min_lon, row.min_lat, row.max_lon, row.max_lat],
                "area_km2": row.area_km2,
            }
            for row in rows
        ],
        "accuracy_note": ACCURACY_NOTE,
    }


@mcp.tool()
def get_satellite(sat_id: int) -> dict[str, Any]:
    """Return one satellite's catalog entry, sensor summary and current TLE age."""
    with session_scope() as session:
        sat = session.get(Satellite, sat_id)
        if sat is None:
            return {"ok": False, "error": "not_found", "sat_id": sat_id}
        sensor = session.scalar(select(Sensor).where(Sensor.sat_id == sat_id))
        tle = session.get(TleCurrent, sat_id)
        return {
            "ok": True,
            "satellite": {
                "norad": sat.sat_id,
                "name": sat.name,
                "name_zh": sat.name_zh,
                "category": sat.category,
                "orbit_regime": sat.orbit_regime,
                "operator": sat.operator,
                "payload_public": sat.payload_public,
            },
            "sensor": {
                "sensor_type": sensor.sensor_type if sensor else None,
                "confidence": sensor.confidence if sensor else None,
                "raw_spec": sensor.raw_spec if sensor and sat.payload_public else None,
            },
            "tle": (
                {"epoch_utc": tle.epoch_utc, "age_hours": tle.age_hours} if tle else None
            ),
            "accuracy_note": ACCURACY_NOTE,
        }


@mcp.tool()
def sync_status() -> dict[str, Any]:
    """Return the latest sync attempt, its retry state and TLE freshness."""
    with session_scope() as session:
        last_run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
        state = session.scalar(select(SyncState))
        tle = session.scalar(select(TleCurrent).order_by(TleCurrent.age_hours.desc()).limit(1))
        return {
            "last_run": (
                {
                    "status": last_run.status,
                    "attempt_no": last_run.attempt_no,
                    "max_attempts": last_run.max_attempts,
                    "started_at_utc": last_run.started_at_utc,
                    "finished_at_utc": last_run.finished_at_utc,
                    "error_code": last_run.error_code,
                }
                if last_run
                else None
            ),
            "state": (
                {
                    "last_attempt_at": state.last_attempt_at,
                    "last_success_at": state.last_success_at,
                    "last_status": state.last_status,
                    "consecutive_failures": state.consecutive_failures,
                    "attempts_today": state.attempts_today,
                    "circuit_open_until": state.circuit_open_until,
                }
                if state
                else None
            ),
            "max_tle_age_hours": tle.age_hours if tle else None,
            "accuracy_note": ACCURACY_NOTE,
        }


@mcp.tool()
def next_passes(
    sat_id: int,
    hours: int = 24,
    lat: float = 30.27,
    lon: float = 120.16,
    min_elev: float = 5.0,
) -> dict[str, Any]:
    """Return pass windows for one satellite over an AOI point (default Hangzhou)."""
    start = datetime.now(UTC)
    try:
        with session_scope() as session:
            return passes_for_sat(
                session,
                sat_id,
                latitude_deg=lat,
                longitude_deg=lon,
                start=start,
                end=start + timedelta(hours=hours),
                min_elevation_deg=min_elev,
            )
    except (LookupError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "sat_id": sat_id}


@mcp.tool()
def ground_track(sat_id: int, hours: int = 2, step_s: int = 60) -> dict[str, Any]:
    """Return the sub-satellite track for one satellite."""
    start = datetime.now(UTC)
    try:
        with session_scope() as session:
            return track_for_sat(
                session, sat_id, start=start, end=start + timedelta(hours=hours), step_s=step_s
            )
    except (LookupError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "sat_id": sat_id}


@mcp.tool()
def list_positions() -> dict[str, Any]:
    """Return current sub-satellite points for all satellites with fresh TLEs."""
    with session_scope() as session:
        return all_positions(session)


@mcp.tool()
def current_position(sat_id: int) -> dict[str, Any]:
    """Return the current sub-satellite point for one satellite."""
    try:
        with session_scope() as session:
            return position_now(session, sat_id)
    except (LookupError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "sat_id": sat_id}


def run_server(host: str | None = None, port: int | None = None) -> None:
    mcp.run(
        transport="streamable-http",
        host=host or settings.mcp_host,
        port=port or settings.mcp_port,
        streamable_http_path="/mcp",
    )
