from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import select

from .. import __version__
from ..astro.service import all_positions, passes_for_sat, position_now, track_for_sat
from ..db.models import Aoi, Satellite, Sensor, SyncRun, SyncState, TleCurrent
from ..db.session import session_scope
from ..interpret import service as interpret_service
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


@mcp.tool()
def list_workspace_images() -> dict[str, Any]:
    """List images in the SIRIAgent workspace (purchased and uploaded), without binary data."""
    return {
        "ok": True,
        "items": interpret_service.list_workspace_images(),
        "accuracy_note": interpret_service.ACCURACY_NOTE,
    }


@mcp.tool()
def detect_change(before_path: str, after_path: str) -> dict[str, Any]:
    """Run change detection synchronously on two workspace images.

    Paths are relative to the SIRIAgent root and must stay under workspace/.
    Returns the mask path, changed pixels/ratio, components, elapsed time and device.
    """
    try:
        return interpret_service.run_change_detection(before_path, after_path)
    except TimeoutError as exc:
        return {"ok": False, "error": "timeout", "message": str(exc)}
    except PermissionError as exc:
        return {"ok": False, "error": "forbidden", "message": str(exc)}
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


@mcp.tool()
def detect_objects(image_path: str) -> dict[str, Any]:
    """Run YOLOX-S object detection synchronously on one workspace/demo image.

    Paths are relative to the SIRIAgent root and must stay under workspace/ or demo/.
    Returns the annotated image path, detection count/classes, boxes, elapsed time
    and an accuracy note.
    """
    try:
        return interpret_service.run_object_detection(image_path)
    except PermissionError as exc:
        return {"ok": False, "error": "forbidden", "message": str(exc)}
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


@mcp.tool()
def segment_semantics(image_path: str) -> dict[str, Any]:
    """Run SparseMask (OpenEarthMap 8 classes) semantic segmentation on one image.

    Paths are relative to the SIRIAgent root and must stay under workspace/ or demo/.
    Returns the color map / overlay paths, per-class pixel counts and ratios,
    elapsed time and an accuracy note.
    """
    try:
        return interpret_service.run_semantic_segmentation(image_path)
    except PermissionError as exc:
        return {"ok": False, "error": "forbidden", "message": str(exc)}
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def run_server(host: str | None = None, port: int | None = None) -> None:
    interpret_service.warmup_safe()
    mcp.run(
        transport="streamable-http",
        host=host or settings.mcp_host,
        port=port or settings.mcp_port,
        streamable_http_path="/mcp",
    )
