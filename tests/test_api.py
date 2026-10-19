from __future__ import annotations

from fastapi.testclient import TestClient

from rs_service.api.app import create_app
from rs_service.db.session import reset_engine
from rs_service.settings import settings


def test_api_health_and_satellites(seeded_db: str) -> None:
    old = settings.database_url
    settings.database_url = seeded_db
    reset_engine()
    try:
        client = TestClient(create_app())

        health = client.get("/api/v1/health").json()
        assert health["ok"] is True
        assert health["satellites"] == 111

        listing = client.get("/api/v1/satellites", params={"limit": 5}).json()
        assert listing["total"] == 111
        assert len(listing["items"]) == 5

        detail = client.get("/api/v1/satellites/40118").json()
        assert detail["satellite"]["name"] == "GF-2"
        assert detail["satellite"]["payload_public"] is True
        assert detail["sensor"]["raw_spec"]

        hidden = client.get("/api/v1/satellites/64048").json()
        assert hidden["satellite"]["payload_public"] is False
        assert "raw_spec" not in hidden["sensor"]
    finally:
        settings.database_url = old
        reset_engine()
