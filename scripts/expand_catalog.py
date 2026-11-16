#!/usr/bin/env python3
"""把 CelesTrak active 里可核的国产光学星补进 seed。

用法：
    .venv/bin/python scripts/expand_catalog.py            # 写入 data/satellites_seed.json
    .venv/bin/python scripts/expand_catalog.py --dry-run  # 只打印

参数置信度：official（官方型号页）/ vendor（厂商公开页）/ approx（公开资料推断）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "data" / "satellites_seed.json"
JL1 = "http://www.jl1.cn/visitlb_view.aspx?id=2514"
JL1_HOME = "http://www.jl1.cn/"

# 只列 NORAD 与型号参数都可核的星；8 颗 JILIN GAOFEN OBJECT 与 SAR/红外型号不在此列。
NEW_SATS: list[dict] = [
    # 吉林一号高分 03 / 早期 03–09
    {"norad": 44312, "name": "JILIN-1 GAOFEN 3A", "model": "GF-03A", "raw_spec": "0.75 m 全色 / 3 m 多光谱；17 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 41914, "name": "JILIN-1 03", "model": "JL1-03", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43022, "name": "JILIN-1 04", "model": "JL1-04", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43023, "name": "JILIN-1 05", "model": "JL1-05", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43024, "name": "JILIN-1 06", "model": "JL1-06", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43159, "name": "JILIN-1 07", "model": "JL1-07", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43160, "name": "JILIN-1 08", "model": "JL1-08", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    {"norad": 43943, "name": "JILIN-1 09", "model": "JL1-09", "raw_spec": "0.72 m 全色 / 2.9 m 多光谱；4.5 km", "confidence": "approx", "source_url": JL1_HOME},
    # 吉林一号宽幅
    {"norad": 49003, "name": "JILIN-1 KUANFU-01B", "model": "Kuanfu-01B", "raw_spec": "0.75 m 全色 / 3 m 多光谱；136 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 61190, "name": "JILIN-01 KUANFU 02B 2", "model": "Kuanfu-02B", "raw_spec": "0.5 m 全色 / 2 m 多光谱；150 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 61191, "name": "JILIN-01 KUANFU 02B 3", "model": "Kuanfu-02B", "raw_spec": "0.5 m 全色 / 2 m 多光谱；150 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 61192, "name": "JILIN-01 KUANFU 02B 4", "model": "Kuanfu-02B", "raw_spec": "0.5 m 全色 / 2 m 多光谱；150 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 61193, "name": "JILIN-01 KUANFU 02B 5", "model": "Kuanfu-02B", "raw_spec": "0.5 m 全色 / 2 m 多光谱；150 km", "confidence": "vendor", "source_url": JL1},
    {"norad": 61194, "name": "JILIN-01 KUANFU 02B 6", "model": "Kuanfu-02B", "raw_spec": "0.5 m 全色 / 2 m 多光谱；150 km", "confidence": "vendor", "source_url": JL1},
    # 天绘 / 资源 / 环境
    {"norad": 49049, "name": "TIANHUI 1-04", "model": "TH-1", "raw_spec": "5 m 全色 / 10 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 44207, "name": "TIANHUI 2-01A", "model": "TH-2", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 44209, "name": "TIANHUI 2-01B", "model": "TH-2", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 49071, "name": "TIANHUI 2-02A", "model": "TH-2", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 49072, "name": "TIANHUI 2-02B", "model": "TH-2", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 50572, "name": "TIANHUI 4A", "model": "TH-4", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 50573, "name": "TIANHUI 4B", "model": "TH-4", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 58199, "name": "TIANHUI 5A", "model": "TH-5", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 58201, "name": "TIANHUI 5B", "model": "TH-5", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 60211, "name": "TIANHUI 5C", "model": "TH-5", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 60212, "name": "TIANHUI 5D", "model": "TH-5", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 55836, "name": "TIANHUI 6A", "model": "TH-6", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 55839, "name": "TIANHUI 6B", "model": "TH-6", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 67300, "name": "TIANHUI 7", "model": "TH-7", "raw_spec": "2 m 全色 / 10 m 多光谱；30 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 38038, "name": "ZIYUAN 1-02C", "model": "ZY1-02C", "raw_spec": "2.36 m 全色 / 5 m 多光谱；54 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 57519, "name": "HJ-2F", "model": "HJ-2F", "raw_spec": "2 m 全色 / 8 m 多光谱；90 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    # 高分 11 / 14（公开资料为 2 m 级光学）
    {"norad": 43585, "name": "GAOFEN-11 01", "model": "GF-11", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 46396, "name": "GAOFEN-11 02", "model": "GF-11", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 49492, "name": "GAOFEN-11 03", "model": "GF-11", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 54818, "name": "GAOFEN-11 04", "model": "GF-11", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 60249, "name": "GAOFEN-11 05", "model": "GF-11", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 47231, "name": "GAOFEN-14 01", "model": "GF-14", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    {"norad": 66175, "name": "GAOFEN-14 02", "model": "GF-14", "raw_spec": "2 m 全色 / 8 m 多光谱；60 km", "confidence": "approx", "source_url": "https://www.cresda.com/"},
    # 国际公开：Sentinel-2 三颗，10 m / 290 km，用来补最后 1% 的空档
    {"norad": 40697, "name": "SENTINEL-2A", "model": "S2A", "raw_spec": "10 m 多光谱；290 km", "confidence": "official", "source_url": "https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-2"},
    {"norad": 42063, "name": "SENTINEL-2B", "model": "S2B", "raw_spec": "10 m 多光谱；290 km", "confidence": "official", "source_url": "https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-2"},
    {"norad": 60989, "name": "SENTINEL-2C", "model": "S2C", "raw_spec": "10 m 多光谱；290 km", "confidence": "official", "source_url": "https://www.esa.int/Applications/Observing_the_Earth/Copernicus/Sentinel-2"},
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = json.loads(SEED.read_text(encoding="utf-8"))
    existing = {int(item["norad"]) for item in payload["satellites"]}
    added = []
    for item in NEW_SATS:
        if int(item["norad"]) in existing:
            continue
        added.append(
            {
                "norad": int(item["norad"]),
                "name": item["name"],
                "operator": (
                    "长光卫星" if "JILIN" in item["name"]
                    else "ESA" if "SENTINEL-2" in item["name"]
                    else "CRESDA"
                ),
                "country": "EU" if "SENTINEL-2" in item["name"] else "CN",
                "category": "commercial" if "JILIN" in item["name"] else "civil",
                "object_type": "payload",
                "status": "active",
                "orbit_regime": "LEO",
                "payload_public": True,
                "sensor_type": "optical",
                "raw_spec": item["raw_spec"],
                "confidence": item["confidence"],
                "source_url": item["source_url"],
                "model": item["model"],
            }
        )
    print(f"seed={len(payload['satellites'])} add={len(added)} total={len(payload['satellites'])+len(added)}")
    for item in added:
        print(item["norad"], item["name"], item["raw_spec"], item["confidence"])
    if not args.dry_run and added:
        payload["satellites"].extend(added)
        SEED.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
