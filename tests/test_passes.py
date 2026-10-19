from __future__ import annotations

from datetime import UTC, datetime

from rs_service.astro.passes import find_passes
from rs_service.astro.propagate import satellite_from_omm

OMM = {
    "OBJECT_NAME": "GAOFEN-2",
    "OBJECT_ID": "2014-049A",
    "EPOCH": "2026-10-18T22:54:30.780288",
    "MEAN_MOTION": 14.8104031,
    "ECCENTRICITY": 0.00070703,
    "INCLINATION": 97.9972,
    "RA_OF_ASC_NODE": 355.9574,
    "ARG_OF_PERICENTER": 181.1663,
    "MEAN_ANOMALY": 178.9534,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 40118,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 12345,
    "BSTAR": 0.0001,
    "MEAN_MOTION_DOT": 1e-05,
    "MEAN_MOTION_DDOT": 0,
}


def test_find_passes_over_hangzhou() -> None:
    sat = satellite_from_omm(OMM)
    passes = find_passes(
        sat,
        30.27,
        120.16,
        datetime(2026, 10, 19, tzinfo=UTC),
        datetime(2026, 10, 20, tzinfo=UTC),
        min_elevation_deg=5.0,
        step_s=30,
    )
    assert passes
    for item in passes:
        assert item["max_elev_deg"] >= 5.0
        assert item["aos_utc"] < item["tca_utc"] < item["los_utc"]
        assert item["duration_s"] > 0
        assert isinstance(item["optical_imaging_ok"], bool)
