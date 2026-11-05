"""GCJ-02 -> WGS84 转换与折线简化。

高德/DataV 的边界是 GCJ-02，直接当 WGS84 用会有 300–600 m 偏移（需求 §4.7）。
"""

from __future__ import annotations

import math

A = 6378245.0
EE = 0.00669342162296594323


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float, iterations: int = 4) -> tuple[float, float]:
    wlon, wlat = lon, lat
    for _ in range(iterations):
        glon, glat = wgs84_to_gcj02(wlon, wlat)
        wlon += lon - glon
        wlat += lat - glat
    return wlon, wlat


def _perp(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def simplify_ring(ring: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker，递归实现；返回闭合环。"""
    if len(ring) < 4:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True

    def walk(lo: int, hi: int) -> None:
        if hi <= lo + 1:
            return
        best, best_d = -1, -1.0
        for i in range(lo + 1, hi):
            d = _perp(ring[i], ring[lo], ring[hi])
            if d > best_d:
                best, best_d = i, d
        if best_d > tolerance:
            keep[best] = True
            walk(lo, best)
            walk(best, hi)

    walk(0, len(ring) - 1)
    out = [ring[i] for i in range(len(ring)) if keep[i]]
    if out[0] != out[-1]:
        out.append(out[0])
    return out


def ring_area_km2(ring: list[tuple[float, float]]) -> float:
    if len(ring) < 3:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.320 * math.cos(math.radians(lat0)), 110.574
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * kx, ring[i][1] * ky
        x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def point_in_ring(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > lat) != (y2 > lat):
            xint = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xint:
                inside = not inside
    return inside
