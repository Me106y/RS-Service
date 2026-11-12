from __future__ import annotations

import time

from fastapi.testclient import TestClient

from rs_service import plan_api
from rs_service.api.app import create_app


def test_plan_job_lifecycle(monkeypatch) -> None:
    monkeypatch.setattr(
        plan_api,
        "run_plan",
        lambda spec: {"status": "completed", "candidate_count": 2, "operation": spec.operation},
    )
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={
            "operation": "task1",
            "aoi_code": "330100",
            "temporal_coverage_target_pct": 90,
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


def test_plan_rejects_missing_task_field() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={"operation": "task2", "aoi_code": "330100"},
    )
    assert response.status_code == 422


def test_unknown_plan_is_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/plans/missing").status_code == 404


def test_report_endpoint_renders_html(monkeypatch, tmp_path) -> None:
    from tests.test_report import fake_result

    monkeypatch.setattr(plan_api, "run_plan", lambda spec: fake_result())
    monkeypatch.setenv("RS_REPORT_DIR", str(tmp_path))
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/plans",
        json={
            "operation": "task1",
            "aoi_code": "330100",
            "temporal_coverage_target_pct": 90,
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
