"""Engine-level golden test.

Fixtures are the reference backend's 110-satellite catalog, TLE snapshot and
city boundaries.  ``expected_task1.json`` was produced by the reference
implementation with the same payload and data; the vendored engine must
reproduce the numbers exactly (same code, same inputs).
"""

from __future__ import annotations

import json
from pathlib import Path

from siriagent_skill.runner import run_skill  # noqa: E402

import rs_service.coverage_engine  # noqa: F401  (adds vendor/ to sys.path)

FIXTURE = Path(__file__).parent / "fixtures" / "coverage_engine"


def test_task1_matches_reference_golden() -> None:
    expected = json.loads((FIXTURE / "expected_task1.json").read_text(encoding="utf-8"))
    result = run_skill(expected["payload"], root=FIXTURE)

    assert result["status"] == expected["status"]
    assert result["feasible"] == expected["feasible"]
    assert result["optimality"] == expected["optimality"]
    assert result["summary_cards"] == expected["summary_cards"]

    got = result["selection"]["recommended_result"]
    want = expected["recommended_result"]
    assert got["selected_sat_ids"] == want["selected_sat_ids"]
    assert got["achieved_area_pct"] == want["achieved_area_pct"]
