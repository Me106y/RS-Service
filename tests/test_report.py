from __future__ import annotations

import json
import re
from pathlib import Path

from rs_service.report import (
    build_meta,
    list_reports,
    render_report,
    task_key,
    write_report,
)


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
                "representative_lat": 29.424269,
                "representative_lon": 118.531438,
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
    for text in [
        "卫星构成",
        "覆盖与回放",
        "推荐计划",
        "口径与精度",
        "薄弱区",
        "杭州市",
        "GF-1",
        "位置",
        "杭州西南",
        "29.424°N, 118.531°E",
    ]:
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
    # 报告靠这段 JS 问一次购买记录，已买过就把按钮换成「已购买」。
    assert 'data-run-id="run-abc"' in html
    assert "/api/purchases" in html
    # 没传 run_id 时不渲染按钮（单测与旧调用兼容）。
    assert "购买本方案" not in render_report(fake_result())


def test_task3_uses_covered_area_and_hides_temporal_metrics() -> None:
    result = fake_result()
    result["operation"] = "task_3_max_qualified_area"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": None}
    result["summary_cards"]["qualified_area_pct"] = 96.9
    result["summary_cards"]["covered_area_pct"] = 96.9
    html = render_report(result, "run-task3")
    assert "覆盖面积" in html
    assert "96.9%" in html
    assert "达标面积" not in html
    assert "时间覆盖率" not in html
    assert "面积加权 MRT" not in html
    # 薄弱区保留网格与面积，但不出现时间覆盖率/MRT 两列。
    assert "薄弱区" in html
    assert "MRT" not in html


def test_task3_zero_area_explains_no_coverage() -> None:
    result = fake_result()
    result["operation"] = "task_3_max_qualified_area"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": None}
    result["summary_cards"]["qualified_area_pct"] = 0.0
    result["summary_cards"]["covered_area_pct"] = 0.0
    html = render_report(result, "run-task3")
    assert "覆盖面积" in html
    assert "时间覆盖率" not in html
    assert "当前窗口内选中卫星没有覆盖目标区域" in html


def test_write_report_writes_files_and_latest(tmp_path: Path) -> None:
    info = write_report(fake_result(), "run1", root=tmp_path)
    assert Path(info["report_path"]).is_file()
    assert Path(info["plan_path"]).is_file()
    assert (tmp_path / "latest").is_symlink()
    assert (tmp_path / "latest" / "plan.json").is_file()
    assert "卫星构成" in (tmp_path / "latest" / "report.html").read_text(encoding="utf-8")


def test_write_report_groups_runs_under_task_dir(tmp_path: Path) -> None:
    info = write_report(fake_result(), "run1", root=tmp_path)
    assert info["task"] == "task1"
    run_dir = tmp_path / "task1" / "run1"
    assert Path(info["report_path"]) == run_dir / "report.html"
    assert Path(info["meta_path"]) == run_dir / "meta.json"
    assert info["relative_report"].endswith("task1/run1/report.html")
    # latest 指向任务子目录，而不是根上的 run_id
    assert (tmp_path / "latest" / "report.html").is_file()
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["task"] == "task1"
    assert meta["task_label"] == "最少星达到覆盖率"
    assert meta["area_name"] == "杭州市"
    assert meta["aoi_kind"] == "city"
    assert meta["sat_count"] == 1
    assert meta["kpi"] == {
        "key": "qualified_area_pct",
        "label": "达标面积",
        "value": 90.0,
        "unit": "%",
    }
    assert meta["created_at"].endswith("+00:00")


def test_task_key_normalizes_legacy_slugs() -> None:
    assert task_key("task3") == "task3"
    assert task_key("task_1_min_satellites_temporal") == "task1"
    assert task_key("task_4_min_area_quantile_mrt") == "task4"
    assert task_key("something_else") == "other"


def test_meta_flags_custom_aoi() -> None:
    result = fake_result()
    result["city"] = {"name": "自定义范围", "adcode": "custom_abc123"}
    meta = build_meta(result, "run-custom")
    assert meta["aoi_kind"] == "custom"
    assert meta["area_name"] == "自定义范围"


def test_task4_meta_kpi_is_area_quantile_mrt() -> None:
    result = fake_result()
    result["operation"] = "task_4_min_area_quantile_mrt"
    result["selection"] = {"result": {"area_target_pct": 90.0, "area_quantile_mrt_s": 255520.0}}
    meta = build_meta(result, "run-task4")
    assert meta["task"] == "task4"
    assert meta["kpi"]["label"] == "90% 面积分位 MRT"
    assert meta["kpi"]["value"] == 71.0
    assert meta["kpi"]["unit"] == "h"


def test_report_history_lists_runs_by_task(tmp_path: Path) -> None:
    write_report(fake_result(), "a1", root=tmp_path)
    task3 = fake_result()
    task3["operation"] = "task_3_max_qualified_area"
    write_report(task3, "b1", root=tmp_path)
    payload = list_reports(tmp_path)
    assert payload["total"] == 2
    assert payload["by_task"] == {"task1": 1, "task3": 1}
    assert {run["run_id"] for run in payload["runs"]} == {"a1", "b1"}
    assert payload["runs"][0]["relative_report"].endswith("report.html")
    assert all(run["has_report"] for run in payload["runs"])
    only = list_reports(tmp_path, operation="task3")
    assert [run["run_id"] for run in only["runs"]] == ["b1"]
    assert only["total"] == 1
    # by_task 统计的是全部任务，方便前端渲染组头
    assert only["by_task"] == {"task1": 1, "task3": 1}


