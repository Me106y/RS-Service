"""把数据库裁剪到 ``data/satellites_seed.json`` 的卫星集。

2026-11-05 的决定：成图规划只保留有完整几何数据的 65 颗（61 光学 + 4 高光谱）。
参考引擎拒绝用默认入射角带算 SAR，数据库也没有 12 颗光学的幅宽，所以这 37 颗
不再作为成图候选；为避免“目录 102、可算 65”的长期不一致，直接从运行库移除。

脚本可重跑：以 seed 的 NORAD 集合为准，删除不在集合里的卫星及其从属行。被删的
卫星/传感器/TLE 先归档到 ``data/satellites_retired.json``，可用于人工恢复。

用法::

    uv run python scripts/prune_satellites.py            # 预演
    uv run python scripts/prune_satellites.py --apply    # 执行
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from rs_service.db.models import (
    PassWindow,
    Satellite,
    SatelliteAlias,
    Sensor,
    SensorMode,
    SensorModeCorrection,
    SyncItemError,
    TleCurrent,
    TleRecord,
)
from rs_service.db.session import session_scope

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "satellites_seed.json"
ARCHIVE = ROOT / "data" / "satellites_retired.json"


def _row(obj: Any) -> dict[str, Any]:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真正删除；默认只预演")
    parser.add_argument("--seed", type=Path, default=SEED)
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()

    keep = {int(row["norad"]) for row in json.loads(args.seed.read_text(encoding="utf-8"))["satellites"]}
    with session_scope() as session:
        all_ids = set(session.scalars(select(Satellite.sat_id)))
        removed = sorted(all_ids - keep)
        missing = sorted(keep - all_ids)
        print(f"keep={len(keep)} db={len(all_ids)} remove={len(removed)} missing={len(missing)}")
        if missing:
            print(f"警告：seed 里有 {len(missing)} 颗不在数据库：{missing}")
        if not removed:
            print("数据库已经是目标集合，无需处理")
            return 0

        satellites = session.scalars(select(Satellite).where(Satellite.sat_id.in_(removed))).all()
        sensors = session.scalars(select(Sensor).where(Sensor.sat_id.in_(removed))).all()
        tles = session.scalars(select(TleRecord).where(TleRecord.sat_id.in_(removed))).all()
        aliases = session.scalars(
            select(SatelliteAlias).where(SatelliteAlias.sat_id.in_(removed))
        ).all()
        archive = {
            "generated_at": "2026-11-05",
            "reason": "成图规划只保留有完整几何数据的 65 颗；其余 37 颗归档",
            "satellites": [_row(item) for item in satellites],
            "sensors": [_row(item) for item in sensors],
            "tle_records": [_row(item) for item in tles],
            "aliases": [_row(item) for item in aliases],
        }
        print(
            f"archive: satellites={len(satellites)} sensors={len(sensors)} "
            f"tle_records={len(tles)} aliases={len(aliases)}"
        )
        if not args.apply:
            print("预演结束；加 --apply 执行")
            return 0

        args.archive.write_text(
            json.dumps(archive, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        session.execute(
            delete(SensorModeCorrection).where(SensorModeCorrection.sat_id.in_(removed))
        )
        session.execute(delete(SensorMode).where(SensorMode.sat_id.in_(removed)))
        session.execute(delete(PassWindow).where(PassWindow.sat_id.in_(removed)))
        session.execute(delete(TleCurrent).where(TleCurrent.sat_id.in_(removed)))
        session.execute(delete(TleRecord).where(TleRecord.sat_id.in_(removed)))
        session.execute(delete(Sensor).where(Sensor.sat_id.in_(removed)))
        session.execute(delete(SatelliteAlias).where(SatelliteAlias.sat_id.in_(removed)))
        session.execute(delete(SyncItemError).where(SyncItemError.sat_id.in_(removed)))
        session.execute(delete(Satellite).where(Satellite.sat_id.in_(removed)))
        print(f"已删除 {len(removed)} 颗；归档写入 {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
