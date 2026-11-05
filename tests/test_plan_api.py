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
        json={"operation": "task1", "aoi_code": "330100"},
    )
    assert response.status_code == 422


def test_unknown_plan_is_404() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/plans/missing").status_code == 404
