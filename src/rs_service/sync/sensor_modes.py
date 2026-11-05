"""把 sensor.raw_spec 的自由文本解析成 (分辨率, 幅宽) 成对的成像模式。

口径（与 imaging-plan 契约一致）：

1. 分辨率取该模式的最优值（全色优先于多光谱），不按波段细分；
2. 幅宽必须与该模式成对，不得取全星最大幅宽；
3. 区间值一律取保守端——幅宽取下界、分辨率取上界；宁可低估覆盖，不高估。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db.models import Satellite, Sensor, SensorMode, SensorModeCorrection, utcnow_iso

PARSER_VERSION = "v0.1"
IMAGING_TYPES = ("optical", "sar", "hyperspectral")
DEFAULT_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "sensor_mode_overrides.json"
)

_NUM = r"(\d+(?:\.\d+)?)"
_RE_NAMED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:[–-]\s*(\d+(?:\.\d+)?))?\s*m\s*(聚束|条带|扫描)")
_RE_WFV = re.compile(_NUM + r"\s*m\s*宽幅\s*" + _NUM + r"\s*km")
_RE_HSI = re.compile(_NUM + r"\s*m\s*高光谱\s*" + _NUM + r"\s*km")
_RE_RES = re.compile(_NUM + r"\s*m(?![a-z])")
_RE_SW_RANGE = re.compile(_NUM + r"\s*[–-]\s*" + _NUM + r"\s*km")
_RE_SW_ONE = re.compile(_NUM + r"\s*km")
_RE_PAIR = re.compile(_NUM + r"\s*m\s*/\s*" + _NUM + r"\s*m")


@dataclass
class ParsedMode:
    mode: str
    resolution_m: float
    swath_km: float | None
    note: str | None = None


@dataclass
class ParseResult:
    modes: list[ParsedMode] = field(default_factory=list)
    review: str | None = None


def _clean(raw: str) -> str:
    return raw.replace("～", "–").replace("—", "–").replace("大约", "约").strip()


def parse_modes(raw: str | None, fallback_swath: float | None) -> ParseResult:
    """解析单颗星的 raw_spec；无法配对的模式保留 swath_km=None 并给出 review 说明。"""
    if not raw:
        return ParseResult(modes=[], review="raw_spec 为空")
    text = _clean(raw)
    segs = [x.strip() for x in text.split("；") if x.strip()]

    extra: list[ParsedMode] = []
    for seg in segs:
        m = _RE_WFV.search(seg)
        if m:
            extra.append(ParsedMode("WFV", float(m.group(1)), float(m.group(2))))
        m = _RE_HSI.search(seg)
        if m:
            extra.append(ParsedMode("HSI", float(m.group(1)), float(m.group(2))))

    res_segs = [x for x in segs if _RE_RES.search(x) and "宽幅" not in x and "高光谱" not in x]
    sw_segs = [x for x in segs if "km" in x]

    named = [
        (nm, float(lo), float(hi) if hi else None)
        for seg in segs
        for lo, hi, nm in _RE_NAMED.findall(seg)
    ]

    if named:
        sw_range = _RE_SW_RANGE.search("；".join(sw_segs))
        sw_min = float(sw_range.group(1)) if sw_range else None
        sw_max = float(sw_range.group(2)) if sw_range else fallback_swath
        named.sort(key=lambda t: t[1])
        review = None
        if len(named) == 1:
            pairs = [(named[0][0], named[0][1], fallback_swath)]
        elif len(named) == 2 and sw_min is not None:
            pairs = [(named[0][0], named[0][1], sw_min), (named[1][0], named[1][1], sw_max)]
            review = "幅宽区间按分辨率升序配对"
        elif len(named) >= 3 and sw_min is not None:
            top = named[-1]
            pairs = [
                (named[0][0], named[0][1], sw_min),
                (named[1][0], named[1][1], None),
                (top[0], top[2] if top[2] is not None else top[1], sw_max),
            ]
            review = "中间模式幅宽未给出；扫描模式按最差分辨率配最大幅宽"
        else:
            pairs = [(n[0], n[1], fallback_swath) for n in named]
            review = "多模式但幅宽无法配对"
        modes = [ParsedMode(a, b, c) for a, b, c in pairs] + extra
        return ParseResult(modes=modes, review=review)

    res_vals: list[float] = []
    for seg in res_segs:
        res_vals += [float(v) for v in _RE_RES.findall(seg)]
    if not res_vals:
        return ParseResult(modes=[], review="无法提取分辨率")

    sw_val: float | None = None
    sw_rng: tuple[float, float] | None = None
    for seg in sw_segs:
        if "宽幅" in seg or "高光谱" in seg:
            continue
        rng = _RE_SW_RANGE.search(seg)
        if rng:
            sw_rng = (float(rng.group(1)), float(rng.group(2)))
            continue
        one = _RE_SW_ONE.search(seg)
        if one:
            sw_val = float(one.group(1))

    review = None
    if sw_rng:
        sw_val = sw_rng[0]  # 保守下界
        review = f"幅宽为区间 {sw_rng[0]:g}–{sw_rng[1]:g} km，取保守下界"
    if sw_val is None:
        sw_val = fallback_swath

    pair = _RE_PAIR.search(" ".join(res_segs))
    spectral_pair = "可见" in text and "红外" in text
    if len(res_vals) >= 2 and ("全色" in text or (pair and not spectral_pair)):
        modes = [ParsedMode("PMS", min(res_vals), sw_val)]
    else:
        modes = [ParsedMode("默认", min(res_vals), sw_val)]
    return ParseResult(modes=modes + extra, review=review)


def load_overrides(path: Path | None = None) -> dict[str, dict]:
    p = Path(path) if path else DEFAULT_OVERRIDES_PATH
    if not p.is_file():
        return {}
    payload = json.loads(p.read_text(encoding="utf-8"))
    return {item["name"]: item for item in payload.get("overrides", [])}


def _apply_override(
    sat_name: str, result: ParseResult, overrides: dict[str, dict]
) -> tuple[list[SensorModeCorrection], bool]:
    """人工核对值覆盖解析结果；返回 (纠错流水, 是否命中)。"""
    spec = overrides.get(sat_name)
    if not spec:
        return [], False
    target = spec.get("mode")
    hits = 0
    for m in result.modes:
        if target and m.mode != target:
            continue
        if "swath_km" in spec:
            m.swath_km = float(spec["swath_km"])
            hits += 1
        if "resolution_m" in spec:
            m.resolution_m = float(spec["resolution_m"])
        m.note = spec.get("note") or m.note
    if not hits:
        return [], False
    corr = [
        SensorModeCorrection(
            sat_id=0,
            mode_name=target,
            field="swath_km",
            raw=spec.get("raw"),
            corrected=str(spec["swath_km"]),
            rule="manual_override",
            parser_version=PARSER_VERSION,
        )
    ]
    return corr, True


def rebuild(
    session: Session,
    overrides_path: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """重建 sensor_mode：只覆盖成像类型 ∧ 载荷公开 ∧ LEO 的卫星。"""
    overrides = load_overrides(overrides_path)
    rows = session.execute(
        select(Satellite, Sensor)
        .join(Sensor, Sensor.sat_id == Satellite.sat_id)
        .where(
            Sensor.sensor_type.in_(IMAGING_TYPES),
            Satellite.payload_public.is_(True),
            Satellite.orbit_regime == "LEO",
        )
        .order_by(Satellite.sat_id)
    ).all()

    created = 0
    review: list[dict] = []
    overridden: list[str] = []
    corrections: list[SensorModeCorrection] = []

    for sat, sensor in rows:
        result = parse_modes(sensor.raw_spec, sensor.swath_km)
        corr, hit = _apply_override(sat.name, result, overrides)
        if hit:
            overridden.append(sat.name)
            for c in corr:
                c.sat_id = sat.sat_id
            corrections.extend(corr)
        if result.review and not hit:
            review.append({"sat_id": sat.sat_id, "name": sat.name, "reason": result.review})

        usable = [m for m in result.modes if m.swath_km is not None]
        if not usable:
            review.append({"sat_id": sat.sat_id, "name": sat.name, "reason": "无可用模式"})
            continue

        default_mode = max(usable, key=lambda m: (m.swath_km or 0.0, -m.resolution_m))
        if not dry_run:
            session.execute(delete(SensorMode).where(SensorMode.sat_id == sat.sat_id))
            session.execute(
                delete(SensorModeCorrection).where(SensorModeCorrection.sat_id == sat.sat_id)
            )

        for m in result.modes:
            if m.swath_km is None:
                continue
            confidence = "approx" if (result.review or hit) else "official"
            note = m.note or result.review
            if not dry_run:
                session.add(
                    SensorMode(
                        sat_id=sat.sat_id,
                        mode_name=m.mode,
                        resolution_m=m.resolution_m,
                        swath_km=float(m.swath_km),
                        is_default=(m is default_mode),
                        confidence=confidence,
                        note=note,
                        source_text=sensor.raw_spec,
                        parser_version=PARSER_VERSION,
                        updated_at=utcnow_iso(),
                    )
                )
            created += 1

        for c in corr:
            if not dry_run:
                session.add(c)

    if not dry_run:
        session.flush()

    return {
        "ok": True,
        "dry_run": dry_run,
        "parser_version": PARSER_VERSION,
        "satellites": len(rows),
        "modes": created,
        "overrides": overridden,
        "review": review,
    }
