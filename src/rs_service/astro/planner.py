"""卫星成图编成求解器。

四个模式共用一条链路：候选筛选 -> 网格 -> 逐星覆盖 -> 贪心 -> 冗余剔除 -> 重访统计。
差异只在目标函数与停止条件。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import Aoi, Satellite, Sensor, SensorMode, TleCurrent, TleRecord
from ..geo import datum
from . import cloud
from . import coverage as cv
from .propagate import satellite_from_omm

ALGO_VERSION = "plan-0.1"
MODES = (
    "min_sats_for_coverage",
    "min_sats_for_revisit",
    "max_coverage_for_n",
    "min_wait_for_n",
)
ACCURACY_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"
MAX_HOURS = 336
EXTEND_LADDER = (72, 168, 336)
POINT_CAP = 8000


class PlanError(Exception):
    def __init__(self, code: str, **extra):
        super().__init__(code)
        self.code = code
        self.extra = extra


@dataclass
class Candidate:
    sat_id: int
    name: str
    sensor_type: str
    mode: str
    resolution_m: float
    swath_km: float
    omm: dict
    mask: np.ndarray | None = None
    hits: list | None = None


@dataclass
class PlanResult:
    data: dict = field(default_factory=dict)


def parse_wkt_polygon(wkt: str) -> list[list[tuple[float, float]]]:
    m = re.search(r"POLYGON\s*\(\((.*?)\)\)", wkt, re.IGNORECASE | re.DOTALL)
    if not m:
        raise PlanError("aoi_invalid", problem="WKT 必须是 POLYGON((lon lat, ...))")
    ring: list[tuple[float, float]] = []
    for chunk in m.group(1).split(","):
        parts = chunk.split()
        if len(parts) < 2:
            raise PlanError("aoi_invalid", problem="坐标解析失败")
        ring.append((float(parts[0]), float(parts[1])))
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    if len(ring) < 4:
        raise PlanError("aoi_invalid", problem="环闭合后少于 4 个点")
    return [ring]


def ring_area_km2(ring: list[tuple[float, float]]) -> float:
    lat0 = math.radians(sum(p[1] for p in ring) / len(ring))
    kx = 111.32 * math.cos(lat0)
    ky = 110.574
    s = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * kx, ring[i][1] * ky
        x2, y2 = ring[i + 1][0] * kx, ring[i + 1][1] * ky
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _province_ring(session: Session):
    from ..db.models import Aoi as AoiModel

    row = session.scalar(select(AoiModel).where(AoiModel.level == "province"))
    if row is None or not row.geom_wkt:
        return None
    return parse_wkt_polygon(row.geom_wkt)[0]


def _lookup_aoi(session: Session, key: str):
    from ..db.models import Aoi as AoiModel

    row = session.scalar(select(AoiModel).where(AoiModel.code == key))
    if row is None:
        row = session.scalar(select(AoiModel).where(AoiModel.adcode == key))
    if row is None:
        exact = session.scalar(select(AoiModel).where(AoiModel.name == key))
        row = exact or session.scalar(select(AoiModel).where(AoiModel.name.like(f"%{key}%")))
    return row


def _rings_overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    return (
        any(datum.point_in_ring(x, y, b) for x, y in a)
        or any(datum.point_in_ring(x, y, a) for x, y in b)
    )


def _overlaps_zhejiang(session: Session, ring: list[tuple[float, float]],
                       province: list[tuple[float, float]] | None) -> bool:
    if province is not None and _rings_overlap(ring, province):
        return True
    rows = session.scalars(
        select(Aoi.geom_wkt).where(Aoi.level == "city", Aoi.adcode.like("33%"))
    ).all()
    for wkt in rows:
        if not wkt:
            continue
        try:
            other = parse_wkt_polygon(wkt)[0]
        except (ValueError, IndexError):
            continue
        if _rings_overlap(ring, other):
            return True
    return False


def resolve_aoi(session: Session, spec: str | list[str]) -> dict:
    specs = [spec] if isinstance(spec, str) else list(spec)
    if not specs or not any(str(x).strip() for x in specs):
        raise PlanError("missing_params", missing=["aoi"])

    province = _province_ring(session)
    rings: list[list[tuple[float, float]]] = []
    items: list[dict] = []

    for one in specs:
        one = str(one).strip()
        if one.upper().startswith("POLYGON"):
            ring = parse_wkt_polygon(one)[0]
            code, name, level = "custom", "自定义范围", "custom"
        else:
            row = _lookup_aoi(session, one)
            if row is None:
                raise PlanError("aoi_not_found", aoi=one)
            if not row.geom_wkt:
                raise PlanError("aoi_invalid", problem="AOI 缺少几何", aoi=one)
            ring = parse_wkt_polygon(row.geom_wkt)[0]
            code, name, level = row.code, row.name, row.level or "city"

        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        centroid_in = province is not None and datum.point_in_ring(cx, cy, province)
        if not centroid_in:
            # 省界数据是大陆单环，不含舟山群岛，质心判断会误杀海岛市。
            # 内置浙江 AOI 直接信任 adcode；自定义 WKT 只要与任一浙江地市相交即可。
            if code != "custom" and str(code).startswith("33"):
                pass
            elif not _overlaps_zhejiang(session, ring, province):
                raise PlanError("aoi_out_of_zhejiang",
                                aoi=one, name=name, centroid=[round(cx, 4), round(cy, 4)],
                                hint="本版本只支持浙江省范围内的市、区县或自定义多边形")

        area = ring_area_km2(ring)
        rings.append(ring)
        items.append({"code": code, "name": name, "level": level, "area_km2": round(area, 1)})

    return {"rings": rings, "area_km2": sum(i["area_km2"] for i in items), "items": items}


def _candidates(
    session: Session, max_res: float, sensor_types: list[str]
) -> list[Candidate]:
    rows = session.execute(
        select(Satellite, Sensor, SensorMode, TleCurrent, TleRecord)
        .join(Sensor, Sensor.sat_id == Satellite.sat_id)
        .join(SensorMode, SensorMode.sat_id == Satellite.sat_id)
        .join(TleCurrent, TleCurrent.sat_id == Satellite.sat_id)
        .join(TleRecord, TleRecord.id == TleCurrent.tle_id)
        .where(
            Satellite.payload_public.is_(True),
            Satellite.orbit_regime == "LEO",
            Sensor.sensor_type.in_(sensor_types),
            SensorMode.resolution_m <= max_res,
        )
    ).all()

    best: dict[int, Candidate] = {}
    for sat, sensor, mode, _cur, tle in rows:
        cand = Candidate(
            sat_id=sat.sat_id, name=sat.name, sensor_type=sensor.sensor_type or "",
            mode=mode.mode_name, resolution_m=mode.resolution_m, swath_km=mode.swath_km,
            omm=json.loads(tle.omm_json or "{}"),
        )
        prev = best.get(sat.sat_id)
        # 满足分辨率的前提下取最宽幅宽；同幅宽取更好分辨率
        if prev is None or (cand.swath_km, -cand.resolution_m) > (prev.swath_km, -prev.resolution_m):
            best[sat.sat_id] = cand
    return sorted(best.values(), key=lambda c: -c.swath_km)


def _plan_grid(rings, area_km2, candidates) -> cv.Grid:
    area_step = cv.adaptive_spacing(area_km2)
    narrow = min((c.swath_km for c in candidates if c.swath_km > 0), default=area_step * 111.32)
    want = min(area_step, max(0.005, narrow / 2.5 / 111.32))
    grid = cv.build_grid(rings, area_km2, want)
    while len(grid) > POINT_CAP:
        want *= 1.3
        grid = cv.build_grid(rings, area_km2, want)
    return grid


def solve(
    session: Session,
    *,
    mode: str,
    aoi: str | list[str],
    max_resolution_m: float,
    target_coverage_pct: float,
    n_sats: int | None = None,
    target_max_gap_hours: float | None = None,
    sensor_types: list[str] | None = None,
    hours: int = 72,
    start: datetime | None = None,
    extend: bool = True,
    persist: bool = True,
    cloud_filter: bool = True,
    cloud_threshold: float = 70.0,
    step_s: int = 30,
) -> dict:
    if mode not in MODES:
        raise PlanError("unsupported_mode", allowed=list(MODES))
    missing = []
    if not aoi:
        missing.append("aoi")
    if not max_resolution_m:
        missing.append("max_resolution_m")
    if target_coverage_pct is None:
        missing.append("target_coverage_pct")
    if mode in ("max_coverage_for_n", "min_wait_for_n") and not n_sats:
        missing.append("n_sats")
    if mode == "min_sats_for_revisit" and not target_max_gap_hours:
        missing.append("target_max_gap_hours")
    if missing:
        raise PlanError("missing_params", missing=missing)

    stypes = sensor_types or ["optical", "sar", "hyperspectral"]
    resolved = resolve_aoi(session, aoi)
    candidates = _candidates(session, max_resolution_m, stypes)
    if not candidates:
        raise PlanError("no_candidate_satellites", filters={"max_resolution_m": max_resolution_m,
                                                            "sensor_types": stypes})
    grid = _plan_grid(resolved["rings"], resolved["area_km2"], candidates)

    t0 = start or datetime.now(UTC)
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=UTC)
    ladder = [h for h in EXTEND_LADDER if h >= hours] or [hours]
    if not extend:
        ladder = [hours]

    cx = float(np.mean(grid.lon))
    cy = float(np.mean(grid.lat))
    cloud_series = cloud.series_for(cy, cx, t0, max(ladder)) if cloud_filter else []
    attempts = []
    chosen: list[Candidate] = []
    coverage_pct = 0.0
    used_hours = hours
    gaps = {}

    for h in ladder:
        used_hours = h
        end = t0 + timedelta(hours=h)
        for c in candidates:
            sat = satellite_from_omm(c.omm)
            c.mask, c.hits = cv.satellite_coverage(sat, grid, t0, end, c.swath_km, step_s=step_s)
            _apply_cloud(c, cloud_series, t0, cloud_threshold)
        chosen, coverage_pct = _greedy(candidates, grid, mode, target_coverage_pct, n_sats, target_max_gap_hours, h * 3600)
        attempts.append({"hours": h, "coverage_pct": round(coverage_pct, 2)})
        ok_cov = coverage_pct >= target_coverage_pct - 1e-9
        ok_gap = True
        if mode == "min_sats_for_revisit" and chosen:
            gaps = _gaps(chosen, grid, h * 3600)
            ok_gap = gaps["p95_hours"] <= (target_max_gap_hours or 0) + 1e-9
        elif chosen:
            gaps = _gaps(chosen, grid, h * 3600)
        if ok_cov and ok_gap:
            break

    if not chosen:
        raise PlanError("target_unreachable", best_coverage_pct=0.0,
                        suggestions=["放宽分辨率要求", "增加传感器类型", "延长窗口"])
    if (mode == "min_sats_for_revisit" and gaps.get("p95_hours") is not None
            and gaps["p95_hours"] > (target_max_gap_hours or 0) + 1e-9):
        raise PlanError(
            "target_unreachable",
            best_coverage_pct=round(coverage_pct, 2),
            best_p95_hours=gaps.get("p95_hours"),
            target_max_gap_hours=target_max_gap_hours,
            suggestions=["增加卫星数量上限（改用 max_coverage_for_n 反查）",
                         "增加传感器类型", "放宽目标最大间隔"],
        )
    if coverage_pct < target_coverage_pct - 1e-9 and mode in ("min_sats_for_coverage", "min_sats_for_revisit"):
        raise PlanError(
            "target_unreachable",
            best_coverage_pct=round(coverage_pct, 2),
            suggestions=["放宽分辨率要求", "增加传感器类型", "延长窗口至 14 天以上"],
        )

    total = max(1, len(grid))
    stride = max(1, total // 2500)
    owner = np.full(len(grid), -1, dtype=int)
    for i, c in enumerate(chosen):
        if c.mask is not None:
            owner[(owner < 0) & c.mask] = i

    sats_out = []
    for i, c in enumerate(chosen):
        pts = np.flatnonzero(owner == i)
        sats_out.append({
            "sat_id": c.sat_id, "name": c.name, "sensor_type": c.sensor_type,
            "mode_used": c.mode, "resolution_m": c.resolution_m, "swath_km": c.swath_km,
            "coverage_pct": round(pts.size / total * 100, 2),
            "solo_coverage_pct": round(float(c.mask.sum()) / total * 100, 2) if c.mask is not None else 0.0,
            "coverage_points": _subsample(grid, pts, stride),
            "footprint": _hull([[float(grid.lon[i]), float(grid.lat[i])] for i in pts]),
        })
    uncovered = _subsample(grid, np.flatnonzero(owner < 0), stride)

    excluded = []
    result_written = _persist(
        session, persist, resolved["items"], chosen, coverage_pct, gaps,
        t0, used_hours, grid, target_coverage_pct,
    )

    return {
        "mode": mode,
        "aoi": resolved["items"],
        "aoi_outline": _outline(resolved["rings"]),
        "grid": {"spacing_deg": grid.spacing_deg, "points": len(grid), "inside": len(grid)},
        "window": {
            "start_utc": t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_utc": (t0 + timedelta(hours=used_hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hours": hours,
            "extended_to_hours": used_hours,
            "attempts": attempts,
        },
        "plan": {
            "sat_count": len(chosen),
            "coverage_pct": round(coverage_pct, 2),
            "feasible": coverage_pct >= target_coverage_pct - 1e-9,
            "sats": sats_out,
            "gap": gaps,
            "target_pct": float(target_coverage_pct),
            "optimal": False,
            "lower_bound_n": _lower_bound(candidates, grid, target_coverage_pct),
            "under_covered_points": gaps.get("under_covered_points", 0),
        },
        "algo_version": ALGO_VERSION,
        "parser_version": _parser_version(),
        "uncovered_points": uncovered,
        "timeline": _timeline(chosen, owner, grid, t0, stride),
        "cloud": {"used": bool(cloud_series), "threshold_pct": cloud_threshold},
        "excluded": excluded,
        "notes": _plan_notes(mode, n_sats, len(chosen), coverage_pct, target_coverage_pct)
        + _excluded_notes(session, stypes)
        + _cloud_notes(cloud_filter, bool(cloud_series), cloud_threshold),
        "persisted": result_written,
    }


def _visit_scores(pool) -> dict[int, int]:
    """每颗星在网格点上的访问次数总和，用于覆盖率已满后按"重访密度"补星。"""
    scores = {}
    for c in pool:
        total = 0
        for ts in c.hits or []:
            if not ts:
                continue
            ts = sorted(set(ts))
            n, prev = 1, ts[0]
            for t in ts[1:]:
                if t - prev > 180.0:
                    n += 1
                prev = t
            total += n
        scores[c.sat_id] = total
    return scores


def _union(chosen, n):
    u = np.zeros(n, dtype=bool)
    for c in chosen:
        u |= c.mask
    return u


def _gaps_of(chosen, grid, window_s):
    return _gaps(chosen, grid, window_s)


def _pick_for_wait(pool, grid, n_sats, window_s, shortlist=12):
    """给定 N 颗、最小化重访间隔：在覆盖增益前 N 名里做贪心，目标是 P95 间隔最小。"""
    if not pool or not n_sats:
        return []
    order = sorted(pool, key=lambda c: -c.mask.sum())[:shortlist]
    chosen: list[Candidate] = []
    best_val = float("inf")
    while len(chosen) < min(n_sats, len(order)):
        pick, pick_val = None, best_val
        for c in order:
            if any(c is x for x in chosen):
                continue
            g = _gaps(chosen + [c], grid, window_s)
            v = g.get("p95_hours")
            v = float("inf") if v is None else v
            if v < pick_val:
                pick, pick_val = c, v
        if pick is None:
            pick = next(c for c in order if not any(c is x for x in chosen))
        chosen.append(pick)
        best_val = pick_val
    return chosen


def _greedy(candidates, grid, mode, target_pct, n_sats, target_gap_h, window_s):
    total = max(1, len(grid))
    target_pts = target_pct / 100.0 * total
    pool = [c for c in candidates if c.mask is not None and c.mask.any()]
    covered = np.zeros(len(grid), dtype=bool)
    chosen: list[Candidate] = []

    if mode == "min_wait_for_n":
        chosen = _pick_for_wait(pool, grid, n_sats or 1, window_s)
        return chosen, float(_union(chosen, len(grid)).sum()) / total * 100

    limit = n_sats if mode == "max_coverage_for_n" else len(pool)
    scores = _visit_scores(pool) if mode == "min_sats_for_revisit" else {}
    while len(chosen) < limit:
        best, best_gain = None, 0
        for c in pool:
            if any(c is x for x in chosen):
                continue
            gain = int((c.mask & ~covered).sum())
            if gain > best_gain:
                best, best_gain = c, gain
        if best is None or best_gain == 0:
            if mode == "min_sats_for_revisit":
                rest = [c for c in pool if not any(c is x for x in chosen)]
                if not rest:
                    break
                best = max(rest, key=lambda c: scores.get(c.sat_id, 0))
                chosen.append(best)
                covered |= best.mask
                g = _gaps(chosen, grid, window_s)
                if g.get("p95_hours") is not None and g["p95_hours"] <= target_gap_h + 1e-9:
                    break
                continue
            break
        chosen.append(best)
        covered |= best.mask
        if mode == "min_sats_for_coverage" and covered.sum() >= target_pts:
            break
        if mode == "min_sats_for_revisit" and covered.sum() >= target_pts:
            g = _gaps(chosen, grid, window_s)
            if g.get("p95_hours") is not None and g["p95_hours"] <= target_gap_h + 1e-9:
                break

    if mode == "min_sats_for_coverage":
        for c in list(chosen):
            rest = [x for x in chosen if x is not c]
            if not rest:
                continue
            u = _union(rest, len(grid))
            if u.sum() >= target_pts:
                chosen = rest
    return chosen, float(_union(chosen, len(grid)).sum()) / total * 100


def _gaps(chosen, grid, window_s):
    n = len(grid)
    under = 0
    per_point = [[] for _ in range(n)]
    for c in chosen:
        for gi, ts in enumerate(c.hits or []):
            if ts:
                per_point[gi].extend(ts)
    mrt, p50, p95, mx, und = [], [], [], [], 0
    for gi in range(n):
        m, a, b, x, u = cv.gaps_from_times(per_point[gi], window_s)
        if not per_point[gi]:
            under += 1
            continue
        mrt.append(m)
        p50.append(a)
        p95.append(b)
        mx.append(x)
        if u:
            und += 1
    if not mrt:
        return {"mrt_hours": None, "p50_hours": None, "p95_hours": None,
                "max_hours": None, "under_covered_points": n}
    return {
        "mrt_hours": round(float(np.mean(mrt)) / 3600, 2),
        "p50_hours": round(float(np.mean(p50)) / 3600, 2),
        "p95_hours": round(float(np.mean(p95)) / 3600, 2),
        "max_hours": round(float(np.max(mx)) / 3600, 2),
        "under_covered_points": under + und,
    }



def _cloud_notes(enabled: bool, ok: bool, threshold: float) -> list[str]:
    if not enabled:
        return ["云量过滤已关闭：本次覆盖率是几何覆盖，未剔除厚云遮挡。"]
    if not ok:
        return ["云量数据获取失败：本次覆盖率按几何覆盖计算，未剔除厚云遮挡。"]
    return [f"光学卫星按 Open-Meteo 逐小时云量过滤：云量 > {threshold:.0f}% 的访问不计入覆盖；SAR 不受影响。"]


def _apply_cloud(c: Candidate, series, t0: datetime, threshold: float) -> None:
    """光学星按访问时刻的云量剔除；SAR 与无云量数据时原样保留。"""
    if not series or c.sensor_type != "optical" or c.hits is None:
        return
    base = series[0][0]
    values = [v for _, v in series]
    kept: list[list[float]] = []
    for ts in c.hits:
        good = []
        for t in ts:
            idx = int((t0.timestamp() + t - base.timestamp()) // 3600)
            if 0 <= idx < len(values) and values[idx] <= threshold:
                good.append(t)
        kept.append(good)
    c.hits = kept
    c.mask = np.array([len(v) > 0 for v in kept], dtype=bool)


def _hull(points: list[list[float]]) -> list[list[float]]:
    """Andrew monotone chain；点少于 3 个时返回空。"""
    pts = sorted({(round(x, 4), round(y, 4)) for x, y in points})
    if len(pts) < 3:
        return []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p_ in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p_) <= 0:
            lower.pop()
        lower.append(p_)
    upper: list = []
    for p_ in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p_) <= 0:
            upper.pop()
        upper.append(p_)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return []
    step = max(1, len(hull) // 48)
    return [[round(x, 4), round(y, 4)] for x, y in hull[::step]]


def _timeline(chosen, owner, grid, t0, stride, bucket_s: int = 30, max_events: int = 120):
    """按"首次被覆盖的时刻"分桶，生成回放事件（每桶是该时刻新增的格点）。"""
    events: dict[tuple[int, int], list[int]] = {}
    for i, c in enumerate(chosen):
        idx = np.flatnonzero(owner == i)
        if idx.size == 0 or not c.hits:
            continue
        for gi in idx:
            hits = c.hits[gi]
            if not hits:
                continue
            b = int(min(hits) // bucket_s)
            events.setdefault((b, c.sat_id), []).append(int(gi))
    out = []
    for (b, sat_id), gis in sorted(events.items()):
        sub = np.array(gis)[::max(1, stride)]
        name = next((c.name for c in chosen if c.sat_id == sat_id), str(sat_id))
        out.append({
            "t_utc": (t0 + timedelta(seconds=b * bucket_s)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sat_id": sat_id,
            "name": name,
            "points": [[round(float(grid.lon[i]), 4), round(float(grid.lat[i]), 4)] for i in sub],
        })
    if len(out) > max_events:
        step = len(out) / max_events
        out = [out[int(i * step)] for i in range(max_events)]
    return out


def _persist(session, enabled, items, chosen, coverage_pct, gaps, t0, hours, grid, target) -> bool:
    """写 coverage_snapshot。自定义 AOI 没有 aoi 行时跳过，返回 False。"""
    if not enabled or not items or items[0].get("code") in (None, "custom"):
        return False
    from ..db.models import CoverageSnapshot

    row = session.scalar(select(Aoi).where(Aoi.code == items[0]["code"]))
    if row is None:
        return False
    sat_hash = hashlib.sha1(",".join(str(c.sat_id) for c in chosen).encode()).hexdigest()[:32]
    session.add(CoverageSnapshot(
        aoi_id=row.id,
        window_start_utc=t0.strftime("%Y-%m-%dT%H:%M:%SZ"),
        window_end_utc=(t0 + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        sat_set_hash=sat_hash,
        grid_spacing_deg=grid.spacing_deg,
        k_min=1,
        temporal_coverage_pct=float(target),
        area_coverage_pct=round(coverage_pct, 2),
        mrt_s=(gaps or {}).get("mrt_hours") and round(gaps["mrt_hours"] * 3600, 1),
        art_s=(gaps or {}).get("p95_hours") and round(gaps["p95_hours"] * 3600, 1),
        algo_version=ALGO_VERSION,
    ))
    session.flush()
    return True


def _excluded_notes(session: Session, sensor_types: list[str]) -> list[str]:
    from ..db.models import Sensor as SensorModel

    geo = session.scalar(
        select(func.count()).select_from(Satellite)
        .join(SensorModel, SensorModel.sat_id == Satellite.sat_id)
        .where(Satellite.orbit_regime == "GEO", SensorModel.sensor_type.in_(sensor_types))
    ) or 0
    notes = []
    if geo:
        notes.append(
            f"已排除 {geo} 颗静止轨道成像星（GF-4 / GF-13 / FY 系列等）：当前足迹模型不支持凝视，"
            "重访指标仅对 LEO 成立。"
        )
    return notes


def _plan_notes(mode, n_sats, chosen_n, cov, target) -> list[str]:
    notes = []
    if mode in ("max_coverage_for_n", "min_wait_for_n") and n_sats and chosen_n < n_sats:
        notes.append(f"给定 {n_sats} 颗，但用 {chosen_n} 颗已达最大增益，再增加卫星不再提升覆盖。")
    if mode == "max_coverage_for_n" and cov >= 99.5:
        notes.append("已达到 100% 覆盖，增加卫星数量不会进一步提升覆盖率。")
    return notes


def _parser_version() -> str:
    from ..sync.sensor_modes import PARSER_VERSION

    return PARSER_VERSION


def _lower_bound(candidates, grid, target_pct):
    total = max(1, len(grid))
    target_pts = target_pct / 100.0 * total
    best_gain = max((int(c.mask.sum()) for c in candidates if c.mask is not None), default=0)
    if best_gain <= 0:
        return 0
    return max(1, math.ceil(target_pts / best_gain))


def _subsample(grid: cv.Grid, idx: np.ndarray, stride: int = 1) -> list[list[float]]:
    """按固定步长抽稀，保证各卫星的点密度一致（按数量抽稀会让覆盖多的星反而更稀）。"""
    if idx.size == 0:
        return []
    idx = idx[::max(1, stride)]
    return [[round(float(grid.lon[i]), 4), round(float(grid.lat[i]), 4)] for i in idx]


def _outline(rings, max_pts: int = 260) -> list[list[float]]:
    """AOI 外环抽稀，供报告画底图。"""
    best = max(rings, key=len)
    if len(best) > max_pts:
        step = len(best) / max_pts
        best = [best[int(i * step)] for i in range(max_pts)]
    return [[round(float(x), 4), round(float(y), 4)] for x, y in best]
