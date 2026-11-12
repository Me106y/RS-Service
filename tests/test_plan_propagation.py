"""SGP4 传播失败容错：部分时刻坏 → 掩码为不可覆盖；全部坏 → 单星异常。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from skyfield.api import load

from rs_service.coverage_engine.vendor import regional_opportunities as ro

TS = load.timescale()
START = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


class _StubModel:
    def __init__(self, bad_from: int) -> None:
        self.bad_from = bad_from

    def sgp4_array(self, jdi, jdf):
        n = len(jdi)
        err = np.zeros(n, dtype=np.int64)
        err[self.bad_from :] = 6
        r = np.tile(np.array([7000.0, 0.0, 0.0]), (n, 1))
        v = np.tile(np.array([0.0, 7.5, 0.0]), (n, 1))
        return err, r, v


class _StubSat:
    norad_id = 99999

    def __init__(self, bad_from: int) -> None:
        self.model = _StubModel(bad_from)


def _times(n: int) -> tuple[list[datetime], object]:
    dt_list = [START + timedelta(minutes=10 * i) for i in range(n)]
    return dt_list, TS.from_datetimes(dt_list)


def test_partial_propagation_failure_is_masked() -> None:
    dt_list, t = _times(6)
    sat = _StubSat(bad_from=3)
    prepared = ro._prepare_ecef_geometry(sat, t, dt_list, {})
    assert prepared["valid"].tolist() == [True, True, True, False, False, False]
    assert np.isfinite(prepared["r"]).all()

    covered = ro._complex_state_for_points(
        prepared,
        np.asarray([30.0]),
        np.asarray([120.0]),
        {"sensor_type": "SimpleCone", "sensor_params": [10.0], "min_elevation_deg": 10.0},
    )
    assert covered.shape == (6, 1)
    assert not covered[3:].any()


def test_all_steps_fail_raises_satellite_error() -> None:
    dt_list, t = _times(3)
    sat = _StubSat(bad_from=0)
    with pytest.raises(ro.SatellitePropagationError):
        ro._prepare_ecef_geometry(sat, t, dt_list, {})
