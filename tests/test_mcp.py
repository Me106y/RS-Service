from __future__ import annotations

from rs_service.db.session import reset_engine
from rs_service.mcp.server import get_satellite, list_satellites, sync_status
from rs_service.settings import settings


def test_mcp_tools(seeded_db: str) -> None:
    old = settings.database_url
    settings.database_url = seeded_db
    reset_engine()
    try:
        listing = list_satellites()
        assert listing["count"] == 111

        detail = get_satellite(40118)
        assert detail["ok"] is True
        assert detail["satellite"]["payload_public"] is True

        hidden = get_satellite(64048)
        assert hidden["sensor"]["raw_spec"] is None

        status = sync_status()
        assert "accuracy_note" in status
    finally:
        settings.database_url = old
        reset_engine()
