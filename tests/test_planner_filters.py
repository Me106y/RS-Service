from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from rs_service.astro.coverage import Grid
from rs_service.astro.planner import Candidate, _apply_sun, _count_visits


def _grid() -> Grid:
    return Grid(lon=np.array([120.15]), lat=np.array([30.28]), spacing_deg=0.01, points=1)


def _candidate(sensor_type: str) -> Candidate:
    return Candidate(
        sat_id=1, name="T", sensor_type=sensor_type, mode="PMS",
        resolution_m=1.0, swath_km=100.0, omm={},
    )


def _offset(t0: datetime, hour: int) -> float:
    return (datetime(2026, 10, 21, hour, 0, tzinfo=UTC) - t0).total_seconds()


def test_optical_night_hits_are_dropped() -> None:
    t0 = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    c = _candidate("optical")
    c.hits = [[_offset(t0, 4)]]  # 12:00 北京，白天
    c.mask = np.array([True])
    _apply_sun(c, _grid(), t0, 10.0)
    assert c.mask.tolist() == [True]

    c.hits = [[_offset(t0, 15)]]  # 23:00 北京，夜间
    c.mask = np.array([True])
    _apply_sun(c, _grid(), t0, 10.0)
    assert c.mask.tolist() == [False]
    assert c.hits == [[]]


def test_hyperspectral_is_gated_but_sar_is_not() -> None:
    t0 = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    for sensor_type, expect in (("hyperspectral", False), ("sar", True)):
        c = _candidate(sensor_type)
        c.hits = [[_offset(t0, 15)]]
        c.mask = np.array([True])
        _apply_sun(c, _grid(), t0, 10.0)
        assert bool(c.mask[0]) is expect, sensor_type


def test_count_visits_merges_within_three_minutes() -> None:
    # 0/100/200/300 秒是同一轮过境的连续命中，500 秒才是下一轮
    assert _count_visits([[0, 100, 200, 300], [500]]) == 2
    assert _count_visits([[0, 181]]) == 2
    assert _count_visits([]) == 0
