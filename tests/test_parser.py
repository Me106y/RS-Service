from __future__ import annotations

import pytest

from rs_service.db.seed import load_seed
from rs_service.sync.parser import normalize_epoch, parse_omm

SAMPLE = {
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


def test_normalize_epoch() -> None:
    assert normalize_epoch("2026-10-18T22:54:30.780288") == "2026-10-18T22:54:30.780288Z"
    assert normalize_epoch("2026-10-18T22:54:30Z") == "2026-10-18T22:54:30Z"


def test_parse_omm() -> None:
    parsed = parse_omm(SAMPLE)
    assert parsed["sat_id"] == 40118
    assert parsed["name"] == "GAOFEN-2"
    assert parsed["epoch_utc"].endswith("Z")
    assert parsed["inclination_deg"] == pytest.approx(97.9972)
    assert parsed["omm_json"]


def test_parse_omm_requires_norad() -> None:
    with pytest.raises(ValueError):
        parse_omm({"OBJECT_NAME": "X"})


def test_seed_has_65_leo() -> None:
    payload = load_seed()
    sats = payload["satellites"]
    assert len(sats) == 65
    assert len({item["norad"] for item in sats}) == 65
    assert {item["orbit_regime"] for item in sats} == {"LEO"}
    assert sum(1 for item in sats if item["payload_public"]) == 65
    assert sum(1 for item in sats if not item["payload_public"]) == 0
    assert {item["category"] for item in sats} == {"commercial", "civil", "meteorology"}


def test_primary_swath_pairs_resolution_not_wide_field() -> None:
    from rs_service.db.seed import primary_swath_km

    assert primary_swath_km("2 m / 8 m；60 km；16 m 宽幅 800 km") == 60.0
    assert primary_swath_km("2 m / 8 m；90 km；16 m 宽幅 800 km") == 90.0
    assert primary_swath_km("5 m / 10 m；115 km；30 m 高光谱 60 km") == 115.0
    assert primary_swath_km("250 m 可见 / 1 km 红外；2900 km") == 2900.0
    assert primary_swath_km(None) is None
