#!/usr/bin/env python3
"""把 CelesTrak active 的 PlanetScope Flock 补进 seed（3 m / 24 km，厂商参数）。

用途：验证「≤10m 光学 100%/24h」在现有国产目录之外是否可达。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "satellites_seed.json"
SOURCE = "https://docs.planet.com/data/imagery/planetscope/"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-json", default="/tmp/active.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    active = json.load(open(args.active_json, encoding="utf-8"))
    payload = json.loads(SEED.read_text(encoding="utf-8"))
    existing = {int(item["norad"]) for item in payload["satellites"]}
    added = []
    for item in active:
        name = str(item.get("OBJECT_NAME") or "").strip()
        upper = name.upper()
        if "FLOCK" not in upper:
            continue
        norad = int(item["NORAD_CAT_ID"])
        if norad in existing:
            continue
        added.append(
            {
                "norad": norad,
                "name": name,
                "operator": "Planet Labs",
                "country": "US",
                "category": "commercial",
                "object_type": "payload",
                "status": "active",
                "orbit_regime": "LEO",
                "payload_public": True,
                "sensor_type": "optical",
                "raw_spec": "3 m 多光谱；24 km",
                "confidence": "vendor",
                "source_url": SOURCE,
                "model": "PlanetScope SuperDove",
            }
        )
    print(f"seed={len(payload['satellites'])} add={len(added)} total={len(payload['satellites'])+len(added)}")
    if not args.dry_run and added:
        payload["satellites"].extend(added)
        SEED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
