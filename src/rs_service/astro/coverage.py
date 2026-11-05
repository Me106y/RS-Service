"""区域覆盖与重访计算。

几何口径（与 imaging-plan 契约一致）：

- AOI 用外环多边形表示，网格间距按面积自适应；
- 足迹用「旋转矩形」：以星下点轨迹为轴，半幅宽为半径的走廊，
  等价于格点到轨迹线段的垂直距离 <= 幅宽/2；两端为圆头，误差 <= 幅宽/2；
- 重访间隔在窗口内计算，首尾不闭环；窗口内只有一次访问的格点记 gap=窗口长度并计入
  under_covered_points。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from skyfield.api import EarthSatellite

from .propagate import timescale

ALGO_VERSION = "cov-0.1"
EARTH_R_KM = 6371.0088


@dataclass
class Grid:
    lon: np.ndarray
    lat: np.ndarray
    spacing_deg: float
    points: int

    def __len__(self) -> int:
        return int(self.lon.size)


def adaptive_spacing(area_km2: float, target_cells: int = 120) -> float:
    """按 AOI 面积自适应网格：clamp(sqrt(area/target), 0.02°, 0.2°)。"""
    if area_km2 <= 0:
        return 0.1
    cell_km = math.sqrt(area_km2 / target_cells)
    deg = cell_km / 111.32
    return float(min(0.2, max(0.02, round(deg, 3))))


def polygon_bbox(rings: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(xs), min(ys), max(xs), max(ys)


def _points_in_rings(lon: np.ndarray, lat: np.ndarray, rings: list[list[tuple[float, float]]]) -> np.ndarray:
    inside = np.zeros(lon.shape, dtype=bool)
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        xs = np.array([p[0] for p in ring], dtype=float)
        ys = np.array([p[1] for p in ring], dtype=float)
        j = np.roll(np.arange(n), 1)
        hit = np.zeros(lon.shape, dtype=bool)
        for i in range(n):
            y1, y2 = ys[j[i]], ys[i]
            if y1 == y2:
                continue
            cond = (y1 > lat) != (y2 > lat)
            xint = (xs[i] - xs[j[i]]) * (lat - y1) / (y2 - y1) + xs[j[i]]
            hit ^= cond & (lon < xint)
        inside |= hit
    return inside


def build_grid(
    rings: list[list[tuple[float, float]]],
    area_km2: float,
    spacing_deg: float | None = None,
) -> Grid:
    step = spacing_deg or adaptive_spacing(area_km2)
    minx, miny, maxx, maxy = polygon_bbox(rings)
    xs = np.arange(minx + step / 2, maxx, step)
    ys = np.arange(miny + step / 2, maxy, step)
    gx, gy = np.meshgrid(xs, ys)
    lon = gx.ravel()
    lat = gy.ravel()
    keep = _points_in_rings(lon, lat, rings)
    return Grid(lon=lon[keep], lat=lat[keep], spacing_deg=step, points=int(keep.sum()))


def propagate_track(
    sat: EarthSatellite, start: datetime, end: datetime, step_s: int = 30
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """向量化推星下点轨迹，返回 (秒偏移, 纬度, 经度)。"""
    total_s = int((end - start).total_seconds())
    offsets = np.arange(0, total_s + step_s, step_s, dtype=float)
    base = start if start.tzinfo else start.replace(tzinfo=UTC)
    stamps = [base + timedelta(seconds=float(o)) for o in offsets]
    ts = timescale()
    t = ts.from_datetimes(stamps)
    sub = sat.at(t).subpoint()
    return offsets, sub.latitude.degrees, sub.longitude.degrees


def _local_km(lon: np.ndarray, lat: np.ndarray, lon0: float, lat0: float) -> tuple[np.ndarray, np.ndarray]:
    x = (lon - lon0) * 111.32 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 110.574
    return x, y


def _segment_distance_km(
    glon: np.ndarray,
    glat: np.ndarray,
    lon0: float,
    lat0: float,
    lon1: float,
    lat1: float,
    origin: tuple[float, float],
) -> np.ndarray:
    """格点到轨迹线段的垂直距离（km）。"""
    ox, oy = origin
    px, py = _local_km(glon, glat, ox, oy)
    ax, ay = _local_km(np.array([lon0]), np.array([lat0]), ox, oy)
    bx, by = _local_km(np.array([lon1]), np.array([lat1]), ox, oy)
    ax, ay, bx, by = ax[0], ay[0], bx[0], by[0]
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 1e-9:
        return np.hypot(px - ax, py - ay)
    t = np.clip(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
    return np.hypot(px - (ax + t * dx), py - (ay + t * dy))


def satellite_coverage(
    sat: EarthSatellite,
    grid: Grid,
    start: datetime,
    end: datetime,
    swath_km: float,
    step_s: int = 30,
    pad_km: float = 200.0,
) -> tuple[np.ndarray, list[list[float]]]:
    """单星对网格的覆盖：返回 (布尔掩码, 每个格点被覆盖的时刻偏移秒)。"""
    offs, lat, lon = propagate_track(sat, start, end, step_s=step_s)
    hits: list[list[float]] = [[] for _ in range(len(grid))]
    mask = np.zeros(len(grid), dtype=bool)
    if lat.size < 2:
        return mask, hits

    minx, miny, maxx, maxy = polygon_bbox([[(grid.lon.min(), grid.lat.min()),
                                            (grid.lon.max(), grid.lat.max()),
                                            (grid.lon.min(), grid.lat.max())]])
    dlat = pad_km / 110.574
    dlon = pad_km / (111.32 * math.cos(math.radians((miny + maxy) / 2)))
    mid_lat = (lat[:-1] + lat[1:]) / 2
    mid_lon = (lon[:-1] + lon[1:]) / 2
    near = (
        (mid_lat > miny - dlat) & (mid_lat < maxy + dlat)
        & (mid_lon > minx - dlon) & (mid_lon < maxx + dlon)
    )
    idx = np.flatnonzero(near)
    origin = (float(grid.lon.mean()), float(grid.lat.mean()))
    half = swath_km / 2.0

    for k in idx:
        d = _segment_distance_km(grid.lon, grid.lat, lon[k], lat[k], lon[k + 1], lat[k + 1], origin)
        sel = d <= half
        if not sel.any():
            continue
        mask |= sel
        t_mid = float(offs[k] + (offs[k + 1] - offs[k]) / 2)
        for gi in np.flatnonzero(sel):
            hits[gi].append(t_mid)
    return mask, hits


def gaps_from_times(times: list[float], window_s: float, dwell_s: float = 180.0) -> tuple[float, float, float, float, bool]:
    """由覆盖时刻算 (mrt, p50, p95, max, under_covered)。

    同一颗星一次过境会在网格点上连续命中几十个采样点，先把间隔小于 dwell_s 的
    相邻时刻并成一次访问，再统计访问之间的间隔。
    """
    if not times:
        return window_s, window_s, window_s, window_s, True
    uniq = sorted(set(times))
    visits: list[float] = []
    for t in uniq:
        if not visits or t - visits[-1] > dwell_s:
            visits.append(t)
    if len(visits) < 2:
        return window_s, window_s, window_s, window_s, True
    gaps = np.diff(np.array(visits, dtype=float))
    return (
        float(gaps.mean()),
        float(np.percentile(gaps, 50)),
        float(np.percentile(gaps, 95)),
        float(gaps.max()),
        False,
    )
