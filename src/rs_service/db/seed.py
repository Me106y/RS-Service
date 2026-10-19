from __future__ import annotations

import json
import re
from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..settings import settings
from .models import DataSource, Satellite, SatelliteAlias, Sensor

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "satellites_seed.json"


def normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().upper())


def load_seed(path: Path | None = None) -> dict[str, Any]:
    seed_path = path or DEFAULT_SEED_PATH
    return json.loads(Path(seed_path).read_text(encoding="utf-8"))


def ensure_source(session: Session) -> DataSource:
    source = session.scalar(select(DataSource).where(DataSource.code == settings.sync_source_code))
    if source is None:
        source = DataSource(
            code=settings.sync_source_code,
            name="CelesTrak GP",
            kind="gp",
            base_url=settings.celestrak_base_url,
            query_template="{base_url}?GROUP=active&FORMAT=json",
            auth_type="none",
            min_interval_s=3600,
            daily_budget=4,
            priority=10,
            enabled=True,
            terms_url="https://celestrak.org/usage-policy.php",
            notes="主源；每日 1 次，失败最多 3 次重试。",
        )
        session.add(source)
        session.flush()
    return source


def seed_catalog(session: Session, path: Path | None = None) -> dict[str, int]:
    payload = load_seed(path)
    ensure_source(session)
    inserted = updated = sensors = aliases = 0

    for item in payload["satellites"]:
        sat = session.get(Satellite, int(item["norad"]))
        if sat is None:
            sat = Satellite(sat_id=int(item["norad"]))
            session.add(sat)
            inserted += 1
        else:
            updated += 1

        sat.name = item["name"]
        sat.name_zh = item.get("name_zh")
        sat.operator = item.get("operator")
        sat.country = item.get("country", "CN")
        sat.category = item.get("category")
        sat.object_type = item.get("object_type", "payload")
        sat.status = item.get("status", "active")
        sat.orbit_regime = item.get("orbit_regime", "LEO")
        sat.is_domestic = bool(item.get("country") == "CN")
        sat.payload_public = bool(item.get("payload_public", True))
        sat.metadata_source = "phase2-seed"
        sat.updated_at = _now()

        sensor = session.scalar(select(Sensor).where(Sensor.sat_id == sat.sat_id))
        if sensor is None:
            sensor = Sensor(sat_id=sat.sat_id)
            session.add(sensor)
        sensor.sensor_name = item.get("sensor_type")
        sensor.sensor_type = item.get("sensor_type")
        sensor.raw_spec = item.get("raw_spec")
        sensor.confidence = item.get("confidence", "unknown")
        sensor.updated_at = _now()
        sensors += 1

        norm = normalize_alias(sat.name)
        alias = session.scalar(
            select(SatelliteAlias).where(
                SatelliteAlias.alias_norm == norm,
                SatelliteAlias.source == "seed",
            )
        )
        if alias is None:
            session.add(
                SatelliteAlias(
                    sat_id=sat.sat_id,
                    alias=sat.name,
                    alias_norm=norm,
                    source="seed",
                    kind="canonical",
                )
            )
            aliases += 1

    session.flush()
    return {
        "inserted": inserted,
        "updated": updated,
        "sensors": sensors,
        "aliases": aliases,
        "total": len(payload["satellites"]),
    }


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
