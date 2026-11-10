from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rs_service.coverage_engine.adapter import (
    ALONG_TRACK_HALF_ANGLE_DEG,
    Candidate,
    PlanSpec,
    _horizons,
    _parse_resolution_m,
    _payload,
    _write_workspace,
    tle_age_hours,
)
from rs_service.plan_api import TASKS, PlanCreate


def test_parse_resolution_takes_first_metre_value() -> None:
    assert _parse_resolution_m("2 m / 8 m；60 km；16 m 宽幅 800 km") == 2.0
    assert _parse_resolution_m("0.72 m 全色 / 2.9 m 多光谱；约 4.5 km") == 0.72
    assert _parse_resolution_m("250 m 可见 / 1 km 红外；2900 km") == 250.0
    assert _parse_resolution_m("未公开") is None
    assert _parse_resolution_m(None) is None


def _candidate() -> Candidate:
    return Candidate(
        norad_id=39150,
        name="GF-1",
        sensor_class="可见光",
        resolution_m=2.0,
        swath_km=800.0,
        omm={"NORAD_CAT_ID": 39150, "OBJECT_NAME": "GF-1"},
        tle_epoch_utc="2026-11-05T00:00:00Z",
        tle_age_hours=6.0,
    )


def test_payload_uses_db_profile_and_nadir_by_default() -> None:
    spec = PlanSpec(
        operation="task1",
        aoi_code="330100",
        start_utc=datetime(2026, 11, 5, tzinfo=UTC),
        horizon_h=8,
        temporal_coverage_target_pct=5,
        area_target_pct=90,
        allow_area_fallback=False,
    )
    payload = _payload(spec, [_candidate()], "330100")
    assert payload["profile_slug"] == "rs_service_db_v1"
    assert payload["tle_path"] == "data/tle/rs.json"
    assert payload["satellite_ids"] == [39150]
    override = payload["satellite_profile_overrides"][0]
    assert override["swath_km"] == 800.0
    assert override["along_track_half_angle_deg"] == ALONG_TRACK_HALF_ANGLE_DEG
    assert "pointing" not in override
    assert payload["area_target_pct"] == 90
    assert payload["allow_area_fallback"] is False
    assert "method" not in payload


def test_payload_targeted_adds_pointing() -> None:
    spec = replace(
        PlanSpec(operation="task2", aoi_code="330100", mrt_target_minutes=24),
        pointing="targeted",
    )
    payload = _payload(spec, [_candidate()], "330100")
    assert payload["satellite_profile_overrides"][0]["pointing"] == {"type": "Targeted"}


def test_workspace_writes_omm_json_and_geojson(tmp_path) -> None:
    aoi = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"adcode": "330100", "name": "杭州市"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[120, 30], [121, 30], [121, 31], [120, 30]]],
                },
            }
        ],
    }
    root = _write_workspace([_candidate()], aoi, "330100")
    try:
        assert (root / "data/geo/330100.json").is_file()
        assert (root / "data/tle/rs.json").is_file()
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


def test_cloud_filter_drops_windows_above_threshold() -> None:
    from regional_opportunities import AccessWindow

    from rs_service.coverage_engine.adapter import _filter_windows

    start = datetime(2026, 11, 5, tzinfo=UTC)
    cloudy = AccessWindow(0, 1, "A", 0, "c0", start, start + timedelta(minutes=10), 600.0)
    clear = AccessWindow(
        0, 1, "A", 1, "c1", start + timedelta(hours=2),
        start + timedelta(hours=2, minutes=10), 600.0,
    )
    series = [(start, 85.0), (start + timedelta(hours=2), 10.0)]
    kept, dropped = _filter_windows({(0, 0): (cloudy,), (0, 1): (clear,)}, series, 70)
    assert dropped == 1
    assert set(kept) == {(0, 1)}


def test_tle_age_hours() -> None:
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    assert tle_age_hours("2026-11-05T06:00:00Z", now) == 6.0
    assert tle_age_hours("2026-11-05T00:00:00", now) == 12.0
    assert tle_age_hours(None, now) is None
    assert tle_age_hours("not-a-time", now) is None


def test_run_plan_auto_expand_stops_on_first_feasible(monkeypatch) -> None:
    from rs_service.coverage_engine import adapter

    calls: list[float] = []

    def fake_run(spec: PlanSpec) -> dict:
        calls.append(spec.horizon_h)
        return {"feasible": spec.horizon_h >= 72.0, "assumptions": []}

    monkeypatch.setattr(adapter, "_run_once", fake_run)
    result = adapter.run_plan(PlanSpec(operation="task1", auto_expand=True, horizon_h=8))
    assert calls == [8.0, 72.0]
    assert result["window"]["used_horizon_h"] == 72.0
    assert result["window"]["expanded"] is True
    assert result["auto_expand"]["attempts"] == 2


