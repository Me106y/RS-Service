from __future__ import annotations

from datetime import datetime, timezone

from rs_service.astro.solar import sun_altitude_deg


def test_sun_altitude_seasons() -> None:
    lat, lon = 30.27, 120.16
    summer = sun_altitude_deg(datetime(2026, 6, 21, 4, tzinfo=timezone.utc), lat, lon)
    winter = sun_altitude_deg(datetime(2026, 12, 21, 4, tzinfo=timezone.utc), lat, lon)
    night = sun_altitude_deg(datetime(2026, 10, 19, 16, tzinfo=timezone.utc), lat, lon)
    assert 70.0 < summer < 90.0
    assert 20.0 < winter < 50.0
    assert night < 0.0
