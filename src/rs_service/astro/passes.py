from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from skyfield.api import EarthSatellite

from .propagate import observer, timescale
from .solar import sun_altitude_deg


@dataclass
class Pass:
    aos_utc: str
    los_utc: str
    tca_utc: str
    max_elev_deg: float
    aos_az_deg: float
    los_az_deg: float
    duration_s: float
    sun_elev_deg: float
    is_daylight: bool
    optical_imaging_ok: bool


def find_passes(
    sat: EarthSatellite,
    latitude_deg: float,
    longitude_deg: float,
    start: datetime,
    end: datetime,
    *,
    min_elevation_deg: float = 5.0,
    step_s: int = 30,
    optical_sun_min_deg: float = 10.0,
) -> list[dict]:
    """扫描时间窗内的过境；5° 可见，10° + 太阳 >10° 为光学成像可用。"""
    ts = timescale()
    observer_site = observer(latitude_deg, longitude_deg)
    difference = sat - observer_site

    start = _aware(start)
    end = _aware(end)
    step = timedelta(seconds=step_s)
    samples: list[tuple[object, float, float]] = []
    current = start
    while current <= end:
        t = ts.from_datetime(current)
        alt, az, _ = difference.at(t).altaz()
        samples.append((t, alt.degrees, az.degrees))
        current += step

    passes: list[dict] = []
    index = 0
    while index < len(samples):
        if samples[index][1] < min_elevation_deg:
            index += 1
            continue
        begin = index
        while index + 1 < len(samples) and samples[index + 1][1] >= min_elevation_deg:
            index += 1
        finish = index
        index += 1

        below = samples[begin - 1][0] if begin > 0 else samples[begin][0]
        above = samples[begin][0]
        aos = _bisect(difference, below, above, min_elevation_deg, want_above=True)
        above_end = samples[finish][0]
        below_end = samples[finish + 1][0] if finish + 1 < len(samples) else samples[finish][0]
        los = _bisect(difference, above_end, below_end, min_elevation_deg, want_above=False)
        if los.tt <= aos.tt:
            continue

        tca = _tca(difference, aos.tt, los.tt)
        max_elev = difference.at(tca).altaz()[0].degrees
        if max_elev < min_elevation_deg:
            continue
        aos_alt, aos_az, _ = difference.at(aos).altaz()
        los_alt, los_az, _ = difference.at(los).altaz()
        tca_dt = tca.utc_datetime()
        sun_alt = sun_altitude_deg(tca_dt, latitude_deg, longitude_deg)
        optical_ok = bool(max_elev >= 10.0 and sun_alt > optical_sun_min_deg)

        passes.append(
            asdict(
                Pass(
                    aos_utc=_iso(aos.utc_datetime()),
                    los_utc=_iso(los.utc_datetime()),
                    tca_utc=_iso(tca_dt),
                    max_elev_deg=round(float(max_elev), 3),
                    aos_az_deg=round(aos_az.degrees, 3),
                    los_az_deg=round(los_az.degrees, 3),
                    duration_s=round((los.tt - aos.tt) * 86400.0, 1),
                    sun_elev_deg=round(sun_alt, 2),
                    is_daylight=bool(sun_alt > 0.0),
                    optical_imaging_ok=optical_ok,
                )
            )
        )
    return passes


def _bisect(difference, t0, t1, threshold: float, *, want_above: bool):
    ts = timescale()
    low, high = t0.tt, t1.tt
    for _ in range(16):
        mid = (low + high) / 2.0
        alt = difference.at(ts.tt_jd(mid)).altaz()[0].degrees
        is_above = alt >= threshold
        if is_above == want_above:
            high = mid
        else:
            low = mid
    return ts.tt_jd(high if want_above else low)


def _tca(difference, aos_tt: float, los_tt: float):
    ts = timescale()
    low, high = aos_tt, los_tt
    for _ in range(28):
        one = low + (high - low) / 3.0
        two = high - (high - low) / 3.0
        alt_one = difference.at(ts.tt_jd(one)).altaz()[0].degrees
        alt_two = difference.at(ts.tt_jd(two)).altaz()[0].degrees
        if alt_one < alt_two:
            low = one
        else:
            high = two
    return ts.tt_jd((low + high) / 2.0)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
