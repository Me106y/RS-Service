#!/usr/bin/env python3
"""构建浙江省 / 11 地市 / 区县边界（DataV 高德数据，GCJ-02 -> WGS84）。

用法：
    .venv/bin/python scripts/build_zhejiang_aoi.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rs_service.geo.datum import gcj02_to_wgs84, ring_area_km2, simplify_ring  # noqa: E402

BASE = "https://geo.datav.aliyun.com/areas_v3/bound/{}.json"
CACHE = Path("/tmp/zj")
OUT = Path(__file__).resolve().parents[1] / "data" / "aoi"
TOL = 0.0015  # 约 150 m，简化后仍可作规划底图


def fetch(code: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"zj_{code}.json"
    if not path.is_file():
        with urllib.request.urlopen(BASE.format(code), timeout=60) as resp:
            path.write_bytes(resp.read())
    return json.loads(path.read_text(encoding="utf-8"))


def main_ring(geometry: dict) -> list[list[float]]:
    coords = geometry["coordinates"]
    polys = coords if geometry["type"] == "MultiPolygon" else [coords]
    return max((p[0] for p in polys), key=len)


def convert(feature: dict) -> dict:
    props = feature["properties"]
    ring = main_ring(feature["geometry"])
    wgs = [gcj02_to_wgs84(float(x), float(y)) for x, y in ring]
    simple = simplify_ring(wgs, TOL)
    closed = simple if simple[0] == simple[-1] else simple + [simple[0]]
    lons = [p[0] for p in closed]
    lats = [p[1] for p in closed]
    return {
        "type": "Feature",
        "properties": {
            "code": str(props["adcode"]),
            "adcode": str(props["adcode"]),
            "name": props["name"],
            "level": props["level"],
            "parent": str(props.get("parent", {}).get("adcode", "")),
            "points": len(closed),
            "area_km2": round(ring_area_km2(closed), 1),
            "bbox": [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)],
            "source": "datav_amap",
            "datum": "gcj02->wgs84",
            "accuracy": "approx",
        },
        "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in closed]]},
    }


def write(name: str, features: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {"type": "FeatureCollection", "features": features}
    path = OUT / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"{name:34s} {len(features):>3} 个  {path.stat().st_size / 1024:>7.0f} KB")


def main() -> int:
    prov = fetch("330000")
    cities = fetch("330000_full")
    write("zhejiang_330000.geojson", [convert(f) for f in prov["features"]])
    write("zhejiang_cities.geojson", [convert(f) for f in cities["features"]])

    districts = []
    for city in cities["features"]:
        code = str(city["properties"]["adcode"])
        try:
            payload = fetch(f"{code}_full")
        except Exception as exc:  # pragma: no cover - 网络异常
            print("skip", code, exc)
            continue
        districts += [convert(f) for f in payload["features"]]
    write("zhejiang_districts.geojson", districts)
    print("总点数", sum(f["properties"]["points"] for f in districts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
