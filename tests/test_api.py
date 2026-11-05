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
        assert health["satellites"] == 102

        listing = client.get("/api/v1/satellites", params={"limit": 5}).json()
        assert listing["total"] == 102
        assert len(listing["items"]) == 5

        detail = client.get("/api/v1/satellites/40118").json()
        assert detail["satellite"]["name"] == "GF-2"
        assert detail["satellite"]["payload_public"] is True
        assert detail["sensor"]["raw_spec"]

        aois = client.get("/api/v1/aoi").json()
        codes = {item["code"] for item in aois["items"]}
        assert "330100" in codes and "330000" in codes
        hz = next(i for i in aois["items"] if i["code"] == "330100")
        assert 16_000 < hz["area_km2"] < 17_500
        assert hz["level"] == "city"

        boundary = client.get("/api/v1/aoi/hangzhou").json()
        assert len(boundary["features"][0]["geometry"]["coordinates"][0]) == 6406

        hidden = client.get("/api/v1/satellites/64048").json()
        assert hidden["satellite"]["payload_public"] is False
        assert "raw_spec" not in hidden["sensor"]
    finally:
        settings.database_url = old
        reset_engine()