def test_run_plan_auto_expand_caps_at_72(monkeypatch) -> None:
    from rs_service.coverage_engine import adapter

    calls: list[float] = []

    def fake_run(spec: PlanSpec) -> dict:
        calls.append(spec.horizon_h)
        return {"feasible": False, "assumptions": []}

    monkeypatch.setattr(adapter, "_run_once", fake_run)
    result = adapter.run_plan(PlanSpec(operation="task1", auto_expand=True, horizon_h=8))
    assert calls == [8.0, 72.0]
    assert result["window"]["used_horizon_h"] == 72.0
    assert result["auto_expand"]["attempts"] == 2


def test_task1_auto_expand_ladder_only_for_task1() -> None:
    task1 = PlanSpec(operation="task1", auto_expand=True, horizon_h=8)
    assert _horizons(task1) == [8.0, 72.0]
    assert _horizons(PlanSpec(operation="task1", auto_expand=True, horizon_h=72)) == [72.0]
    task2 = PlanSpec(operation="task2", auto_expand=True, horizon_h=8)
    assert _horizons(task2) == [8.0]


def test_auto_expand_rejected_for_other_tasks() -> None:
    with pytest.raises(ValidationError):
        PlanCreate(
            operation="task2",
            aoi_code="330100",
            mrt_target_minutes=24,
            auto_expand=True,
        )
    model = PlanCreate(
        operation="task1",
        aoi_code="330100",
        temporal_coverage_target_pct=10,
        auto_expand=True,
    )
    assert model.to_spec().auto_expand is True


def test_five_tasks_only() -> None:
    assert [item["operation"] for item in TASKS] == [
        "task1",
        "task2",
        "task3",
        "task4",
        "task5",
    ]


def test_plan_create_requires_aoi_and_task_fields() -> None:
    with pytest.raises(ValidationError):
        PlanCreate(operation="task1", temporal_coverage_target_pct=90)
    with pytest.raises(ValidationError):
        PlanCreate(operation="task2", aoi_code="330100")
    with pytest.raises(ValidationError):
        PlanCreate(
            operation="task1",
            aoi_code="330100",
            aoi_wkt="POLYGON ((120 30, 121 30, 121 31, 120 30))",
            temporal_coverage_target_pct=90,
        )
    # task1 是面积口径，不再要求时间覆盖率；缺省面积目标按 90%。
    spec = PlanCreate(operation="task1", aoi_code="330100").to_spec()
    assert spec.area_target_pct == 90.0
    assert spec.temporal_coverage_target_pct == pytest.approx(0.001)


def test_plan_create_to_spec_defaults_timezone() -> None:
    model = PlanCreate(
        operation="task2",
        aoi_code="330100",
        mrt_target_minutes=24,
        start_utc=datetime(2026, 11, 5),
    )
    spec = model.to_spec()
    assert spec.start_utc.tzinfo is not None
    assert spec.sensor_types == ("optical", "hyperspectral", "sar")


def test_task1_is_area_only_regardless_of_temporal_target() -> None:
    """回归：task1 必须按面积口径算，忽略旧客户端传来的单格时间覆盖率。

    否则 90% 的单格时间阈值在 72 h 内不可达，达标面积恒为 0.0%。
    """

    model = PlanCreate(
        operation="task1",
        aoi_code="330100",
        temporal_coverage_target_pct=90.0,
        area_target_pct=90.0,
    )
    spec = model.to_spec()
    assert spec.temporal_coverage_target_pct == pytest.approx(0.001)
    assert spec.area_target_pct == 90.0
    assert spec.fallback_area_target_pct == 90.0


def test_task3_is_area_only_regardless_of_temporal_target() -> None:
    """回归：task3 是“给定星数最大化覆盖面积”，不再要求/使用时间覆盖率目标。"""

    model = PlanCreate(
        operation="task3",
        aoi_code="330100",
        n_budget=3,
        temporal_coverage_target_pct=50.0,
    )
    spec = model.to_spec()
    assert spec.n_budget == 3
    assert spec.temporal_coverage_target_pct == pytest.approx(0.001)
    # 新客户端不再传目标覆盖率也必须能提交。
    spec2 = PlanCreate(operation="task3", aoi_code="330100", n_budget=3).to_spec()
    assert spec2.temporal_coverage_target_pct == pytest.approx(0.001)


def test_task3_annotates_covered_area_and_hides_temporal_target() -> None:
    result = {
        "operation": "task3",
        "selection": {
            "recommended_result": {
                "temporal_coverage_target_pct": 0.001,
                "area_target_pct": 90.0,
                "mrt_target_minutes": None,
            }
        },
        "summary_cards": {"qualified_area_pct": 96.9},
    }
    from rs_service.coverage_engine.adapter import _annotate_result

    _annotate_result(result, [], [], {}, {})
    assert result["targets"]["temporal_coverage_pct"] is None
    assert result["targets"]["area_pct"] is None
    assert result["summary_cards"]["covered_area_pct"] == pytest.approx(96.9)
