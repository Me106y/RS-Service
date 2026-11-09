from __future__ import annotations

from pathlib import Path

from rs_service.report import render_report, write_report


def fake_result() -> dict:
    return {
        "status": "completed",
        "operation": "task_1_min_satellites_temporal",
        "feasible": True,
        "optimality": "feasible_heuristic",
        "targets": {"temporal_coverage_pct": 90.0, "area_pct": 90.0},
        "window": {"requested_horizon_h": 8, "used_horizon_h": 8, "expanded": False},
        "tle": {"max_age_hours": 6.0, "degraded": False},
        "summary_cards": {
            "satellite_count": 1,
            "qualified_area_pct": 90.0,
            "city_mean_temporal_coverage_pct": 1.25,
        },
        "report_data": {
            "sats": [
                {
                    "sat_id": 39150,
                    "name": "GF-1",
                    "sensor_label": "可见光",
                    "resolution_m": 2.0,
                    "swath_km": 60.0,
                    "passes": 1,
                    "cumulative_minutes": 5.0,
                    "first_window": "2026-11-05T02:00:00Z",
                }
            ],
            "selected_ids": [39150],
            "selected_count": 1,
            "aoi_outline": [[120.0, 30.0], [121.0, 30.0], [121.0, 31.0], [120.0, 30.0]],
            "playback": {
                "events": [
                    {
                        "sat_id": 39150,
                        "label": "11-05 10:00",
                        "points": [[120.1, 30.1]],
                    }
                ],
                "uncovered": [],
                "total": 1,
                "colors": {"39150": "#48c7d9"},
                "colors_light": {"39150": "#0e7490"},
                "start_label": "11-05 08:00",
            },
        },
        "city": {"name": "杭州市", "area_km2": 16819.0},
        "input_summary": {
            "start_utc": "2026-11-05T00:00:00Z",
            "end_utc": "2026-11-05T08:00:00Z",
            "analysis_horizon_h": 8,
        },
        "assumptions": ["逐星幅宽来自卫星档案。"],
        "warnings": [],
        "cloud": {"filtered": False, "reason": "测试"},
        "blocked_satellites": [],
        "city_pass_window_table": [
            {
                "satellite": "GF-1",
                "start_utc": "2026-11-05T02:00:00Z",
                "end_utc": "2026-11-05T02:05:00Z",
                "duration_minutes": 5.0,
                "average_covered_area_pct": 1.0,
                "maximum_covered_area_pct": 1.2,
            }
        ],
        "weak_areas": [
            {
                "grid_id": "330100:0:0:0",
                "area_km2": 25.0,
                "temporal_coverage_pct": 0.0,
                "mrt_minutes": 480.0,
            }
        ],
    }


def test_render_report_contains_four_sections() -> None:
    result = fake_result()
    # task1 的报告是面积口径，不展示薄弱区与时间覆盖率；四节结构用其他任务验证。
    result["operation"] = "task_2_min_satellites_mrt"
    result["targets"] = {"temporal_coverage_pct": 90.0, "area_pct": 90.0}
    result["summary_cards"]["qualified_area_pct"] = 80.0
    html = render_report(result)
    for text in ["卫星构成", "覆盖与回放", "推荐计划", "口径与精度", "薄弱区", "杭州市", "GF-1"]:
        assert text in html
    assert "MAP_DATA" not in html
    assert "目标 90%" in html
    # 推荐计划表的“平均覆盖 / 最大覆盖”必须在“口径与精度”里交代含义。
    assert "平均覆盖：" in html
    assert "最大覆盖：" in html
    assert "不叠加其它卫星" in html


def test_report_carries_purchase_link_when_run_id_given() -> None:
    html = render_report(fake_result(), "run-abc")
    assert "购买本方案" in html
    assert 'href="/purchase/run-abc"' in html
    # 没传 run_id 时不渲染按钮（单测与旧调用兼容）。
    assert "购买本方案" not in render_report(fake_result())


def test_write_report_writes_files_and_latest(tmp_path: Path) -> None:
    info = write_report(fake_result(), "run1", root=tmp_path)
    assert Path(info["report_path"]).is_file()
    assert Path(info["plan_path"]).is_file()
    assert (tmp_path / "latest").is_symlink()
    assert (tmp_path / "latest" / "plan.json").is_file()
    assert "卫星构成" in (tmp_path / "latest" / "report.html").read_text(encoding="utf-8")


def test_task1_report_uses_area_target_and_hides_temporal_strip() -> None:
    result = fake_result()
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": 90.0}
    html = render_report(result)
    assert "达标面积（目标 90%）" in html
    assert "单格时间覆盖率" not in html
    assert "至少成像一次" in html
