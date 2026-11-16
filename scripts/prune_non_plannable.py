#!/usr/bin/env python3
"""删掉不参与成图规划的 12 颗星，使目录与可规划数一致（203）。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, select, func

from rs_service.db.models import (
    PassWindow,
    Satellite,
    SatelliteAlias,
    Sensor,
    SensorMode,
    SensorModeCorrection,
    TleCurrent,
    TleRecord,
)
from rs_service.db.session import session_scope

SEED = Path(__file__).resolve().parents[1] / "data" / "satellites_seed.json"
REMOVE = [43010, 43441, 43461, 43485, 43609, 44528, 45721, 49008, 49122, 57490, 58349, 65815]


def main() -> int:
    payload = json.loads(SEED.read_text(encoding="utf-8"))
    before = len(payload["satellites"])
    payload["satellites"] = [item for item in payload["satellites"] if int(item["norad"]) not in REMOVE]
    SEED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with session_scope() as session:
        session.execute(delete(TleCurrent).where(TleCurrent.sat_id.in_(REMOVE)))
        session.execute(delete(PassWindow).where(PassWindow.sat_id.in_(REMOVE)))
        session.execute(delete(TleRecord).where(TleRecord.sat_id.in_(REMOVE)))
        session.execute(delete(SensorModeCorrection).where(SensorModeCorrection.sat_id.in_(REMOVE)))
        session.execute(delete(SensorMode).where(SensorMode.sat_id.in_(REMOVE)))
        session.execute(delete(Sensor).where(Sensor.sat_id.in_(REMOVE)))
        session.execute(delete(SatelliteAlias).where(SatelliteAlias.sat_id.in_(REMOVE)))
        session.execute(delete(Satellite).where(Satellite.sat_id.in_(REMOVE)))
        total = session.scalar(select(func.count()).select_from(Satellite))
        modes = session.scalar(select(func.count()).select_from(SensorMode))
        sensors = session.scalar(select(func.count()).select_from(Sensor))
    print(json.dumps({"seed_before": before, "seed_after": len(payload["satellites"]), "db_satellites": total, "sensors": sensors, "sensor_modes": modes}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
