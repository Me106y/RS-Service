from __future__ import annotations

from sqlalchemy import func, select

from rs_service.db.models import Satellite, SensorMode, SensorModeCorrection
from rs_service.db.session import session_scope
from rs_service.sync.sensor_modes import parse_modes, rebuild


def test_parse_optical_multi_mode() -> None:
    """GF-1 的 2 m 窄幅与 16 m 宽幅必须拆成两个成对模式。"""
    r = parse_modes("2 m / 8 m；60 km；16 m 宽幅 800 km", 800.0)
    assert [(m.mode, m.resolution_m, m.swath_km) for m in r.modes] == [
        ("PMS", 2.0, 60.0),
        ("WFV", 16.0, 800.0),
    ]
    assert r.review is None


def test_parse_named_sar_modes() -> None:
    """GF-3 三个模式，中间那个幅宽缺失必须留空而不是拿 650 km 顶上。"""
    r = parse_modes("C 波段；1 m 聚束 / 3 m 条带 / 10–500 m 扫描；10–650 km", 650.0)
    got = {m.mode: (m.resolution_m, m.swath_km) for m in r.modes}
    assert got["聚束"] == (1.0, 10.0)
    assert got["条带"][1] is None
    assert got["扫描"] == (500.0, 650.0)
    assert r.review


def test_parse_swath_range_is_conservative() -> None:
    """20–100 km 取 20，宁可低估覆盖。"""
    r = parse_modes("S 波段；5 m；20–100 km", 100.0)
    assert r.modes[0].resolution_m == 5.0
    assert r.modes[0].swath_km == 20.0
    assert "保守" in (r.review or "")


def test_parse_single_named_mode_not_flagged() -> None:
    r = parse_modes("X 波段 InSAR；0.5 m 聚束；80 km 拼接", 80.0)
    assert [(m.mode, m.resolution_m, m.swath_km) for m in r.modes] == [("聚束", 0.5, 80.0)]
    assert r.review is None


def test_parse_spectral_pair_is_not_pms() -> None:
    """250 m 可见 / 1 km 红外 是气象星，不该被当成全色/多光谱。"""
    r = parse_modes("250 m 可见 / 1 km 红外；2900 km", 2900.0)
    assert r.modes[0].mode == "默认"
    assert r.modes[0].resolution_m == 250.0


def test_rebuild_persists_and_is_idempotent(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        first = rebuild(session)
    assert first["ok"] is True
    assert first["satellites"] == 65
    assert first["modes"] == 71
    assert first["overrides"] == []

    with session_scope(seeded_db) as session:
        second = rebuild(session)
        total = session.scalar(select(func.count()).select_from(SensorMode))
        gf1_wfv = session.execute(
            select(SensorMode)
            .where(SensorMode.sat_id == 39150, SensorMode.mode_name == "WFV")
        ).scalar_one()
        corr = session.scalar(select(func.count()).select_from(SensorModeCorrection))
    assert second["modes"] == first["modes"]
    assert total == 71, "重复 rebuild 不应叠加"
    assert gf1_wfv.swath_km == 800.0
    assert gf1_wfv.confidence == "official"
    assert corr >= 0


def test_private_payload_absent(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        session.get(Satellite, 40118).payload_public = False
        rebuild(session)
        ids = set(session.scalars(select(SensorMode.sat_id)))
    assert 40118 not in ids, "payload_public=false 的卫星不得进入模式目录"
