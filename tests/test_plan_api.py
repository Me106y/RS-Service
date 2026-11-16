from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rs_service import plan_api
from rs_service.api.app import create_app


def test_plan_job_lifecycle(monkeypatch) -> None:
    monkeypatch.setattr(
        plan_api,
        "run_plan",
        lambda spec, progress=None: {"status": "completed", "candidate_count": 2, "operation": spec.operation},
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={
            "operation": "revisit",
            "aoi_code": "330100",
            "revisit_hours": 48,
            "start_utc": "2026-11-05T00:00:00Z",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    body: dict = {}
    for _ in range(100):
        body = client.get(f"/api/v1/plans/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)

    assert body["status"] == "completed"
    assert body["result"]["candidate_count"] == 2


def test_plan_rejects_missing_aoi() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={"operation": "revisit", "revisit_hours": 48},
    )
    assert response.status_code == 422


def test_unknown_plan_is_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/plans/missing").status_code == 404


def test_report_endpoint_renders_html(monkeypatch, tmp_path) -> None:
    from tests.test_report import fake_result

    monkeypatch.setattr(plan_api, "run_plan", lambda spec, progress=None: fake_result())
    monkeypatch.setenv("RS_REPORT_DIR", str(tmp_path))
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={
            "operation": "revisit",
            "aoi_code": "330100",
            "revisit_hours": 48,
        },
    )
    job_id = response.json()["job_id"]
    for _ in range(100):
        body = client.get(f"/api/v1/plans/{job_id}").json()
        if body["status"] == "completed":
            break
        time.sleep(0.05)

    report = client.post(f"/api/v1/plans/{job_id}/report")
    assert report.status_code == 200
    assert report.json()["relative_report"].endswith("report.html")
    assert report.json()["task"] == "task1"
    html = client.get(f"/api/v1/plans/{job_id}/report")
    assert html.status_code == 200
    assert "卫星构成" in html.text


def test_plans_history_lists_written_reports(monkeypatch, tmp_path) -> None:
    from rs_service.report import write_report
    from tests.test_report import fake_result

    monkeypatch.setenv("RS_REPORT_DIR", str(tmp_path))
    write_report(fake_result(), "run-history")
    client = TestClient(create_app())
    response = client.get("/api/v1/plans/history")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["total"] == 1
    assert body["by_task"] == {"task1": 1}
    assert body["runs"][0]["run_id"] == "run-history"
    assert body["runs"][0]["relative_report"].endswith("task1/run-history/report.html")
    # 旧的平铺路径已经不存在：GET report 也按 task 子目录找
    assert client.get("/api/v1/plans/run-history/report").status_code == 200


def test_plans_history_filters_by_operation(monkeypatch, tmp_path) -> None:
    from rs_service.report import write_report
    from tests.test_report import fake_result

    monkeypatch.setenv("RS_REPORT_DIR", str(tmp_path))
    write_report(fake_result(), "run-task1")
    task3 = fake_result()
    task3["operation"] = "task_3_max_qualified_area"
    write_report(task3, "run-task3")
    client = TestClient(create_app())
    body = client.get("/api/v1/plans/history", params={"operation": "task3"}).json()
    assert [run["run_id"] for run in body["runs"]] == ["run-task3"]
    assert body["total"] == 1
    # 短 slug 与旧长 slug 等价
    legacy = client.get(
        "/api/v1/plans/history", params={"operation": "task_3_max_qualified_area"}
    ).json()
    assert [run["run_id"] for run in legacy["runs"]] == ["run-task3"]


def test_plan_progress_is_exposed_while_running(monkeypatch) -> None:
    def fake_run(spec, progress=None):
        progress("opportunities", "传播与过境机会", 2, 4)
        time.sleep(0.3)
        return {"status": "completed", "candidate_count": 2, "operation": spec.operation}

    monkeypatch.setattr(plan_api, "run_plan", fake_run)
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={"operation": "revisit", "aoi_code": "330100", "revisit_hours": 48},
    )
    job_id = response.json()["job_id"]

    seen: list[dict] = []
    for _ in range(50):
        body = client.get(f"/api/v1/plans/{job_id}").json()
        seen.append(body)
        stage = (body.get("progress") or {}).get("stage")
        if stage == "opportunities":
            break
        time.sleep(0.02)

    assert any(
        (item.get("progress") or {}).get("stage") == "opportunities"
        and (item.get("progress") or {}).get("pct", 0) > 30
        for item in seen
    )
    for _ in range(100):
        body = client.get(f"/api/v1/plans/{job_id}").json()
        if body["status"] == "completed":
            break
        time.sleep(0.02)
    assert body["progress"]["stage"] == "done"
    assert body["progress"]["pct"] == 100.0


def test_revisit_window_is_three_t_and_capped() -> None:
    from rs_service.plan_api import PlanCreate

    spec = PlanCreate(operation="revisit", aoi_code="330100").to_spec()
    assert spec.operation == "revisit"
    assert spec.horizon_h == 144.0
    assert spec.mrt_target_minutes == 2880.0
    assert spec.area_target_pct == 100.0
    assert spec.fallback_area_target_pct == 100.0
    assert spec.allow_area_fallback is False
    assert spec.cloud_filter is False

    seventy_two = PlanCreate(operation="revisit", aoi_code="330100", revisit_hours=72).to_spec()
    assert seventy_two.horizon_h == 216.0

    full = PlanCreate(operation="revisit", aoi_code="330100", revisit_hours=112).to_spec()
    assert full.horizon_h == 336.0

    capped = PlanCreate(operation="revisit", aoi_code="330100", revisit_hours=168).to_spec()
    assert capped.horizon_h == 336.0

    with pytest.raises(ValidationError):
        PlanCreate(operation="revisit", aoi_code="330100", revisit_hours=47)


def test_plan_options_exposes_only_revisit() -> None:
    client = TestClient(create_app())
    body = client.get("/api/v1/plan-options").json()
    assert [task["operation"] for task in body["tasks"]] == ["revisit"]
    assert body["revisit"]["min_hours"] == 48
    assert body["revisit"]["max_hours"] == 168
    assert body["revisit"]["default_hours"] == 48
    assert body["revisit"]["window_cap_hours"] == 336
    assert body["revisit"]["area_target_pct"] == 100
    assert body["aoi_limits"]["max_area_km2"] == 20000
    assert all(item["level"] != "province" for item in body["aoi"])
