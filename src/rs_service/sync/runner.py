from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import (
    DataSource,
    Satellite,
    SyncItemError,
    SyncRun,
    SyncState,
    TleCurrent,
    TleRecord,
)
from ..db.seed import ensure_source
from ..settings import settings
from .celestrak import FetchResult, fetch_for_sync
from .parser import parse_omm

SHANGHAI = ZoneInfo("Asia/Shanghai")
ALGO_NOTE = "TLE/SGP4; along-track ~1 km/day; not for sub-meter planning"


@dataclass
class SyncOutcome:
    status: str
    attempts: int = 0
    requested: int = 0
    inserted: int = 0
    unchanged: int = 0
    failed: int = 0
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    message: str = ""


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_0300_utc(now: datetime | None = None) -> str:
    current = (now or now_utc()).astimezone(SHANGHAI)
    target = (current + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
    return iso(target)


def _reset_daily(state: SyncState, now: datetime) -> None:
    if not state.last_attempt_at:
        return
    try:
        previous = datetime.fromisoformat(state.last_attempt_at.replace("Z", "+00:00"))
    except ValueError:
        return
    if previous.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
        state.attempts_today = 0
        state.retry_count_today = 0


def _seed_ids(session: Session) -> set[int]:
    return set(session.scalars(select(Satellite.sat_id)))


def _ingest(
    session: Session,
    items: list[dict[str, Any]],
    source: DataSource,
    seed_ids: set[int],
    run: SyncRun,
) -> tuple[int, int, int]:
    inserted = unchanged = failed = 0
    run.requested = len(items)
    for item in items:
        try:
            sat_id = int(item.get("NORAD_CAT_ID"))
        except (TypeError, ValueError):
            failed += 1
            continue
        if seed_ids and sat_id not in seed_ids:
            continue
        try:
            parsed = parse_omm(item)
        except (TypeError, ValueError, KeyError) as exc:
            failed += 1
            session.add(
                SyncItemError(
                    run_id=run.id,
                    sat_id=sat_id,
                    attempt_no=run.attempt_no,
                    error_code="parse_error",
                    error_message=str(exc)[:500],
                )
            )
            continue

        exists = session.scalar(
            select(TleRecord.id).where(
                TleRecord.sat_id == parsed["sat_id"],
                TleRecord.epoch_utc == parsed["epoch_utc"],
                TleRecord.source_id == source.id,
            )
        )
        if exists:
            unchanged += 1
            continue

        record = TleRecord(
            sat_id=parsed["sat_id"],
            epoch_utc=parsed["epoch_utc"],
            line1=parsed["line1"],
            line2=parsed["line2"],
            omm_json=parsed["omm_json"],
            checksum_ok=parsed["checksum_ok"],
            mean_motion=parsed["mean_motion"],
            inclination_deg=parsed["inclination_deg"],
            eccentricity=parsed["eccentricity"],
            raan_deg=parsed["raan_deg"],
            argp_deg=parsed["argp_deg"],
            mean_anomaly_deg=parsed["mean_anomaly_deg"],
            bstar=parsed["bstar"],
            mean_motion_dot=parsed["mean_motion_dot"],
            mean_motion_ddot=parsed["mean_motion_ddot"],
            ephemeris_type=parsed["ephemeris_type"],
            element_set_no=parsed["element_set_no"],
            rev_at_epoch=parsed["rev_at_epoch"],
            source_id=source.id,
            fetched_at_utc=iso(now_utc()),
        )
        session.add(record)
        session.flush()

        current = session.get(TleCurrent, parsed["sat_id"])
        if current is None:
            current = TleCurrent(sat_id=parsed["sat_id"])
            session.add(current)
        current.tle_id = record.id
        current.epoch_utc = parsed["epoch_utc"]
        current.age_hours = _age_hours(parsed["epoch_utc"])
        current.updated_at = iso(now_utc())
        inserted += 1

    run.inserted = inserted
    run.unchanged = unchanged
    run.failed = failed
    return inserted, unchanged, failed


def _age_hours(epoch_utc: str) -> float:
    try:
        epoch = datetime.fromisoformat(epoch_utc.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (now_utc() - epoch).total_seconds() / 3600.0)


def run_sync(
    session: Session,
    *,
    dry_run: bool = False,
    force: bool = False,
    source: DataSource | None = None,
    fetcher: Callable[..., FetchResult] = fetch_for_sync,
    max_retries: int | None = None,
    retry_delays: list[int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    client: Any | None = None,
) -> SyncOutcome:
    source = source or ensure_source(session)
    seed_ids = _seed_ids(session)
    if not seed_ids:
        return SyncOutcome(status="empty", message="卫星目录为空，先执行 rs-service init")

    retries = settings.sync_max_retries if max_retries is None else max_retries
    delays = retry_delays or settings.retry_delays()
    max_attempts = retries + 1
    state = session.get(SyncState, source.id)
    if state is None:
        state = SyncState(source_id=source.id)
        session.add(state)
        session.flush()

    now = now_utc()
    _reset_daily(state, now)

    if dry_run:
        result = fetcher(
            source.base_url,
            sat_ids=seed_ids,
            timeout=settings.celestrak_timeout_seconds,
            client=client,
            proxy=settings.http_proxy,
        )
        if not result.ok:
            return SyncOutcome(
                status="failed",
                attempts=1,
                http_status=result.http_status,
                error_code=result.error_code,
                error_message=result.error_message,
            )
        count = sum(1 for item in result.items if int(item.get("NORAD_CAT_ID", 0)) in seed_ids)
        return SyncOutcome(status="dry_run", requested=len(result.items), inserted=count)

    if state.circuit_open_until and not force:
        try:
            open_until = datetime.fromisoformat(state.circuit_open_until.replace("Z", "+00:00"))
        except ValueError:
            open_until = None
        if open_until and now < open_until:
            return SyncOutcome(
                status="skipped",
                message=f"circuit open until {state.circuit_open_until}",
            )

    last_error: FetchResult | None = None
    for attempt in range(1, max_attempts + 1):
        started = now_utc()
        run = SyncRun(
            source_id=source.id,
            started_at_utc=iso(started),
            status="running",
            attempt_no=attempt,
            max_attempts=max_attempts,
        )
        session.add(run)
        session.flush()

        state.last_attempt_at = iso(started)
        state.attempts_today += 1
        state.last_status = "running"
        state.updated_at = iso(started)

        result = fetcher(
            source.base_url,
            sat_ids=seed_ids,
            timeout=settings.celestrak_timeout_seconds,
            client=client,
            proxy=settings.http_proxy,
        )
        run.http_status = result.http_status
        run.bytes = result.bytes
        source.last_http_status = result.http_status
        finished = now_utc()
        run.finished_at_utc = iso(finished)
        run.duration_ms = int((finished - started).total_seconds() * 1000)

        if result.ok and result.not_modified:
            run.status = "skipped_not_modified"
            state.last_status = "skipped_not_modified"
            state.updated_at = iso(finished)
            session.commit()
            return SyncOutcome(status="skipped_not_modified", attempts=attempt, http_status=result.http_status)

        if result.ok:
            _ingest(session, result.items, source, seed_ids, run)
            run.status = "ok"
            state.last_success_at = iso(finished)
            state.last_status = "ok"
            state.consecutive_failures = 0
            state.circuit_open_until = None
            state.updated_at = iso(finished)
            source.last_success_at = iso(finished)
            session.commit()
            return SyncOutcome(
                status="ok",
                attempts=attempt,
                requested=run.requested or 0,
                inserted=run.inserted or 0,
                unchanged=run.unchanged or 0,
                failed=run.failed or 0,
                http_status=result.http_status,
            )

        last_error = result
        run.error_code = result.error_code
        run.error_message = (result.error_message or "")[:1000]
        state.updated_at = iso(finished)

        if result.not_modified:
            run.status = "skipped_not_modified"
            state.last_status = "skipped_not_modified"
            session.commit()
            return SyncOutcome(status="skipped_not_modified", attempts=attempt, http_status=result.http_status)

        if attempt < max_attempts:
            delay = delays[min(attempt - 1, len(delays) - 1)]
            run.status = "retrying"
            run.next_retry_at = iso(finished + timedelta(seconds=delay))
            state.last_status = "retrying"
            state.retry_count_today += 1
            session.commit()
            if delay > 0:
                sleep(delay)
            continue

        run.status = "abandoned"
        state.last_status = "abandoned"
        state.consecutive_failures += 1
        state.circuit_open_until = next_0300_utc(finished)
        state.next_scheduled_at = state.circuit_open_until
        session.commit()
        return SyncOutcome(
            status="abandoned",
            attempts=attempt,
            http_status=result.http_status,
            error_code=result.error_code,
            error_message=result.error_message,
            message=f"3 次重试后仍失败，将于 {state.circuit_open_until} 恢复",
        )

    return SyncOutcome(
        status="abandoned",
        attempts=max_attempts,
        error_code=(last_error.error_code if last_error else None),
        error_message=(last_error.error_message if last_error else None),
    )
