#!/usr/bin/env python3
"""下载解译模型权重（git 不入库，按需拉取并校验 SHA256）。

用法：
    .venv/bin/python scripts/fetch_models.py            # 全部缺失的
    .venv/bin/python scripts/fetch_models.py yolox      # 只拉目标检测

权重来源与许可：
- yolox_s.pth：Megvii-BaseDetection/YOLOX 官方 COCO 预训练（Apache-2.0）。
  国内直连 GitHub Releases 常被限速，优先走 ghproxy.net 镜像。
- sparsemask_openearthmap.onnx：M3 语义分割（CC BY-NC-SA，限所内）。
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "data" / "models"

# key -> (相对路径, 镜像 URL 列表, SHA256, 许可)
CATALOG = {
    "yolox": (
        "object_detection/yolox_s.pth",
        [
            "https://ghproxy.net/https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth",
            "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.pth",
        ],
        "f55ded7181e1b0c13285c56e7790b8f0e8f8db590fe4edb37f0b7f345c913a30",
        "Apache-2.0",
    ),
    "sparsemask": (
        "semantic_segmentation/sparsemask_openearthmap.onnx",
        [
            "https://hf-mirror.com/geobase/sparsemask/resolve/main/onnx/model.onnx",
            "https://huggingface.co/geobase/sparsemask/resolve/main/onnx/model.onnx",
        ],
        "83c6ff78ff65818a38ed20ab562ccb9b20927bac6ef93021b3066a712678193d",
        "MIT code / OpenEarthMap weights (CC BY-NC-SA, 限所内)",
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(key: str) -> bool:
    rel, urls, expected, license_name = CATALOG[key]
    target = MODELS / rel
    if target.is_file():
        actual = sha256(target)
        if expected == "TBD" or actual == expected:
            print(f"[skip] {rel} 已存在（sha256 {actual[:12]}…，{license_name}）")
            return True
        print(f"[warn] {rel} 校验不符，重新下载")
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    for url in urls:
        try:
            print(f"[get ] {url}")
            urllib.request.urlretrieve(url, target)
            actual = sha256(target)
            if expected != "TBD" and actual != expected:
                print(f"[fail] sha256 不符：{actual}")
                target.unlink()
                continue
            print(f"[ok  ] {rel}（sha256 {actual}，{license_name}）")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {url} -> {exc}")
            target.unlink(missing_ok=True)
    return False


def main(argv: list[str]) -> int:
    keys = argv[1:] or [key for key in CATALOG if not (MODELS / CATALOG[key][0]).is_file()]
    if not keys:
        print("模型齐全。")
        return 0
    ok = True
    for key in keys:
        if key not in CATALOG:
            print(f"未知模型：{key}；可选 {', '.join(CATALOG)}")
            return 2
        ok = fetch(key) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
