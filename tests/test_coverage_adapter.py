from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rs_service.coverage_engine.adapter import (
    ALONG_TRACK_HALF_ANGLE_DEG,
    Candidate,
    PlanSpec,
    _parse_resolution_m,
    _payload,
    _write_workspace,
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
        PlanCreate(operation="task1", aoi_code="330100")
    with pytest.raises(ValidationError):
        PlanCreate(
            operation="task1",
            aoi_code="330100",
            aoi_wkt="POLYGON ((120 30, 121 30, 121 31, 120 30))",
            temporal_coverage_target_pct=90,
        )


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
