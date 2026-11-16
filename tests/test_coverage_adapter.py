from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rs_service.coverage_engine.adapter import (
    ALONG_TRACK_GROUND_SPEED_KMS,
    ALONG_TRACK_SMEAR_FACTOR,
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
    # 推扫靠运动累积：沿轨半长 = 0.55·v·dt，10 s 对应 41.25 km
    assert override["along_track_km"] == pytest.approx(
        ALONG_TRACK_SMEAR_FACTOR * ALONG_TRACK_GROUND_SPEED_KMS * 10
    )
    assert override["min_elevation_deg"] == 10.0
    assert override["constraints"] == [
        {"name": "SunGroundElevAngle", "params": "Min 10"}
    ]
    assert "pointing" not in override
    assert payload["area_target_pct"] == 90
    assert payload["allow_area_fallback"] is False
    assert "method" not in payload


def test_payload_never_adds_pointing_override() -> None:
    # 目标指向已取消：不管 spec 里写什么，都不再生成 Targeted 覆盖配置。
    spec = replace(
        PlanSpec(operation="task2", aoi_code="330100", mrt_target_minutes=24),
        pointing="targeted",
    )
    payload = _payload(spec, [_candidate()], "330100")
    assert "pointing" not in payload["satellite_profile_overrides"][0]


def test_plan_create_rejects_targeted_pointing() -> None:
    with pytest.raises(ValidationError):
        PlanCreate(
            operation="task1",
            aoi_code="330100",
            temporal_coverage_target_pct=0.3,
            pointing="targeted",
        )


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


def test_cloud_filter_is_per_grid_not_whole_aoi() -> None:
    """同一个过境里，东部网格用本地晴空云量，不被西部质心阴雨一起判死。"""

    from types import SimpleNamespace

    from rs_service.coverage_engine.adapter import _filter_windows_by_cell

    start = datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
    west = SimpleNamespace(grid_index=0, start_utc=start, end_utc=start + timedelta(minutes=10))
    east = SimpleNamespace(grid_index=1, start_utc=start, end_utc=start + timedelta(minutes=10))
    cells = [
        SimpleNamespace(representative_lat=29.90, representative_lon=119.47),
        SimpleNamespace(representative_lat=30.27, representative_lon=120.16),
    ]
    series_points = [
        ((29.90, 119.47), [(start, 100.0)]),
        ((30.27, 120.16), [(start, 35.0)]),
    ]
    kept, dropped = _filter_windows_by_cell(
        {(0, 0): (west,), (0, 1): (east,)}, cells, series_points, 70
    )
    assert dropped == 1
    assert set(kept) == {(0, 1)}


def test_cloud_sample_points_cover_aoi_without_centroid_bias() -> None:
    from types import SimpleNamespace

    from rs_service.coverage_engine.adapter import _cloud_sample_points

    cells = [
        SimpleNamespace(representative_lat=29.0 + lat * 0.1, representative_lon=119.0 + lon * 0.1)
        for lat in range(8)
        for lon in range(8)
    ]
    store = SimpleNamespace(grid=SimpleNamespace(cells=cells))
    points = _cloud_sample_points(store)
    assert 1 < len(points) <= 64
    assert (29.0, 119.0) in points
    assert len(set(points)) == len(points)


def test_tle_age_hours() -> None:
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    assert tle_age_hours("2026-11-05T06:00:00Z", now) == 6.0
    assert tle_age_hours("2026-11-05T00:00:00", now) == 12.0
    assert tle_age_hours(None, now) is None
    assert tle_age_hours("not-a-time", now) is None


def test_run_plan_auto_expand_stops_on_first_feasible(monkeypatch) -> None:
    from rs_service.coverage_engine import adapter

    calls: list[float] = []

    def fake_run(spec: PlanSpec, progress=None) -> dict:
        calls.append(spec.horizon_h)
        return {"feasible": spec.horizon_h >= 72.0, "assumptions": []}

    monkeypatch.setattr(adapter, "_run_once", fake_run)
    result = adapter.run_plan(PlanSpec(operation="task1", auto_expand=True, horizon_h=8))
    assert calls == [8.0, 72.0]
    assert result["window"]["used_horizon_h"] == 72.0
    assert result["window"]["expanded"] is True
    assert result["auto_expand"]["attempts"] == 2


def test_run_plan_auto_expand_caps_at_168(monkeypatch) -> None:
    from rs_service.coverage_engine import adapter

    calls: list[float] = []

    def fake_run(spec: PlanSpec, progress=None) -> dict:
        calls.append(spec.horizon_h)
        return {"feasible": False, "assumptions": []}

    monkeypatch.setattr(adapter, "_run_once", fake_run)
    result = adapter.run_plan(PlanSpec(operation="task1", auto_expand=True, horizon_h=8))
    assert calls == [8.0, 72.0, 168.0]
    assert result["window"]["used_horizon_h"] == 168.0
    assert result["auto_expand"]["attempts"] == 3


def test_task1_auto_expand_ladder_only_for_task1() -> None:
    task1 = PlanSpec(operation="task1", auto_expand=True, horizon_h=8)
    assert _horizons(task1) == [8.0, 72.0, 168.0]
    # 请求 72h 是常见路径：必须继续扩到 7 天，而不是原地判定不可行。
    assert _horizons(PlanSpec(operation="task1", auto_expand=True, horizon_h=72)) == [
        72.0,
        168.0,
    ]
    assert _horizons(PlanSpec(operation="task1", auto_expand=True, horizon_h=168)) == [
        168.0
    ]
    task2 = PlanSpec(operation="task2", auto_expand=True, horizon_h=8)
    assert _horizons(task2) == [8.0]


def test_revisit_has_no_auto_expand() -> None:
    with pytest.raises(ValidationError):
        PlanCreate(operation="task2", aoi_code="330100", mrt_target_minutes=24)
    model = PlanCreate(operation="revisit", aoi_code="330100", revisit_hours=24)
    assert model.to_spec().auto_expand is False


def test_single_revisit_task() -> None:
    assert [item["operation"] for item in TASKS] == ["revisit"]


def test_plan_create_requires_aoi() -> None:
    with pytest.raises(ValidationError):
        PlanCreate(operation="revisit", revisit_hours=24)
    with pytest.raises(ValidationError):
        PlanCreate(
            operation="revisit",
            aoi_code="330100",
            aoi_wkt="POLYGON ((120 30, 121 30, 121 31, 120 30))",
        )
    spec = PlanCreate(operation="revisit", aoi_code="330100").to_spec()
    assert spec.area_target_pct == 100.0
    assert spec.temporal_coverage_target_pct is None


def test_plan_create_to_spec_defaults_timezone() -> None:
    model = PlanCreate(
        operation="revisit",
        aoi_code="330100",
        revisit_hours=24,
        start_utc=datetime(2026, 11, 5),
    )
    spec = model.to_spec()
    assert spec.start_utc.tzinfo is not None
    assert spec.sensor_types == ("optical",)
    assert spec.horizon_h == 72.0
    assert spec.mrt_target_minutes == 1440.0


def test_payload_sar_is_exempt_from_solar_constraint() -> None:
    sar = replace(_candidate(), sensor_class="SAR")
    payload = _payload(
        PlanSpec(operation="task3", aoi_code="330100", n_budget=2),
        [sar],
        "330100",
    )
    override = payload["satellite_profile_overrides"][0]
    assert override["min_elevation_deg"] == 10.0
    assert "constraints" not in override


def test_payload_applies_imaging_gate_to_revisit() -> None:
    payload = _payload(
        PlanSpec(operation="revisit", aoi_code="330100", mrt_target_minutes=1440),
        [_candidate()],
        "330100",
    )
    override = payload["satellite_profile_overrides"][0]
    assert override["min_elevation_deg"] == 10.0
    assert override["constraints"] == [
        {"name": "SunGroundElevAngle", "params": "Min 10"}
    ]
    assert payload["operation"] == "task_2_min_satellites_mrt"
    assert payload["analysis_horizon_h"] == 72.0