def test_report_history_sorts_newest_first(tmp_path: Path) -> None:
    for run_id, created in (("old", "2026-11-01T10:00:00+00:00"), ("new", "2026-11-09T10:00:00+00:00")):
        write_report(fake_result(), run_id, root=tmp_path)
        meta_path = tmp_path / "task1" / run_id / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["created_at"] = created
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    payload = list_reports(tmp_path)
    assert [run["run_id"] for run in payload["runs"]] == ["new", "old"]


def test_report_history_respects_limit(tmp_path: Path) -> None:
    for index in range(3):
        write_report(fake_result(), f"run{index}", root=tmp_path)
    payload = list_reports(tmp_path, limit=2)
    assert payload["total"] == 3
    assert len(payload["runs"]) == 2


def test_report_playback_clock_shows_time_only() -> None:
    html = render_report(fake_result(), "run1")
    assert 'id="now"' in html
    assert 'id="pct"' not in html
    assert "pctEl" not in html
    assert "cum[" not in html
    assert "TOTAL" not in html


def test_task1_report_uses_area_target_and_hides_temporal_strip() -> None:
    result = fake_result()
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": 90.0}
    html = render_report(result)
    assert "达标面积（目标 90%）" in html
    assert "单格时间覆盖率" not in html
    assert "至少成像一次" in html


def test_report_labels_repeated_satellite_pass_segments() -> None:
    result = fake_result()
    result["operation"] = "task_2_min_satellites_mrt"
    result["targets"] = {"temporal_coverage_pct": 90.0, "area_pct": 90.0}
    first = dict(result["city_pass_window_table"][0])
    second = dict(first)
    second["start_utc"] = "2026-11-05T03:00:00Z"
    second["end_utc"] = "2026-11-05T03:05:00Z"
    result["city_pass_window_table"] = [first, second]
    html = render_report(result, "run-segments")
    assert "第 1/2 段" in html
    assert "第 2/2 段" in html
    assert "不是重复选星" in html


def test_task4_report_uses_mrt_kpi_and_no_time_coverage() -> None:
    result = fake_result()
    result["operation"] = "task_4_min_area_quantile_mrt"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": 90.0, "mrt_minutes": None}
    result["summary_cards"]["satellite_count"] = 3
    result["summary_cards"]["qualified_area_pct"] = 90.537
    result["selection"] = {
        "result": {
            "n_budget": 3,
            "area_target_pct": 90.0,
            "area_quantile_mrt_s": 255520.0,
            "selected_sat_names": ["A", "B", "C"],
        }
    }
    html = render_report(result, "run-task4")
    assert "90% 面积分位 MRT" in html
    assert "71.0 h" in html
    assert "星数预算 / 实际" in html
    assert "3 / 3 颗" in html
    assert "时间覆盖率" not in html
    assert "达标面积" not in html
    assert "两者不是同一个指标" in html


def test_task2_report_labels_mrt_target_and_explains_zero_area() -> None:
    result = fake_result()
    result["operation"] = "task_2_min_satellites_mrt"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": 90.0, "mrt_minutes": None}
    result["summary_cards"]["qualified_area_pct"] = 0.0
    result["selection"] = {"recommended_result": {"mrt_target_s": 1440.0, "achieved_area_pct": 0.0}}
    html = render_report(result, "run-task2")
    # 目标写在达标面积旁边，0.0% 时才不会被当成算法坏了
    assert "达标面积（最长重访 ≤ 24 分 · 面积 90%）" in html
    assert "光学只覆盖白天" in html
    assert "必然为 0.0%" in html


def test_task2_report_uses_hours_for_long_targets() -> None:
    result = fake_result()
    result["operation"] = "task_2_min_satellites_mrt"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": 90.0, "mrt_minutes": None}
    result["summary_cards"]["qualified_area_pct"] = 66.1
    result["selection"] = {"strict_result": {"mrt_target_s": 2880 * 60.0}}
    html = render_report(result, "run-task2-ok")
    assert "达标面积（最长重访 ≤ 48 h · 面积 90%）" in html
    assert "必然为 0.0%" not in html


def test_task5_report_uses_area_weighted_art() -> None:
    result = fake_result()
    result["operation"] = "task_5_min_area_weighted_art"
    result["targets"] = {"temporal_coverage_pct": None, "area_pct": None, "mrt_minutes": None}
    result["summary_cards"]["qualified_area_pct"] = None
    result["summary_cards"]["area_weighted_art_minutes"] = 1234.782
    result["weak_areas"][0]["art_minutes"] = 1234.0
    html = render_report(result, "run-task5")
    # 首屏只保留「选中卫星 + 面积加权 ART」，不再出现达标面积与时间覆盖率
    labels = re.findall(r'<div class="lab">([^<]*)</div>', html)
    assert labels == ["选中卫星", "面积加权 ART"]
    assert "面积加权 ART 20.6 h" in re.sub(r"<[^>]+>", "", html)
    assert "达标面积" not in html
    # 薄弱区保留 时间覆盖率 + ART 两列
    assert '<th class="num">ART</th>' in html
