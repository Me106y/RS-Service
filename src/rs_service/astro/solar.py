from __future__ import annotations

import math
from datetime import UTC, datetime


def sun_altitude_deg(when: datetime, latitude_deg: float, longitude_deg: float) -> float:
    """NOAA 简化太阳位置；精度约 0.1°，足够判断光学成像的太阳高度门限。"""
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when = when.astimezone(UTC)

    jd = _julian_day(when)
    t = (jd - 2451545.0) / 36525.0

    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m_rad = math.radians(m)
    c = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    apparent_long = true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    epsilon0 = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    epsilon = epsilon0 + 0.00256 * math.cos(math.radians(omega))

    declination = math.asin(
        math.sin(math.radians(epsilon)) * math.sin(math.radians(apparent_long))
    )
    y = math.tan(math.radians(epsilon) / 2.0) ** 2
    eq_time = 4.0 * math.degrees(
        y * math.sin(2 * math.radians(l0))
        - 2 * e * math.sin(m_rad)
        + 4 * e * y * math.sin(m_rad) * math.cos(2 * math.radians(l0))
        - 0.5 * y * y * math.sin(4 * math.radians(l0))
        - 1.25 * e * e * math.sin(2 * m_rad)
    )

    minutes = when.hour * 60 + when.minute + when.second / 60.0
    true_solar_time = (minutes + eq_time + 4.0 * longitude_deg) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0
    if hour_angle < -180.0:
        hour_angle += 360.0

    lat = math.radians(latitude_deg)
    ha = math.radians(hour_angle)
    altitude = math.asin(
        math.sin(lat) * math.sin(declination)
        + math.cos(lat) * math.cos(declination) * math.cos(ha)
    )
    return math.degrees(altitude)


def _julian_day(when: datetime) -> float:
    year, month = when.year, when.month
    day = (
        when.day
        + when.hour / 24.0
        + when.minute / 1440.0
        + (when.second + when.microsecond / 1_000_000.0) / 86400.0
    )
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5
