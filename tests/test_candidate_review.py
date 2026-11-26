from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from rs_service.coverage_engine import candidate_review as cr
from rs_service.coverage_engine.candidate_review import _classify, build_candidate_review
from rs_service.report import render_report


def _metrics(count: int, total: int = 4):
    return SimpleNamespace(
        city=SimpleNamespace(
            qualified_grid_count=count,
            qualified_area_pct=100.0 * count / total,
        )
    )


class FakeSat:
    def __init__(self, norad: int, name: str) -> None:
        self.norad_id = norad
        self.name = name


def _store(selected_count_map=None):
    start = datetime(2026, 11, 22, 0, tzinfo=UTC)
    window = SimpleNamespace(
        sat_index=0,
        grid_index=0,
        start_utc=start,
        end_utc=start + timedelta(minutes=5),
    )
    return SimpleNamespace(
        satellites=(FakeSat(101, "A"), FakeSat(102, "B"), FakeSat(103, "C")),
        windows={
            (0, 0): (window,),
            (0, 1): (window,),
            (1, 0): (window,),
            (1, 1): (window,),
        },
        n_cells=4,
        start_utc=start,
        end_utc=start + timedelta(hours=48),
        decision_step_s=60,
    )


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"unavailable": "缺少 OMM/TLE"}, "data_unavailable"),
        ({"selected": True}, "selected"),
        (
            {"cloud": {"dropped": 3, "clear": 0, "no_data": 0}, "access_count": 0},
            "cloud_dropped",
        ),
        (
            {"access_count": 0, "diagnosis": {"max_elevation_deg": -2.0}},
            "no_track",
        ),
        (
            {"access_count": 0, "diagnosis": {"max_elevation_deg": 7.4}},
            "elevation_below_threshold",
        ),
        (
            {"access_count": 0, "diagnosis": {"max_elevation_deg": 40.0, "sun_at_peak_deg": -12.0}},
            "night_only",
        ),
        ({"access_count": 2, "marginal_cells": 0}, "redundant"),
        ({"access_count": 2, "marginal_cells": 3}, "insufficient"),
        ({"access_count": 2, "replaces": ["101"]}, "alternate"),
        ({"access_count": 2, "replaces": ["101"], "tie": True}, "equivalent_tie"),
    ],
)
def test_classify_priority(kwargs: dict, reason: str) -> None:
    base = {
        "selected": False,
        "access_count": 2,
        "standalone_ok": False,
        "marginal_cells": 0,
        "replaces": (),
        "tie": False,
        "cloud": None,
        "diagnosis": {},
        "unavailable": None,
    }
    base.update(kwargs)
    assert _classify(**base)[0] == reason


def test_candidate_review_closure_and_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    """目录总数 = 入选 + 备选 + 未采用；每颗星只出现一次。"""

    store = _store()
    selected = (0,)

    def fake_evaluate(_store, indices, _mrt, _min_sim):
        combo = set(int(item) for item in indices)
        if combo == {0}:
            return _metrics(2)
        if combo == {0, 1}:
            return _metrics(2)
        if combo == {1}:
            return _metrics(1)
        return _metrics(0)

    monkeypatch.setattr(cr, "_evaluate", fake_evaluate)
    monkeypatch.setattr(
        cr,
        "diagnose_no_access",
        lambda *_args, **_kwargs: {"max_elevation_deg": -3.0, "sun_at_peak_deg": None},
    )
    review = build_candidate_review(
        store=store,
        profiles=[
            {"norad_id": 101, "name": "A", "sensor_class": "可见光", "resolution_m": 5.0, "swath_km": 60.0},
            {"norad_id": 102, "name": "B", "sensor_class": "可见光", "resolution_m": 10.0, "swath_km": 90.0},
        ],
        catalog=[
            {"norad_id": 101, "name": "A"},
            {"norad_id": 102, "name": "B"},
            {"norad_id": 103, "name": "C"},
            {"norad_id": 104, "name": "D", "unavailable_reason": "缺少 OMM/TLE"},
        ],
        selected_indices=selected,
        selected_ids=[101],
        mrt_target_s=3600.0,
        target_area_pct=100.0,
        aoi_point=(30.0, 120.0),
        horizon_h=48.0,
    )
    reasons = {row["sat_id"]: row["reason"] for row in review["rows"]}
    assert reasons[101] == "selected"
    assert reasons[102] == "redundant"
    assert reasons[103] == "no_track"
    assert reasons[104] == "data_unavailable"
    summary = review["summary"]
    assert summary["selected"] == 1
    assert summary["excluded"] == 3
    assert summary["selected"] + summary["candidate"] + summary["excluded"] == review["catalog_total"] == 4
    assert len(review["rows"]) == 4


def test_candidate_review_marks_alternate_when_replacement_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()

    def fake_evaluate(_store, indices, _mrt, _min_sim):
        combo = set(int(item) for item in indices)
        return _metrics(4 if 0 in combo or combo == {1} else 0)

    monkeypatch.setattr(cr, "_evaluate", fake_evaluate)
    review = build_candidate_review(
        store=store,
        profiles=[{"norad_id": 102, "name": "B"}],
        catalog=[{"norad_id": 101, "name": "A"}, {"norad_id": 102, "name": "B"}],
        selected_indices=(0,),
        selected_ids=[101],
        mrt_target_s=3600.0,
        target_area_pct=100.0,
        aoi_point=None,
        horizon_h=48.0,
    )
    row = next(item for item in review["rows"] if item["sat_id"] == 102)
    assert row["reason"] == "alternate"
    assert row["verdict"] == "candidate"
    assert row["replaces"] == ["101"]


