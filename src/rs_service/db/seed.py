from __future__ import annotations

import json
import math
import re
from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..settings import settings
from .models import Aoi, DataSource, Satellite, SatelliteAlias, Sensor

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "satellites_seed.json"
AOI_PATH = Path(__file__).resolve().parents[3] / "data" / "aoi" / "hangzhou_330100.geojson"


def max_km(raw: str | None) -> float | None:
    """从传感器文本里取最大的 km 数值，作为幅宽近似（仅供前端画足迹）。"""
    if not raw:
        return None
    values = [float(item) for item in re.findall(r"(\d+(?:\.\d+)?)\s*km", raw)]
    return max(values) if values else None


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
        sensor.swath_km = max_km(item.get("raw_spec"))
        if sat.orbit_regime == "GEO":
            sensor.swath_km = 2000.0
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


def seed_aoi(session: Session, path: Path | None = None) -> dict[str, int]:
    """把 data/aoi/*.geojson 写入 aoi 表，支持省 / 市 / 区县三级。"""
    folder = (path.parent if path else AOI_PATH.parent)
    files = sorted(folder.glob("*.geojson")) if path is None else [path]
    inserted = updated = 0
    levels: dict[str, int] = {}
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            props = feature["properties"]
            code = str(props.get("code") or props.get("adcode"))
            ring = feature["geometry"]["coordinates"][0]
            wkt = "POLYGON((" + ",".join(f"{p[0]} {p[1]}" for p in ring) + "))"
            bbox = props.get("bbox") or [
                min(p[0] for p in ring), min(p[1] for p in ring),
                max(p[0] for p in ring), max(p[1] for p in ring),
            ]
            row = session.scalar(select(Aoi).where(Aoi.code == code))
            if row is None:
                row = Aoi(code=code)
                session.add(row)
                inserted += 1
            else:
                updated += 1
            row.name = props.get("name", code)
            row.level = props.get("level", "city")
            row.adcode = str(props.get("adcode", code))
            row.geom_wkt = wkt
            row.min_lon, row.min_lat, row.max_lon, row.max_lat = bbox
            row.area_km2 = props.get("area_km2") or _ring_area_km2(ring)
            row.source = props.get("source", "amap_district")
            row.datum = "wgs84"
            row.accuracy = props.get("accuracy", "approx")
            levels[row.level] = levels.get(row.level, 0) + 1
    session.flush()
    return {"inserted": inserted, "updated": updated, "levels": levels}


def _ring_area_km2(ring: list[list[float]]) -> float:
    if len(ring) < 3:
        return 0.0
    lat0 = sum(point[1] for point in ring) / len(ring)
    kx = 111.320 * math.cos(math.radians(lat0))
    ky = 110.574
    total = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index][0] * kx, ring[index][1] * ky
        x2, y2 = ring[index + 1][0] * kx, ring[index + 1][1] * ky
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0
