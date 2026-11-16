#!/usr/bin/env python3
"""离线导入 CelesTrak active JSON 的 TLE（CelesTrak 403/不可达时的兜底）。

用法：
    .venv/bin/python scripts/import_active_tle.py --active-json /tmp/active.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from sqlalchemy import select

from rs_service.db.models import DataSource, Satellite, TleCurrent, TleRecord
from rs_service.db.seed import ensure_source
from rs_service.db.session import session_scope
from rs_service.sync.parser import parse_omm


def _age_hours(epoch_utc: str) -> float:
    moment = datetime.fromisoformat(epoch_utc.replace("Z", "+00:00"))
    return (datetime.now(UTC) - moment).total_seconds() / 3600.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-json", default="/tmp/active.json")
    args = parser.parse_args()
    items = json.load(open(args.active_json, encoding="utf-8"))
    inserted = skipped = missing = 0
    with session_scope() as session:
        source = ensure_source(session)
        sat_ids = set(session.scalars(select(Satellite.sat_id)).all())
        for item in items:
            try:
                parsed = parse_omm(item)
            except (TypeError, ValueError, KeyError):
                skipped += 1
                continue
            if parsed["sat_id"] not in sat_ids:
                missing += 1
                continue
            exists = session.scalar(
                select(TleRecord.id).where(
                    TleRecord.sat_id == parsed["sat_id"],
                    TleRecord.epoch_utc == parsed["epoch_utc"],
                    TleRecord.source_id == source.id,
                )
            )
            if exists:
                skipped += 1
                continue
            record = TleRecord(
                sat_id=parsed["sat_id"],
                epoch_utc=parsed["epoch_utc"],
                line1=parsed["line1"],
                line2=parsed["line2"],
                omm_json=parsed["omm_json"],
                checksum_ok=parsed["checksum_ok"],
                mean_motion=parsed["mean_motion"],
                inclination_deg=parsed["inclination_deg"],
                eccentricity=parsed["eccentricity"],
                raan_deg=parsed["raan_deg"],
                argp_deg=parsed["argp_deg"],
                mean_anomaly_deg=parsed["mean_anomaly_deg"],
                bstar=parsed["bstar"],
                mean_motion_dot=parsed["mean_motion_dot"],
                mean_motion_ddot=parsed["mean_motion_ddot"],
                ephemeris_type=parsed["ephemeris_type"],
                element_set_no=parsed["element_set_no"],
                rev_at_epoch=parsed["rev_at_epoch"],
                source_id=source.id,
                fetched_at_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            session.add(record)
            session.flush()
            current = session.get(TleCurrent, parsed["sat_id"])
            if current is None:
                current = TleCurrent(sat_id=parsed["sat_id"])
                session.add(current)
            current.tle_id = record.id
            current.epoch_utc = parsed["epoch_utc"]
            current.age_hours = _age_hours(parsed["epoch_utc"])
            current.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            inserted += 1
    print(json.dumps({"inserted": inserted, "skipped": skipped, "not_in_catalog": missing}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
