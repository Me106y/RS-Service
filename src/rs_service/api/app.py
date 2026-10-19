from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import func, select

from .. import __version__
from ..astro.service import all_positions, passes_for_sat, position_now, track_for_sat
from ..db.models import Satellite, Sensor, SyncRun, SyncState, TleCurrent
from ..db.session import session_scope

ACCURACY_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"


def create_app() -> FastAPI:
    app = FastAPI(title="RS-Service", version=__version__)

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        with session_scope() as session:
            sat_count = session.scalar(select(func.count()).select_from(Satellite)) or 0
            last_run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
            state = session.scalar(select(SyncState))
        return {
            "ok": True,
            "version": __version__,
            "satellites": sat_count,
            "last_sync": _run_dict(last_run) if last_run else None,
            "sync_state": _state_dict(state) if state else None,
            "accuracy_note": ACCURACY_NOTE,
        }

    @app.get("/api/v1/satellites")
    def list_satellites(
        category: str | None = Query(default=None),
        orbit: str | None = Query(default=None),
        payload_public: bool | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        with session_scope() as session:
            stmt = select(Satellite)
            if category:
                stmt = stmt.where(Satellite.category == category)
            if orbit:
                stmt = stmt.where(Satellite.orbit_regime == orbit)
            if payload_public is not None:
                stmt = stmt.where(Satellite.payload_public == payload_public)
            total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            rows = list(session.scalars(stmt.order_by(Satellite.sat_id).limit(limit).offset(offset)))
            return {
                "total": total,
                "items": [_sat_dict(row) for row in rows],
                "accuracy_note": ACCURACY_NOTE,
            }

    @app.get("/api/v1/positions")
    def positions() -> dict[str, Any]:
        with session_scope() as session:
            return all_positions(session)

    @app.get("/api/v1/satellites/{sat_id}")
    def get_satellite(sat_id: int) -> dict[str, Any]:
        with session_scope() as session:
            sat = session.get(Satellite, sat_id)
            if sat is None:
                return {"ok": False, "error": "not_found", "sat_id": sat_id}
            sensor = session.scalar(select(Sensor).where(Sensor.sat_id == sat_id))
            tle = session.get(TleCurrent, sat_id)
            return {
                "satellite": _sat_dict(sat),
                "sensor": _sensor_dict(sensor, payload_public=sat.payload_public),
                "tle": _tle_dict(tle),
                "accuracy_note": ACCURACY_NOTE,
            }

    @app.get("/api/v1/sync/status")
    def sync_status() -> dict[str, Any]:
        with session_scope() as session:
            last_run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
            state = session.scalar(select(SyncState))
            tle_count = session.scalar(select(func.count()).select_from(TleCurrent)) or 0
            max_age = session.scalar(select(func.max(TleCurrent.age_hours)))
        return {
            "last_run": _run_dict(last_run) if last_run else None,
            "state": _state_dict(state) if state else None,
            "tle_current_count": tle_count,
            "tle_max_age_hours": max_age,
            "accuracy_note": ACCURACY_NOTE,
        }

    @app.get("/api/v1/satellites/{sat_id}/passes")
    def satellite_passes(
        sat_id: int,
        hours: int = Query(default=24, ge=1, le=168),
        lat: float = Query(default=30.27, ge=-90, le=90),
        lon: float = Query(default=120.16, ge=-180, le=180),
        min_elev: float = Query(default=5.0, ge=0, le=90),
        step: int = Query(default=30, ge=5, le=300),
    ) -> dict[str, Any]:
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
                    step_s=step,
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/satellites/{sat_id}/track")
    def satellite_track(
        sat_id: int,
        hours: int = Query(default=2, ge=1, le=24),
        step: int = Query(default=60, ge=10, le=600),
    ) -> dict[str, Any]:
        start = datetime.now(UTC)
        try:
            with session_scope() as session:
                return track_for_sat(
                    session, sat_id, start=start, end=start + timedelta(hours=hours), step_s=step
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/satellites/{sat_id}/position")
    def satellite_position(sat_id: int) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return position_now(session, sat_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return app


def _sat_dict(sat: Satellite) -> dict[str, Any]:
    return {
        "norad": sat.sat_id,
        "name": sat.name,
        "name_zh": sat.name_zh,
        "category": sat.category,
        "orbit_regime": sat.orbit_regime,
        "operator": sat.operator,
        "country": sat.country,
        "status": sat.status,
        "payload_public": sat.payload_public,
    }


def _sensor_dict(sensor: Sensor | None, *, payload_public: bool) -> dict[str, Any] | None:
    if sensor is None:
        return None
    base = {
        "sensor_type": sensor.sensor_type,
        "confidence": sensor.confidence,
        "payload_public": payload_public,
    }
    if not payload_public:
        return base
    base.update(
        {
            "raw_spec": sensor.raw_spec,
            "pan_resolution_m": sensor.pan_resolution_m,
            "ms_resolution_m": sensor.ms_resolution_m,
            "swath_km": sensor.swath_km,
            "bands_json": sensor.bands_json,
        }
    )
    return base


def _tle_dict(tle: TleCurrent | None) -> dict[str, Any] | None:
    if tle is None:
        return None
    return {
        "tle_id": tle.tle_id,
        "epoch_utc": tle.epoch_utc,
        "age_hours": tle.age_hours,
    }


def _run_dict(run: SyncRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "attempt_no": run.attempt_no,
        "max_attempts": run.max_attempts,
        "started_at_utc": run.started_at_utc,
        "finished_at_utc": run.finished_at_utc,
        "http_status": run.http_status,
        "inserted": run.inserted,
        "unchanged": run.unchanged,
        "failed": run.failed,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def _state_dict(state: SyncState) -> dict[str, Any]:
    return {
        "last_attempt_at": state.last_attempt_at,
        "last_success_at": state.last_success_at,
        "last_status": state.last_status,
        "consecutive_failures": state.consecutive_failures,
        "attempts_today": state.attempts_today,
        "retry_count_today": state.retry_count_today,
        "circuit_open_until": state.circuit_open_until,
        "next_scheduled_at": state.next_scheduled_at,
    }
