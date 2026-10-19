from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from skyfield.api import EarthSatellite, wgs84
from skyfield.timelib import Timescale


@lru_cache(maxsize=1)
def timescale() -> Timescale:
    from skyfield.api import load

    return load.timescale()


def omm_from_json(value: str) -> dict:
    return json.loads(value)


def satellite_from_omm(omm: dict) -> EarthSatellite:
    return EarthSatellite.from_omm(timescale(), omm)


def subpoint(sat: EarthSatellite, when: datetime) -> dict[str, float | str]:
    t = timescale().from_datetime(_aware(when))
    position = sat.at(t).subpoint()
    return {
        "time_utc": _iso(when),
        "lat_deg": position.latitude.degrees,
        "lon_deg": position.longitude.degrees,
        "alt_km": position.elevation.km,
    }


def ground_track(
    sat: EarthSatellite,
    start: datetime,
    end: datetime,
    step_s: int = 60,
) -> list[dict[str, float | str]]:
    points: list[dict[str, float | str]] = []
    current = _aware(start)
    finish = _aware(end)
    while current <= finish:
        points.append(subpoint(sat, current))
        current += timedelta(seconds=step_s)
    return points


def observer(lat_deg: float, lon_deg: float, elevation_m: float = 10.0):
    return wgs84.latlon(lat_deg, lon_deg, elevation_m=elevation_m)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return _aware(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
