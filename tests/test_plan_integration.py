"""真实数据库的规划集成测试。

默认跳过：需要本机 ``rs_service`` 数据库里有卫星、TLE 与 AOI。CI 只跑单元测试。
本地运行：

    RS_RUN_INTEGRATION=1 uv run pytest tests/test_plan_integration.py -q
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from rs_service.coverage_engine.adapter import PlanSpec, run_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("RS_RUN_INTEGRATION") != "1",
    reason="需要本机 rs_service 数据库；设置 RS_RUN_INTEGRATION=1 运行",
)


def test_task1_runs_against_local_database() -> None:
    result = run_plan(
        PlanSpec(
            operation="task1",
            aoi_code="330100",
            start_utc=datetime.now(UTC) - timedelta(hours=1),
            horizon_h=8,
            sensor_types=("optical",),
            max_resolution_m=10,
            temporal_coverage_target_pct=5,
            area_target_pct=90,
            allow_area_fallback=False,
            method="greedy",
        )
    )
    assert result["status"] == "completed"
    assert result["candidate_count"] > 0
    assert all(item["name"] != "GF-1" for item in result["blocked_satellites"])
