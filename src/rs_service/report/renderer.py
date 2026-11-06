"""Render a self-contained HTML report from a completed plan result."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from shapely.geometry import shape
from shapely.ops import unary_union

BJ = timezone(timedelta(hours=8))
TEMPLATES = Path(__file__).resolve().parent
ACCURACY_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"

OPERATION_LABELS = {
    "task_1_min_satellites_temporal": "最少星达到覆盖率",
    "task_2_min_satellites_mrt": "最少星达到重访",
    "task_3_max_qualified_area": "给定星数最大化达标面积",
    "task_4_min_area_quantile_mrt": "给定星数最小化面积分位 MRT",
    "task_5_min_area_weighted_art": "给定星数最小化 ART",
}


def _bj(value: str | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    if not value:
        return "—"
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(BJ).strftime(fmt)


def report_root() -> Path:
    configured = os.environ.get("RS_REPORT_DIR")
    if configured:
        return Path(configured).expanduser()
    # RS-Service 与 SIRIAgent 是相邻仓库；报告由 SIRIAGent 的 /api/files 提供。
    return Path(__file__).resolve().parents[3].parent / "SIRIAgent" / "reports" / "imaging-plan"


def _map_json(report_data: dict[str, Any]) -> str:
    playback = report_data.get("playback") or {}
    outline = report_data.get("aoi_outline") or []
    lons = [point[0] for point in outline] or [118.0, 121.0]
    lats = [point[1] for point in outline] or [29.0, 31.0]
    payload = {
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "outline": outline,
        "uncovered": playback.get("uncovered") or [],
        "events": playback.get("events") or [],
        "colors": playback.get("colors") or {},
        "colors_light": playback.get("colors_light") or {},
        "total": max(1, int(playback.get("total") or 0)),
        "start_label": playback.get("start_label") or "—",
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _notes(result: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    notes.extend(str(item) for item in result.get("assumptions") or [])
    notes.extend(str(item) for item in result.get("warnings") or [])
    cloud = result.get("cloud") or {}
    if cloud.get("filtered"):
        notes.append(
            f"云量过滤：Open-Meteo 逐小时 cloud_cover >{cloud.get('threshold_pct')}% 的访问已剔除"
            f"（丢弃窗口 {cloud.get('dropped_windows')} 个）。"
        )
    else:
        notes.append(f"云量未过滤：{cloud.get('reason') or '未启用'}。")
    blocked = result.get("blocked_satellites") or []
    if blocked:
        notes.append(f"未参与选星 {len(blocked)} 颗，原因见任务结果。")
    targets = result.get("targets") or {}
    if result.get("operation") in {"task1", "task_1_min_satellites_temporal"}:
        notes.append(
            "达标判定：至少成像一次的面积占比目标 "
            f"{targets.get('area_pct', 90):g}%；不设时间门槛。"
        )
        area_pct = float((result.get("summary_cards") or {}).get("qualified_area_pct") or 0.0)
        area_target = targets.get("area_pct")
        if area_target is not None and area_pct < float(area_target) - 1e-9:
            notes.append(f"未达标面积 {100.0 - area_pct:.1f}%。")
    elif targets.get("temporal_coverage_pct") is not None and targets.get("area_pct") is not None:
        notes.append(
            "达标判定：单格时间覆盖率 ≥ "
            f"{targets['temporal_coverage_pct']:g}%，达到该阈值的面积占比目标 "
            f"{targets['area_pct']:g}%。"
        )
    provenance = result.get("provenance") or {}
    if provenance:
        notes.append(
            "复现指纹：TLE "
            f"{str(provenance.get('tle_sha256'))[:12]}…，catalog "
            f"{str(provenance.get('catalog_sha256'))[:12]}…，"
            f"profile {provenance.get('profile_policy_version') or '—'}。"
        )
    tle = result.get("tle") or {}
    if tle.get("max_age_hours") is not None:
        notes.append(
            f"TLE 最大年龄 {tle['max_age_hours']:g} 小时"
            f"{'（>72h，结果标 degraded）' if tle.get('degraded') else ''}。"
        )
    notes.append("TLE 每日同步；SGP4 在 LEO 约 1 km/天，结果只用于可行性初判。")
    return list(dict.fromkeys(notes))


def _gap_components(result: dict[str, Any]) -> list[dict[str, Any]]:
    geojson = (result.get("coverage_map") or {}).get("geojson") or {}
    features = geojson.get("features") or []
    holes = [
        item
        for item in features
        if not (item.get("properties") or {}).get("qualified", False)
    ]
    if not holes:
        return []
    geometries = [shape(item["geometry"]) for item in holes]
    merged = unary_union(geometries)
    components = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    rows = []
    for component in components:
        members = [
            (geometry, feature.get("properties") or {})
            for geometry, feature in zip(geometries, holes, strict=False)
            if component.intersects(geometry.representative_point())
        ]
        if not members:
            continue
        minx, miny, maxx, maxy = component.bounds
        rows.append(
            {
                "area_km2": round(sum(float(props.get("area_km2") or 0) for _, props in members), 1),
                "cells": len(members),
                "lon": f"{minx:.2f}–{maxx:.2f}°E",
                "lat": f"{miny:.2f}–{maxy:.2f}°N",
            }
        )
    return sorted(rows, key=lambda item: item["area_km2"], reverse=True)[:5]


def render_report(result: dict[str, Any]) -> str:
    cards = result.get("summary_cards") or {}
    report_data = result.get("report_data") or {}
    playback = report_data.get("playback") or {}
    colors = playback.get("colors") or {}
    colors_light = playback.get("colors_light") or {}

    sats = []
    for item in report_data.get("sats") or []:
        key = str(item.get("sat_id"))
        sats.append(
            {
                **item,
                "color": colors.get(key, "#48c7d9"),
                "color_light": colors_light.get(key, "#0e7490"),
            }
        )

    passes = []
    for item in result.get("city_pass_window_table") or []:
        passes.append(
            {
                **item,
                "start_label": _bj(item.get("start_utc")),
                "end_label": _bj(item.get("end_utc")),
            }
        )

    max_by_sat = {
        int(item.get("norad_id")): float(item.get("maximum_covered_area_pct") or 0.0)
        for item in passes
        if item.get("norad_id") is not None
    }
    for item in sats:
        item["max_covered_area_pct"] = max_by_sat.get(int(item.get("sat_id") or 0), 0.0)
    sats.sort(
        key=lambda item: (item.get("max_covered_area_pct") or 0.0, item.get("cumulative_minutes") or 0.0),
        reverse=True,
    )
    for index, item in enumerate(sats):
        item["rank"] = index + 1

    gaps = _gap_components(result)
    weak = list(result.get("weak_areas") or [])[:10]
    city = result.get("city") or {}
    start = (result.get("input_summary") or {}).get("start_utc")
    end = (result.get("input_summary") or {}).get("end_utc")
    window = result.get("window") or {}
    hours = window.get("used_horizon_h") or (result.get("input_summary") or {}).get(
        "analysis_horizon_h"
    )
    window_note = (
        f"请求 {window.get('requested_horizon_h'):g}h，自动扩窗"
        if window.get("expanded") and window.get("requested_horizon_h") is not None
        else ""
    )
    is_task1 = result.get("operation") in {
        "task1",
        "task_1_min_satellites_temporal",
    }
    show_temporal = not is_task1
    targets = result.get("targets") or {}
    temporal_target = targets.get("temporal_coverage_pct")
    area_target = targets.get("area_pct")
    target_label = (
        f"达标面积（目标 {area_target:g}%）"
        if area_target is not None
        else "达标面积"
    )
    label = OPERATION_LABELS.get(result.get("operation", ""), result.get("operation", "成图规划"))
    count = int(cards.get("satellite_count") or len(sats))
    area_pct = float(cards.get("qualified_area_pct") or 0.0)
    headline = f"{label}：选中 <em>{count}</em> 颗，达标面积 <em>{area_pct:.1f}%</em>"

    events_count = len(playback.get("events") or [])
    tick_ms = max(900, min(2500, 12000 // max(1, events_count)))
    area_met = area_target is not None and area_pct >= float(area_target) - 1e-9
    show_weak = bool(weak) and show_temporal and not area_met
    show_gaps = bool(gaps) and area_pct < 99.9
    max_gap_hours = 0.0
    if len(passes) > 1:
        starts = sorted(
            datetime.fromisoformat(item["start_utc"].replace("Z", "+00:00"))
            for item in passes
        )
        max_gap_hours = max(
            (later - earlier).total_seconds() / 3600.0
            for earlier, later in zip(starts, starts[1:], strict=False)
        )
    scope_note = (
        f"面积为 {hours:g}h 窗口内累计并集，不是单景；"
        f"最大相邻过境间隔 {max_gap_hours:.1f}h；"
        "单景可执行性需另看太阳/云量与同时覆盖。"
        if is_task1
        else ""
    )
    show_playback = events_count > 1
    playback_note = (
        ""
        if show_playback
        else "本次方案只有 1 颗卫星 / 1 个过境，没有可播放的多帧序列；多星方案才有颜色累积回放。"
    )

    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("report.html.j2").render(
        title=f"成图编成 · {city.get('name', '')}",
        generated_at=datetime.now(BJ).strftime("%Y-%m-%d %H:%M"),
        headline=headline,
        operation_label=label,
        city_name=city.get("name") or "—",
        city_area=float(city.get("area_km2") or 0.0),
        window_start=_bj(start),
        window_end=_bj(end),
        window_hours=hours,
        window_note=window_note,
        target_label=target_label,
        temporal_target=temporal_target,
        area_target=area_target,
        show_temporal=show_temporal,
        show_weak=show_weak,
        show_gaps=show_gaps,
        gaps=gaps,
        scope_note=scope_note,
        show_playback=show_playback,
        playback_note=playback_note,
        feasible=bool(result.get("feasible")),
        optimality=result.get("optimality") or "—",
        sat_count=count,
        cards=cards,
        sats=sats,
        passes=passes,
        weak=weak,
        notes=_notes(result),
        map_json=_map_json(report_data),
        events_count=events_count,
        tick_ms=tick_ms,
        speed_label=f"{tick_ms / 1000:.1f} 秒/步",
        accuracy_note=ACCURACY_NOTE,
        algo_label="coverage-engine 0.1.0-rc.1 · RS-Service",
    )


def write_report(
    result: dict[str, Any],
    run_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    target_root = Path(root) if root else report_root()
    target = target_root / run_id
    target.mkdir(parents=True, exist_ok=True)

    plan_path = target / "plan.json"
    plan_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    report_path = target / "report.html"
    report_path.write_text(render_report(result), encoding="utf-8")

    latest = target_root / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_id, target_is_directory=True)
    except OSError:
        (target_root / "latest.txt").write_text(run_id, encoding="utf-8")

    siriagent_root = target_root.parents[1]
    try:
        relative = report_path.relative_to(siriagent_root).as_posix()
        relative_plan = plan_path.relative_to(siriagent_root).as_posix()
    except ValueError:
        relative = str(report_path)
        relative_plan = str(plan_path)
    return {
        "run_id": run_id,
        "dir": str(target),
        "report_path": str(report_path),
        "plan_path": str(plan_path),
        "relative_report": relative,
        "relative_plan": relative_plan,
        "latest": str(latest),
    }


__all__ = ["ACCURACY_NOTE", "render_report", "report_root", "write_report"]
