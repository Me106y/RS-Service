#!/usr/bin/env python3
"""区域重访数据闸门：验证「补星后杭州 72h / 24h / 100%」是否真的可达。

用法：
    .venv/bin/python scripts/revisit_gate.py --aoi 330100 --revisit-hours 24
    .venv/bin/python scripts/revisit_gate.py --aoi 330100 --revisit-hours 24 --start-utc 2026-11-16T08:00:00Z

退出码：0 = 可行且达标面积达标；1 = 不可行/未达标；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime

from rs_service.coverage_engine.adapter import PlanSpec, run_plan


def _parse_start(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def main() -> int:
    parser = argparse.ArgumentParser(description="区域重访数据闸门")
    parser.add_argument("--aoi", default="330100", help="AOI adcode，默认杭州")
    parser.add_argument("--revisit-hours", type=int, default=24, choices=(24, 48, 72))
    parser.add_argument("--max-resolution-m", type=float, default=10.0)
    parser.add_argument("--expect-area", type=float, default=100.0)
    parser.add_argument("--cloud-filter", action="store_true")
    parser.add_argument("--start-utc", default=None)
    args = parser.parse_args()

    horizon = min(3.0 * args.revisit_hours, 168.0)
    spec = PlanSpec(
        operation="revisit",
        aoi_code=args.aoi,
        start_utc=_parse_start(args.start_utc),
        horizon_h=horizon,
        sensor_types=("optical",),
        max_resolution_m=args.max_resolution_m,
        mrt_target_minutes=float(args.revisit_hours) * 60.0,
        area_target_pct=100.0,
        fallback_area_target_pct=100.0,
        allow_area_fallback=False,
        cloud_filter=args.cloud_filter,
        method="both",
        selection_time_limit_s=30,
        greedy_time_limit_s=60,
    )
    started = time.time()
    result = run_plan(spec)
    wall = round(time.time() - started, 1)
    cards = result.get("summary_cards") or {}
    selection = (result.get("selection") or {}).get("recommended_result") or {}
    area = float(cards.get("qualified_area_pct") or 0.0)
    ceiling = cards.get("ceiling_area_pct")
    passed = bool(result.get("feasible")) and area + 1e-9 >= args.expect_area
    payload = {
        "gate": "pass" if passed else "fail",
        "aoi": args.aoi,
        "revisit_hours": args.revisit_hours,
        "window_hours": horizon,
        "max_resolution_m": args.max_resolution_m,
        "cloud_filter": args.cloud_filter,
        "feasible": bool(result.get("feasible")),
        "optimality": result.get("optimality"),
        "qualified_area_pct": area,
        "ceiling_area_pct": ceiling,
        "satellite_count": cards.get("satellite_count"),
        "selected_sat_ids": selection.get("selected_sat_ids") or [],
        "candidate_count": result.get("candidate_count"),
        "wall_s": wall,
        "timings_s": result.get("timings_s"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
