#!/usr/bin/env python3
"""把 SIRI-Agent 的杭州高德市界转成 WGS84 GeoJSON，供 RS-Service / 前端使用。

用法：
    .venv/bin/python scripts/build_hangzhou_aoi.py \
        --source "/home/siri/桌面/SIRI-Agent/data/zhejiang_city_boundary_cache.json"
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

A = 6378245.0
EE = 0.00669342162296594323
OUT = Path(__file__).resolve().parents[1] / "data" / "aoi" / "hangzhou_330100.geojson"


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float, iterations: int = 4) -> tuple[float, float]:
    wlon, wlat = lon, lat
    for _ in range(iterations):
        glon, glat = wgs84_to_gcj02(wlon, wlat)
        wlon += lon - glon
        wlat += lat - glat
    return wlon, wlat


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default="/home/siri/桌面/SIRI-Agent/data/zhejiang_city_boundary_cache.json",
    )
    args = parser.parse_args()
    payload = json.loads(Path(args.source).read_text(encoding="utf-8"))
    city = next(item for item in payload["cities"] if item["city_adcode"] == "330100")
    ring = city["boundaries"][0]
    coordinates = []
    for lat, lon in ring:
        wlon, wlat = gcj02_to_wgs84(float(lon), float(lat))
        coordinates.append([round(wlon, 6), round(wlat, 6)])
    lons = [point[0] for point in coordinates]
    lats = [point[1] for point in coordinates]
    feature = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "code": "330100",
                    "name": "杭州市",
                    "source": "amap_district",
                    "datum": "gcj02->wgs84",
                    "accuracy": "approx",
                    "points": len(coordinates),
                    "bbox": [min(lons), min(lats), max(lons), max(lats)],
                },
                "geometry": {"type": "Polygon", "coordinates": [coordinates]},
            }
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(feature, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} points={len(coordinates)} bbox={min(lons):.3f},{min(lats):.3f},{max(lons):.3f},{max(lats):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
