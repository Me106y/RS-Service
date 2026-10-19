from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Satellite, Sensor
from .passes import find_passes
from .propagate import satellite_from_omm, subpoint
from .service import ACCURACY_NOTE, current_omm

# 重点保障集里的 19 颗高分辨率光学/SAR/宽幅星；气象海洋与 GEO 不参与覆盖热力图。
COVERAGE_IDS = [
    40118,
    44703,
    45016,
    49256,
    52320,
    53587,
    41727,
    49495,
    52200,
    51284,
    51822,
    54035,
    56153,
    43260,
    43484,
    45939,
    44528,
    49122,
    46478,
]

_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_S = 300.0


def coverage_grid(
    session: Session,
    *,
    latitude_deg: float,
    longitude_deg: float,
    radius_km: float = 150.0,
    hours: int = 24,
    grid_deg: float = 0.2,
    start: datetime | None = None,
    min_elevation_deg: float = 5.0,
    step_s: int = 60,
    sat_ids: list[int] | None = None,
) -> dict:
    started = time.time()
    start = (start or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    end = start + timedelta(hours=hours)
    ids = sat_ids or COVERAGE_IDS
    key = (
        round(latitude_deg, 3),
        round(longitude_deg, 3),
        round(radius_km, 1),
        hours,
        round(grid_deg, 3),
        f"{start.strftime('%Y-%m-%dT%H')}:{(start.minute // 10) * 10:02d}Z",
        tuple(ids),
    )
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_TTL_S:
        return cached[1]

    lat_step = grid_deg
    lon_step = grid_deg
    lat_span = radius_km / 111.0
    lon_span = radius_km / (111.0 * max(0.2, math.cos(math.radians(latitude_deg))))
    min_lat, max_lat = latitude_deg - lat_span, latitude_deg + lat_span
    min_lon, max_lon = longitude_deg - lon_span, longitude_deg + lon_span

    cells: dict[tuple[int, int], int] = defaultdict(int)
    centers: dict[tuple[int, int], tuple[float, float]] = {}
    i = 0
    while min_lat + i * lat_step <= max_lat:
        j = 0
        while min_lon + j * lon_step <= max_lon:
            centers[(i, j)] = (min_lat + i * lat_step, min_lon + j * lon_step)
            cells[(i, j)] = 0
            j += 1
        i += 1

    used_sats = 0
    used_passes = 0
    for sat_id in ids:
        sat = session.get(Satellite, sat_id)
        if sat is None or sat.orbit_regime == "GEO":
            continue
        sensor = session.scalar(select(Sensor).where(Sensor.sat_id == sat_id))
        if sensor is None or not sensor.swath_km:
            continue
        try:
            omm, _age = current_omm(session, sat_id)
            earth = satellite_from_omm(omm)
            passes = find_passes(
                earth,
                latitude_deg,
                longitude_deg,
                start,
                end,
                min_elevation_deg=min_elevation_deg,
                step_s=max(60, step_s),
            )
        except (LookupError, ValueError):
            continue
        used_sats += 1
        half_swath = float(sensor.swath_km) / 2.0
        covers_all = half_swath >= radius_km + grid_deg * 111.0
        for item in passes:
            used_passes += 1
            aos = _parse(item["aos_utc"])
            los = _parse(item["los_utc"])
            touched: set[tuple[int, int]] = set()
            moment = aos
            while moment <= los:
                try:
                    point = subpoint(earth, moment)
                except (ValueError, KeyError):
                    moment += timedelta(seconds=step_s)
                    continue
                if covers_all:
                    touched.update(cells)
                else:
                    touched.update(
                        _covered_cells(
                            centers,
                            point["lat_deg"],
                            point["lon_deg"],
                            half_swath,
                        )
                    )
                moment += timedelta(seconds=step_s)
            for cell in touched:
                cells[cell] += 1

    result_cells = [
        {"lat": round(centers[key][0], 4), "lon": round(centers[key][1], 4), "count": count}
        for key, count in cells.items()
        if count > 0
    ]
    result = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "aoi": {"lat": latitude_deg, "lon": longitude_deg, "radius_km": radius_km},
        "window": {"start_utc": _iso(start), "end_utc": _iso(end), "hours": hours},
        "grid_deg": grid_deg,
        "lon_step_deg": lon_step,
        "cells": result_cells,
        "min_count": min((cell["count"] for cell in result_cells), default=0),
        "max_count": max((cell["count"] for cell in result_cells), default=0),
        "satellites_used": used_sats,
        "passes_used": used_passes,
        "coverage_note": "过境覆盖为估算：按传感器幅宽/2 的圆近似，含夜间过境；计数为 24h 内覆盖该格的过境次数。",
        "accuracy_note": ACCURACY_NOTE,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    _CACHE[key] = (time.time(), result)
    return result


def _covered_cells(
    centers: dict[tuple[int, int], tuple[float, float]],
    lat: float,
    lon: float,
    half_swath_km: float,
) -> set[tuple[int, int]]:
    return {
        key
        for key, (cell_lat, cell_lon) in centers.items()
        if _distance_km(lat, lon, cell_lat, cell_lon) <= half_swath_km
    }


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
