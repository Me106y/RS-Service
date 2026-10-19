from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.models import Satellite, TleCurrent, TleRecord
from .passes import find_passes
from .propagate import ground_track, satellite_from_omm, subpoint

ACCURACY_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"
MAX_TLE_AGE_HOURS = 168.0


def current_omm(session: Session, sat_id: int) -> tuple[dict, float]:
    satellite = session.get(Satellite, sat_id)
    if satellite is None:
        raise LookupError(f"satellite {sat_id} not found")
    tle = session.get(TleCurrent, sat_id)
    if tle is None:
        raise LookupError(f"satellite {sat_id} has no current TLE")
    if tle.age_hours is not None and tle.age_hours > MAX_TLE_AGE_HOURS:
        raise ValueError(
            f"TLE age {tle.age_hours:.1f}h exceeds {MAX_TLE_AGE_HOURS:.0f}h; run rs-service sync"
        )
    record = session.get(TleRecord, tle.tle_id)
    if record is None or not record.omm_json:
        raise LookupError(f"TLE record {tle.tle_id} missing OMM")
    return json.loads(record.omm_json), float(tle.age_hours or 0.0)


def passes_for_sat(
    session: Session,
    sat_id: int,
    *,
    latitude_deg: float,
    longitude_deg: float,
    start: datetime,
    end: datetime,
    min_elevation_deg: float = 5.0,
    step_s: int = 30,
) -> dict:
    omm, age = current_omm(session, sat_id)
    sat = satellite_from_omm(omm)
    passes = find_passes(
        sat,
        latitude_deg,
        longitude_deg,
        start,
        end,
        min_elevation_deg=min_elevation_deg,
        step_s=step_s,
    )
    return {
        "sat_id": sat_id,
        "aoi": {"lat": latitude_deg, "lon": longitude_deg},
        "min_elevation_deg": min_elevation_deg,
        "tle_age_hours": round(age, 2),
        "passes": passes,
        "accuracy_note": ACCURACY_NOTE,
    }


def track_for_sat(
    session: Session,
    sat_id: int,
    *,
    start: datetime,
    end: datetime,
    step_s: int = 60,
) -> dict:
    omm, age = current_omm(session, sat_id)
    sat = satellite_from_omm(omm)
    points = ground_track(sat, start, end, step_s=step_s)
    return {
        "sat_id": sat_id,
        "tle_age_hours": round(age, 2),
        "points": points,
        "accuracy_note": ACCURACY_NOTE,
    }


def position_now(session: Session, sat_id: int) -> dict:
    omm, age = current_omm(session, sat_id)
    sat = satellite_from_omm(omm)
    point = subpoint(sat, datetime.now(UTC))
    return {"sat_id": sat_id, "tle_age_hours": round(age, 2), **point, "accuracy_note": ACCURACY_NOTE}