def test_candidate_review_degrades_past_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store()

    def fake_evaluate(_store, indices, _mrt, _min_sim):
        return _metrics(2)

    monkeypatch.setattr(cr, "_evaluate", fake_evaluate)
    review = build_candidate_review(
        store=store,
        profiles=[],
        catalog=[],
        selected_indices=(0,),
        selected_ids=[101],
        mrt_target_s=3600.0,
        target_area_pct=100.0,
        horizon_h=48.0,
        deadline_s=0.0,
    )
    assert review["degraded"] is True
    assert all(row["reason"] in {"not_evaluated", "selected"} for row in review["rows"])


def _revisit_result() -> dict:
    return {
        "status": "completed",
        "operation": "revisit",
        "feasible": True,
        "optimality": "proven_optimal",
        "targets": {"area_pct": 100.0, "revisit_hours": 48.0, "mrt_minutes": 2880.0},
        "window": {"requested_horizon_h": 144.0, "used_horizon_h": 144.0, "expanded": False},
        "tle": {"max_age_hours": 6.0, "degraded": False},
        "summary_cards": {"satellite_count": 1, "qualified_area_pct": 100.0},
        "city": {"name": "杭州市", "area_km2": 16850.0, "adcode": "330100"},
        "report_data": {
            "sats": [
                {
                    "sat_id": 101,
                    "name": "SENTINEL-2B",
                    "sensor_label": "可见光",
                    "resolution_m": 10.0,
                    "swath_km": 290.0,
                    "passes": 2,
                    "cumulative_minutes": 1.2,
                    "first_window": "2026-11-23T02:49:20Z",
                }
            ],
            "selected_ids": [101],
            "selected_count": 1,
            "aoi_outline": [[120.0, 30.0], [121.0, 30.0], [121.0, 31.0], [120.0, 30.0]],
            "playback": {
                "events": [],
                "uncovered": [],
                "total": 0,
                "colors": {"101": "#48c7d9"},
                "colors_light": {"101": "#0e7490"},
                "start_label": "11-22 21:00",
                "accesses": [],
            },
        },
        "candidate_review": {
            "generated_by": cr.GENERATOR,
            "degraded": False,
            "catalog_total": 3,
            "summary": {
                "selected": 1,
                "candidate": 0,
                "excluded": 2,
                "groups": [
                    {"code": "selected", "label": "入选", "count": 1},
                    {"code": "no_track", "label": "轨迹未经过 AOI", "count": 1},
                    {"code": "data_unavailable", "label": "数据不可用", "count": 1},
                ],
            },
            "targets": {"area_pct": 100.0, "total_cells": 782, "target_cells": 782},
            "ties": {"greedy_only": [], "ilp_only": [], "shared_count": 1},
            "cloud": {"filtered": False, "dropped_windows": 0, "no_data_pct": 0.0},
            "method_notes": ["备选判定基于覆盖并集重算（替换某颗入选星），未逐星重跑 ILP。"],
            "rows": [
                {
                    "sat_id": 101,
                    "name": "SENTINEL-2B",
                    "sensor_class": "可见光",
                    "resolution_m": 10.0,
                    "swath_km": 290.0,
                    "covered_cells": 782,
                    "covered_pct": 100.0,
                    "access_count": 12,
                    "standalone_qualified_pct": 100.0,
                    "marginal_cells": 0,
                    "cloud": None,
                    "verdict": "selected",
                    "reason": "selected",
                    "reason_label": "入选",
                    "evidence": "推荐解采用",
                    "replaces": [],
                },
                {
                    "sat_id": 102,
                    "name": "JILIN-1 GAOFEN 3E",
                    "sensor_class": "可见光",
                    "resolution_m": 5.0,
                    "swath_km": 60.0,
                    "covered_cells": 0,
                    "covered_pct": 0.0,
                    "access_count": 0,
                    "standalone_qualified_pct": None,
                    "marginal_cells": 0,
                    "cloud": None,
                    "verdict": "excluded",
                    "reason": "no_track",
                    "reason_label": "轨迹未经过 AOI",
                    "evidence": "最高仰角 -3.0°（在地平线以下，轨迹未经过 AOI）",
                    "replaces": [],
                },
                {
                    "sat_id": 103,
                    "name": "BROKEN-SAT",
                    "sensor_class": None,
                    "resolution_m": None,
                    "swath_km": None,
                    "covered_cells": 0,
                    "covered_pct": 0.0,
                    "access_count": 0,
                    "standalone_qualified_pct": None,
                    "marginal_cells": None,
                    "cloud": None,
                    "verdict": "excluded",
                    "reason": "data_unavailable",
                    "reason_label": "数据不可用",
                    "evidence": "缺少 OMM/TLE",
                    "replaces": [],
                },
            ],
        },
    }


def test_report_renders_review_section_and_keeps_two_kpis() -> None:
    html = render_report(_revisit_result())
    assert "候选与排除" in html
    assert "目录里共 3 颗：入选 1、备选 0、未采用 2" in html
    assert "轨迹未经过 AOI" in html
    assert "数据不可用" in html
    labels = re.findall(r'<div class="lab">([^<]+)</div>', html)
    assert labels == ["选中卫星", "达标面积（最长重访 ≤ 48 h · 目标 100%）"]
    # 同源断言：报告里逐颗的行数必须等于 plan.json 里的 rows 长度。
    section = html.split('id="review"', 1)[1].split("</section>", 1)[0]
    assert len(re.findall(r'<span class="sat-id">', section)) == 3


def test_report_hides_review_section_without_data() -> None:
    result = _revisit_result()
    result.pop("candidate_review")
    html = render_report(result)
    assert "候选与排除" not in html
