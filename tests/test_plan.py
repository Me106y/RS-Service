from __future__ import annotations

from rs_service.astro import planner
from rs_service.db.session import session_scope


def test_missing_params_are_reported(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        try:
            planner.solve(session, mode="min_sats_for_coverage", aoi="330100",
                          max_resolution_m=None, target_coverage_pct=None)  # type: ignore[arg-type]
        except planner.PlanError as err:
            assert err.code == "missing_params"
            assert set(err.extra["missing"]) == {"max_resolution_m", "target_coverage_pct"}
        else:
            raise AssertionError("应当抛 missing_params")


def test_mode_specific_required_params(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        for mode, expect in [("max_coverage_for_n", "n_sats"),
                             ("min_sats_for_revisit", "target_max_gap_hours")]:
            try:
                planner.solve(session, mode=mode, aoi="330100",
                              max_resolution_m=10, target_coverage_pct=90)
            except planner.PlanError as err:
                assert expect in err.extra["missing"]
            else:
                raise AssertionError(f"{mode} 缺 {expect} 时应报错")


def test_unknown_aoi(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        try:
            planner.solve(session, mode="min_sats_for_coverage", aoi="北京市",
                          max_resolution_m=10, target_coverage_pct=90)
        except planner.PlanError as err:
            assert err.code == "aoi_not_found"
        else:
            raise AssertionError("应当抛 aoi_not_found")


def test_unsupported_mode(seeded_db: str) -> None:
    with session_scope(seeded_db) as session:
        try:
            planner.solve(session, mode="magic", aoi="330100",
                          max_resolution_m=10, target_coverage_pct=90)
        except planner.PlanError as err:
            assert err.code == "unsupported_mode"
            assert set(err.extra["allowed"]) == set(planner.MODES)
        else:
            raise AssertionError("应当抛 unsupported_mode")


def test_given_n_modes_do_not_require_coverage() -> None:
    """给定 N 颗时覆盖率是输出：不应把 target_coverage_pct 报成缺失参数。"""
    for mode in ("max_coverage_for_n", "min_wait_for_n"):
        missing = planner.missing_params(
            mode, aoi="330100", max_resolution_m=5,
            target_coverage_pct=None, n_sats=3, target_max_gap_hours=None,
        )
        assert missing == [], f"{mode} 不应要求 {missing}"

    missing = planner.missing_params(
        "max_coverage_for_n", aoi="330100", max_resolution_m=5,
        target_coverage_pct=None, n_sats=None, target_max_gap_hours=None,
    )
    assert missing == ["n_sats"]


def test_revisit_mode_does_not_require_coverage() -> None:
    missing = planner.missing_params(
        "min_sats_for_revisit", aoi="330100", max_resolution_m=10,
        target_coverage_pct=None, n_sats=None, target_max_gap_hours=24,
    )
    assert missing == []


def test_coverage_mode_still_requires_target() -> None:
    missing = planner.missing_params(
        "min_sats_for_coverage", aoi="330100", max_resolution_m=10,
        target_coverage_pct=None, n_sats=None, target_max_gap_hours=None,
    )
    assert missing == ["target_coverage_pct"]


def test_bad_ranges_are_rejected_before_solving() -> None:
    """范围错误要在算之前报出来，不能默默按 0 颗/负数窗口跑一遍。"""
    cases = [
        ({"mode": "max_coverage_for_n", "n_sats": 0, "hours": 72}, "n_sats"),
        ({"mode": "max_coverage_for_n", "n_sats": 3, "hours": 0}, "hours"),
        ({"mode": "min_sats_for_coverage", "target_coverage_pct": 120, "hours": 72},
         "target_coverage_pct"),
        ({"mode": "min_sats_for_revisit", "target_max_gap_hours": 0, "hours": 72},
         "target_max_gap_hours"),
    ]
    for extra, field in cases:
        try:
            planner.solve(
                None,  # type: ignore[arg-type] - 校验在碰数据库之前
                aoi="330100", max_resolution_m=10, **extra,
            )
        except planner.PlanError as err:
            assert err.code == "bad_params", (extra, err.code)
            assert err.extra.get("field") == field
        else:
            raise AssertionError(f"{extra} 应当报 bad_params")
