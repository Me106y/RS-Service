"""候选与排除：为报告逐颗解释"为什么没用它"。

求解定稿后运行，只做覆盖并集重算与粗筛诊断，**不改变**选星结果
（``selected_sat_ids`` / ``feasible`` / ``optimality`` 一律不动）。

口径（与 ../SIRIAgent/docs/PHASE2-REQUIREMENTS.md §11 Q8 一致）：

* 闭环：``目录星数 = 入选 + 备选 + 未采用``，每颗星有且只有一条主原因。
* 备选（候补）：替换某一颗入选星后仍是 100%，只是更贵；不逐星重跑 ILP，
  用覆盖并集重算判定。
* 零覆盖诊断以 AOI 形心为准、默认 60 s 步长粗筛，只用于解释，不参与选星。
* 云量三态：有云量且 > 阈值剔除 / 有云量但保留 / 无云量数据（不剔除但标明）。
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

GENERATOR = "rs_service.candidate_review/1"

# 主原因优先级：越靠前越先解释（同一颗星只取一条）。
REASON_ORDER = (
    "data_unavailable",
    "no_track",
    "elevation_below_threshold",
    "night_only",
    "geometry_miss",
    "cloud_dropped",
    "insufficient",
    "redundant",
    "equivalent_tie",
    "alternate",
    "selected",
    "not_evaluated",
)

REASON_LABELS = {
    "data_unavailable": "数据不可用",
    "no_track": "轨迹未经过 AOI",
    "elevation_below_threshold": "仰角不足",
    "night_only": "仅夜间过境",
    "geometry_miss": "轨迹经过但视场未覆盖",
    "cloud_dropped": "访问被云量剔除",
    "insufficient": "能力不足（达不到重访目标）",
    "redundant": "冗余（加入后无新增达标格）",
    "equivalent_tie": "等价解（与推荐解同星数同达标）",
    "alternate": "备选（可替换某颗入选星）",
    "selected": "入选",
    "not_evaluated": "未评估（解释段超时降级）",
}

ELEVATION_MIN_DEG = 10.0
OPTICAL_SUN_MIN_DEG = 10.0


def _evaluate(
    store: Any,
    indices: Iterable[int],
    mrt_target_s: float,
    min_simultaneous: int,
) -> Any:
    """用 vendor 的窗口级指标复算一个组合（与选星器同一判据）。"""

    import regional_selection  # noqa: PLC0415 - vendor 模块，按需导入

    return regional_selection._evaluate(  # noqa: SLF001 - 同仓 vendor 复核
        store,
        tuple(sorted({int(item) for item in indices})),
        float(mrt_target_s),
        int(min_simultaneous),
    )


def _qualified_count(metrics: Any) -> int:
    return int(metrics.city.qualified_grid_count or 0)


def _qualified_pct(metrics: Any) -> float:
    return float(metrics.city.qualified_area_pct or 0.0)


def _sat_norad(sat: Any) -> int:
    """兼容包装类与裸 EarthSatellite 的 NORAD 编号提取。"""

    value = getattr(sat, "norad_id", None)
    if value is None:
        value = getattr(getattr(sat, "model", None), "satnum", None)
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _orbit_of(sat: Any) -> Any:
    """取真正的天空场轨道对象；包装类没有实现减法等特殊方法。"""

    orbit = getattr(sat, "orbit", None)
    return orbit if orbit is not None else sat


def _timescale() -> Any:
    """Skyfield 时间尺度按需构造并复用（避免每颗星重建）。"""

    global _TS
    try:
        return _TS  # type: ignore[name-defined]
    except NameError:
        pass
    from skyfield.api import load  # noqa: PLC0415 - 只在诊断分支用到

    _TS = load.timescale(builtin=True)
    return _TS


def _time_axis(start: datetime, hours: float, step_s: int) -> Any:
    ts = _timescale()
    total = max(step_s, int(round(hours * 3600.0)))
    return ts.utc(
        start.year,
        start.month,
        start.day,
        start.hour,
        start.minute,
        range(0, total + 1, int(step_s)),
    )


def _sun_altitude_deg(times: Any, index: int, lat: float, lon: float) -> float | None:
    """目标地面太阳高度（Meeus 低精度，与覆盖引擎同一公式）。"""

    try:
        import numpy as np  # noqa: PLC0415
        import sensor_geometry as sg  # noqa: PLC0415 - vendor 模块
    except Exception:  # noqa: BLE001 - 诊断失败不影响主流程
        return None
    try:
        moment = times[index]
        # vendor 的太阳/旋转函数按 (T,3) 数组实现，标量要包成长度 1 的数组。
        jd = np.array([float(moment.tt)])
        sun_eci = np.asarray(sg.sun_vector_eci(jd), dtype=float)
        gmst = np.array([math.radians(float(moment.gmst) * 15.0)])
        sun_ecef = np.asarray(sg.rot_z_array(gmst, sun_eci), dtype=float).reshape(3)
        norm = float(np.linalg.norm(sun_ecef))
        if norm <= 0.0:
            return None
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        up = np.array(
            [math.cos(lat_r) * math.cos(lon_r), math.cos(lat_r) * math.sin(lon_r), math.sin(lat_r)]
        )
        sine = float(np.dot(sun_ecef / norm, up))
        return math.degrees(math.asin(max(-1.0, min(1.0, sine))))
    except Exception:  # noqa: BLE001 - 诊断失败不影响主流程
        return None


def diagnose_no_access(
    sat: Any,
    lat: float,
    lon: float,
    start: datetime,
    hours: float,
    *,
    step_s: int = 60,
) -> dict[str, Any]:
    """零覆盖卫星的粗筛诊断：最高仰角与峰值时刻太阳高度。

    只回答"有没有过境、仰角够不够、是不是天黑了"，不重算足迹。
    返回空字典表示诊断失败（调用方按"未评估"处理）。
    """

    try:
        from skyfield.api import wgs84  # noqa: PLC0415

        site = wgs84.latlon(float(lat), float(lon), elevation_m=10.0)
        orbit = _orbit_of(sat)
        axis = _time_axis(start, hours, step_s)
        altitude, _azimuth, _distance = (orbit - site).at(axis).altaz()
        degrees = [float(item) for item in altitude.degrees]
        peak = max(range(len(degrees)), key=degrees.__getitem__)
        peak_deg = degrees[peak]
        # 在粗筛峰值附近用 10 s 步长精修，避免 60 s 采样低估仰角。
        refined = peak
        try:
            ts = _timescale()
            moment = axis[peak]
            fine = ts.utc(
                *moment.utc[:5],
                [moment.utc[5] - 30.0, moment.utc[5] - 20.0, moment.utc[5] - 10.0,
                 moment.utc[5], moment.utc[5] + 10.0, moment.utc[5] + 20.0,
                 moment.utc[5] + 30.0],
            )
            fine_alt, _fa, _fd = (orbit - site).at(fine).altaz()
            fine_deg = [float(item) for item in fine_alt.degrees]
            best = max(range(len(fine_deg)), key=fine_deg.__getitem__)
            if fine_deg[best] > peak_deg:
                peak_deg = fine_deg[best]
                refined = peak
        except Exception:  # noqa: BLE001 - 精修失败沿用粗筛值
            refined = peak
        sun_deg = _sun_altitude_deg(axis, refined, float(lat), float(lon))
        return {
            "max_elevation_deg": round(peak_deg, 1),
            "sun_at_peak_deg": None if sun_deg is None else round(sun_deg, 1),
            "step_s": int(step_s),
        }
    except Exception:  # noqa: BLE001 - 诊断失败不影响主流程
        return {}


def _cloud_counts(cloud_stats: dict[str, Any], sat_index: int) -> dict[str, int] | None:
    per_sat = (cloud_stats or {}).get("per_sat") or {}
    item = per_sat.get(str(sat_index)) or per_sat.get(sat_index)
    if not item:
        return None
    return {
        "dropped": int(item.get("dropped", 0)),
        "clear": int(item.get("clear", 0)),
        "no_data": int(item.get("no_data", 0)),
    }


def _classify(
    *,
    selected: bool,
    access_count: int,
    standalone_ok: bool,
    marginal_cells: int,
    replaces: Sequence[str],
    tie: bool,
    cloud: dict[str, int] | None,
    diagnosis: dict[str, Any],
    unavailable: str | None,
    target_met: bool = True,
    swath_km: float | None = None,
) -> tuple[str, str]:
    """返回 (主原因码, 一句依据)。优先级见 REASON_ORDER。"""

    if unavailable:
        return "data_unavailable", unavailable
    if selected:
        return "selected", "推荐解采用"
    if cloud and cloud["dropped"] > 0 and cloud["clear"] + cloud["no_data"] == 0:
        return "cloud_dropped", f"{cloud['dropped']} 次访问全部 >70% 云量被剔除"
    if access_count == 0:
        if not diagnosis:
            return "geometry_miss", "窗口内无覆盖窗口，诊断不可用"
        elevation = diagnosis.get("max_elevation_deg")
        if elevation is None:
            return "geometry_miss", "窗口内无覆盖窗口"
        if elevation < 0.0:
            return "no_track", f"最高仰角 {elevation:g}°（在地平线以下，轨迹未经过 AOI）"
        if elevation < ELEVATION_MIN_DEG:
            return "elevation_below_threshold", f"最高仰角 {elevation:g}° < {ELEVATION_MIN_DEG:g}°"
        sun_deg = diagnosis.get("sun_at_peak_deg")
        if sun_deg is not None and sun_deg < OPTICAL_SUN_MIN_DEG:
            return "night_only", f"有 {elevation:g}° 过境，但太阳高度 {sun_deg:g}° < {OPTICAL_SUN_MIN_DEG:g}°"
        if swath_km:
            return (
                "geometry_miss",
                f"有 {elevation:g}° 过境（幅宽 {swath_km:g} km），没有网格代表点落入视场",
            )
        return "geometry_miss", f"有 {elevation:g}° 过境，视场未覆盖网格"
    if standalone_ok or replaces:
        tie_label = "与推荐解同星数、同达标（另一组最优解）" if tie else "替换后仍 100%"
        if tie:
            return "equivalent_tie", tie_label
        return "alternate", tie_label
    if marginal_cells <= 0:
        return "redundant", "加入已选集合后达标格增量 0"
    if not target_met:
        return "insufficient", f"加入已选集合可多覆盖 {marginal_cells} 格达标，但本次最佳努力方案未纳入"
    return "insufficient", f"加入已选集合仅新增 {marginal_cells} 格达标，仍达不到目标"


def _verdict(reason: str) -> str:
    if reason == "selected":
        return "selected"
    if reason in {"alternate", "equivalent_tie"}:
        return "candidate"
    return "excluded"


def build_candidate_review(
    *,
    store: Any,
    profiles: Sequence[dict[str, Any]],
    catalog: Sequence[dict[str, Any]],
    selected_indices: Sequence[int],
    selected_ids: Sequence[int],
    cloud_stats: dict[str, Any] | None = None,
    mrt_target_s: float | None,
    target_area_pct: float,
    min_simultaneous: int = 1,
    greedy_ids: Sequence[int] = (),
    ilp_ids: Sequence[int] = (),
    aoi_point: tuple[float, float] | None = None,
    horizon_h: float = 0.0,
    deadline_s: float = 300.0,
    unavailable_by_id: dict[int, str] | None = None,
) -> dict[str, Any]:
    """构造 ``plan.json.candidate_review``。"""

    profiles_by_id = {int(item["norad_id"]): item for item in profiles}
    selected = tuple(sorted({int(item) for item in selected_indices}))
    selected_set = set(selected)
    selected_ids = [int(item) for item in selected_ids]
    greedy = {int(item) for item in greedy_ids}
    ilp = {int(item) for item in ilp_ids}
    dead = set(greedy) ^ set(ilp)
    cloud_stats = cloud_stats or {}

    deadline = time.monotonic() + max(0.0, float(deadline_s))
    degraded = False

    base = _evaluate(store, selected, mrt_target_s, min_simultaneous) if mrt_target_s else None
    base_cells = _qualified_count(base) if base is not None else 0
    base_pct = _qualified_pct(base) if base is not None else 0.0
    # 目标面积对应的网格数：100% 即全部网格。
    total_cells = int(store.n_cells)
    target_cells = int(round(total_cells * target_area_pct / 100.0))
    sat_ids = {index: _sat_norad(sat) for index, sat in enumerate(store.satellites)}
    # 极端情况下 store 里的对象没有 NORAD 编号，用推荐解回填，保证 replaces 可读。
    for position, slot in enumerate(selected):
        if sat_ids.get(slot, -1) < 0 and position < len(selected_ids):
            sat_ids[slot] = selected_ids[position]

    attempts: dict[tuple[int, ...], Any] = {}

    def metrics_for(indices: Iterable[int]) -> Any:
        key = tuple(sorted({int(item) for item in indices}))
        if key not in attempts:
            attempts[key] = _evaluate(store, key, mrt_target_s, min_simultaneous)
        return attempts[key]

    cells_by_sat: dict[int, int] = {}
    windows_by_sat: dict[int, int] = {}
    for (sat_index, _grid_index), windows in store.windows.items():
        cells_by_sat[int(sat_index)] = cells_by_sat.get(int(sat_index), 0) + 1
        windows_by_sat[int(sat_index)] = windows_by_sat.get(int(sat_index), 0) + len(windows)

    rows: list[dict[str, Any]] = []
    for sat_index, sat in enumerate(store.satellites):
        norad = _sat_norad(sat)
        profile = profiles_by_id.get(norad, {})
        covered = cells_by_sat.get(sat_index, 0)
        access = windows_by_sat.get(sat_index, 0)
        standalone_ok = False
        marginal = 0
        replaces: list[int] = []
        cloud = _cloud_counts(cloud_stats, sat_index)
        diagnosis: dict[str, Any] = {}
        if time.monotonic() > deadline:
            degraded = True
            if sat_index in selected_set:
                reason, evidence, verdict = "selected", "推荐解采用", "selected"
            else:
                reason = "not_evaluated"
                evidence = "解释段超过防呆上限，未完成评估"
                verdict = "excluded"
            rows.append(
                {
                    "sat_id": norad,
                    "name": str(getattr(sat, "name", "") or profile.get("name") or norad),
                    "sensor_class": profile.get("sensor_class"),
                    "resolution_m": profile.get("resolution_m"),
                    "swath_km": profile.get("swath_km"),
                    "covered_cells": covered,
                    "covered_pct": round(100.0 * covered / total_cells, 2) if total_cells else 0.0,
                    "access_count": access,
                    "standalone_qualified_pct": None,
                    "marginal_cells": None,
                    "cloud": cloud,
                    "verdict": verdict,
                    "reason": reason,
                    "reason_label": REASON_LABELS[reason],
                    "evidence": evidence,
                    "replaces": [],
                }
            )
            continue
        unavailable = (unavailable_by_id or {}).get(norad)
        if mrt_target_s and not unavailable:
            alone = metrics_for((sat_index,))
            standalone_ok = _qualified_count(alone) >= target_cells
            if standalone_ok and sat_index not in selected_set and selected:
                # 单星就达标时，任意包含它的组合都达标（覆盖对卫星集合单调），
                # 因此它可以替换任何一个入选位次。
                replaces = [str(sat_ids.get(slot, slot)) for slot in selected]
            if sat_index in selected_set:
                marginal = 0
            else:
                union = metrics_for(set(selected) | {sat_index})
                marginal = _qualified_count(union) - base_cells
                if not standalone_ok and access > 0 and len(selected) > 0:
                    for slot in selected:
                        combo = set(selected) - {slot} | {sat_index}
                        if _qualified_count(metrics_for(combo)) >= target_cells:
                            replaces.append(str(sat_ids.get(slot, slot)))
                            break
        else:
            alone = None
        if access == 0 and aoi_point and not (cloud and cloud["no_data"] + cloud["dropped"] > 0):
            diagnosis = diagnose_no_access(
                sat,
                lat=float(aoi_point[0]),
                lon=float(aoi_point[1]),
                start=store.start_utc,
                hours=horizon_h or ((store.end_utc - store.start_utc).total_seconds() / 3600.0),
                step_s=max(30, int(store.decision_step_s or 60)),
            )
        tie = norad in dead
        reason, evidence = _classify(
            selected=sat_index in selected_set,
            access_count=access,
            standalone_ok=standalone_ok,
            marginal_cells=marginal,
            replaces=replaces,
            tie=tie,
            cloud=cloud,
            diagnosis=diagnosis,
            unavailable=unavailable,
            target_met=base_pct + 1e-9 >= float(target_area_pct),
            swath_km=profile.get("swath_km"),
        )
        rows.append(
            {
                "sat_id": norad,
                "name": str(getattr(sat, "name", "") or profile.get("name") or norad),
                "sensor_class": profile.get("sensor_class"),
                "resolution_m": profile.get("resolution_m"),
                "swath_km": profile.get("swath_km"),
                "covered_cells": covered,
                "covered_pct": round(100.0 * covered / total_cells, 2) if total_cells else 0.0,
                "access_count": access,
                "standalone_qualified_pct": (
                    None if alone is None else round(_qualified_pct(alone), 2)
                ),
                "marginal_cells": marginal,
                "cloud": cloud,
                "verdict": _verdict(reason),
                "reason": reason,
                "reason_label": REASON_LABELS[reason],
                "evidence": evidence,
                "replaces": replaces,
            }
        )

    # 目录里有、但没进候选池的星（缺 sensor/TLE、SAR、分辨率超限等）也要上账。
    known = {int(item["sat_id"]) for item in rows}
    for item in catalog:
        norad = int(item["norad_id"])
        if norad in known:
            continue
        reason = "data_unavailable"
        evidence = item.get("unavailable_reason") or "未进入候选池"
        rows.append(
            {
                "sat_id": norad,
                "name": item.get("name") or str(norad),
                "sensor_class": item.get("sensor_class"),
                "resolution_m": item.get("resolution_m"),
                "swath_km": item.get("swath_km"),
                "covered_cells": 0,
                "covered_pct": 0.0,
                "access_count": 0,
                "standalone_qualified_pct": None,
                "marginal_cells": None,
                "cloud": None,
                "verdict": "excluded",
                "reason": reason,
                "reason_label": REASON_LABELS[reason],
                "evidence": evidence,
                "replaces": [],
            }
        )

    rows.sort(key=lambda row: (REASON_ORDER.index(row["reason"]), -row["covered_cells"], row["sat_id"]))
    counts = {"selected": 0, "candidate": 0, "excluded": 0}
    groups: dict[str, int] = {}
    for row in rows:
        counts[row["verdict"]] += 1
        groups[row["reason"]] = groups.get(row["reason"], 0) + 1
    summary_groups = [
        {"code": code, "label": REASON_LABELS[code], "count": groups[code]}
        for code in REASON_ORDER
        if groups.get(code)
    ]
    no_data = sum((item.get("cloud") or {}).get("no_data", 0) for item in rows)
    all_access = sum((item.get("cloud") or {}).get("no_data", 0) + (item.get("cloud") or {}).get("dropped", 0) + (item.get("cloud") or {}).get("clear", 0) for item in rows)

    ties = {
        "greedy_only": [str(item) for item in sorted(greedy - ilp)],
        "ilp_only": [str(item) for item in sorted(ilp - greedy)],
        "shared_count": len(greedy & ilp),
    }
    if not ties["greedy_only"] and not ties["ilp_only"]:
        ties = {"greedy_only": [], "ilp_only": [], "shared_count": len(greedy & ilp)}

    return {
        "generated_by": GENERATOR,
        "degraded": degraded,
        "catalog_total": len(rows),
        "summary": {
            "selected": counts["selected"],
            "candidate": counts["candidate"],
            "excluded": counts["excluded"],
            "groups": summary_groups,
        },
        "targets": {
            "area_pct": round(float(target_area_pct), 2),
            "qualified_area_pct": round(base_pct, 2),
            "total_cells": total_cells,
            "target_cells": target_cells,
        },
        "ties": ties,
        "cloud": {
            "filtered": bool(cloud_stats.get("filtered")),
            "dropped_windows": int(cloud_stats.get("dropped_windows") or 0),
            "no_data_accesses": no_data,
            "no_data_pct": round(100.0 * no_data / all_access, 2) if all_access else 0.0,
            "per_sat_available": bool(cloud_stats.get("per_sat")),
        },
        "method_notes": [
            "备选判定基于覆盖并集重算（替换某颗入选星），未逐星重跑 ILP。",
            (
                f"零覆盖诊断以 AOI 形心为准、{max(30, int(store.decision_step_s or 60))} s 步长粗筛，"
                "只用于解释原因，不参与选星。"
            ),
            "云量三态：>阈值剔除 / 有数据保留 / 无数据不剔除但标明。",
        ],
        "rows": rows,
    }
