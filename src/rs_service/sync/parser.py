from __future__ import annotations

import json
from typing import Any


def normalize_epoch(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("empty epoch")
    if raw.endswith("Z"):
        return raw
    return raw + "Z"


def parse_omm(item: dict[str, Any]) -> dict[str, Any]:
    """把 CelesTrak GP 的 OMM/JSON 条目归一化为 tle_record 字段。"""
    if "NORAD_CAT_ID" not in item:
        raise ValueError("missing NORAD_CAT_ID")
    return {
        "sat_id": int(item["NORAD_CAT_ID"]),
        "name": str(item.get("OBJECT_NAME") or "").strip(),
        "epoch_utc": normalize_epoch(str(item.get("EPOCH") or "")),
        "omm_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
        "mean_motion": _float(item.get("MEAN_MOTION")),
        "inclination_deg": _float(item.get("INCLINATION")),
        "eccentricity": _float(item.get("ECCENTRICITY")),
        "raan_deg": _float(item.get("RA_OF_ASC_NODE")),
        "argp_deg": _float(item.get("ARG_OF_PERICENTER")),
        "mean_anomaly_deg": _float(item.get("MEAN_ANOMALY")),
        "bstar": _float(item.get("BSTAR")),
        "mean_motion_dot": _float(item.get("MEAN_MOTION_DOT")),
        "mean_motion_ddot": _float(item.get("MEAN_MOTION_DDOT")),
        "ephemeris_type": _int(item.get("EPHEMERIS_TYPE")),
        "element_set_no": _int(item.get("ELEMENT_SET_NO")),
        "rev_at_epoch": _int(item.get("REV_AT_EPOCH")),
        "line1": None,
        "line2": None,
        "checksum_ok": None,
    }


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
