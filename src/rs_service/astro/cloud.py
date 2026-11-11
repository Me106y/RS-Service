"""云量：Open-Meteo 逐小时 cloud_cover（需求 §4.8）。

只用于给光学卫星的每次访问打折——SAR 不看云。取不到就返回 None，
调用方不做过滤并在 notes 里说明，绝不假装"云量可用"。
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

FORECAST = "https://api.open-meteo.com/v1/forecast"
CACHE: dict[tuple, list[tuple[datetime, float]]] = {}


def _fetch(lat: float, lon: float, start: datetime, hours: int) -> list[tuple[datetime, float]]:
    key = (round(lat, 2), round(lon, 2), start.strftime("%Y-%m-%dT%H"), hours)
    if key in CACHE:
        return CACHE[key]
    params = {
        "latitude": f"{lat:.3f}",
        "longitude": f"{lon:.3f}",
        "hourly": "cloud_cover",
        "forecast_days": min(16, max(2, math.ceil(hours / 24) + 1)),
        "timezone": "UTC",
    }
    url = FORECAST + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return []
    times = payload.get("hourly", {}).get("time", [])
    values = payload.get("hourly", {}).get("cloud_cover", [])
    series = []
    for t, v in zip(times, values, strict=False):
        if v is None:
            continue
        series.append((datetime.fromisoformat(t).replace(tzinfo=UTC), float(v)))
    CACHE[key] = series
    return series


def series_for(lat: float, lon: float, start: datetime, hours: int):
    """返回按小时排序的 (UTC 时刻, 云量%) 序列；失败返回空列表。"""
    return _fetch(lat, lon, start, hours)


def _parse_series(item: dict) -> list[tuple[datetime, float]]:
    times = item.get("hourly", {}).get("time", [])
    values = item.get("hourly", {}).get("cloud_cover", [])
    series = []
    for t, v in zip(times, values, strict=False):
        if v is None:
            continue
        series.append((datetime.fromisoformat(t).replace(tzinfo=UTC), float(v)))
    return series


def series_for_many(
    points: list[tuple[float, float]], start: datetime, hours: int
) -> list[list[tuple[datetime, float]]]:
    """一次请求多个坐标的云量序列；返回顺序与 ``points`` 一致。

    网格代表点通常有几十个，逐点请求会被 Open-Meteo 限流；多坐标接口把
    ``latitude`` / ``longitude`` 用逗号拼起来，一次返回数组。
    """

    if not points:
        return []
    cached = [
        CACHE.get((round(lat, 2), round(lon, 2), start.strftime("%Y-%m-%dT%H"), hours))
        for lat, lon in points
    ]
    if all(item is not None for item in cached):
        return list(cached)  # type: ignore[arg-type]
    params = {
        "latitude": ",".join(f"{lat:.3f}" for lat, _ in points),
        "longitude": ",".join(f"{lon:.3f}" for _, lon in points),
        "hourly": "cloud_cover",
        "forecast_days": min(16, max(2, math.ceil(hours / 24) + 1)),
        "timezone": "UTC",
    }
    url = FORECAST + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return [[] for _ in points]
    if isinstance(payload, dict):
        payload = [payload]
    result: list[list[tuple[datetime, float]]] = []
    for index, (lat, lon) in enumerate(points):
        key = (round(lat, 2), round(lon, 2), start.strftime("%Y-%m-%dT%H"), hours)
        series = _parse_series(payload[index]) if index < len(payload) else []
        CACHE[key] = series
        result.append(series)
    return result


def cloud_at(series, when: datetime) -> float | None:
    """线性插值取某个时刻的云量。"""
    if not series:
        return None
    best = min(series, key=lambda kv: abs((kv[0] - when).total_seconds()))
    if abs((best[0] - when).total_seconds()) > 5400:
        return None
    return best[1]


def dilate(start: datetime, hours: int, step_min: int = 10) -> list[datetime]:
    out = []
    cur = start
    end = start + timedelta(hours=hours)
    while cur <= end:
        out.append(cur)
        cur += timedelta(minutes=step_min)
    return out
