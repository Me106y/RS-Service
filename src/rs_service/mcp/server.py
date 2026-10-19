from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from .. import __version__
from ..astro.coverage import COVERAGE_IDS
from ..astro.coverage import coverage_grid as build_coverage
from ..astro.service import all_positions, passes_for_sat, position_now, track_for_sat
from ..db.models import Satellite, Sensor, SyncRun, SyncState, TleCurrent
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


@mcp.tool()
def list_satellites(category: str | None = None, orbit: str | None = None) -> dict[str, Any]:
    """List the 111 domestic satellites, optionally filtered by category or orbit regime."""
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
def coverage_window(
    lat: float = 30.27,
    lon: float = 120.16,
    radius_km: float = 150.0,
    hours: int = 24,
) -> dict[str, Any]:
    """Estimate per-cell pass coverage over an AOI for the focus satellite set."""
    with session_scope() as session:
        return build_coverage(
            session,
            latitude_deg=lat,
            longitude_deg=lon,
            radius_km=radius_km,
            hours=hours,
            sat_ids=COVERAGE_IDS,
        )


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
