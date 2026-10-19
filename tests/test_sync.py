from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from rs_service.db.models import SyncRun, SyncState, TleCurrent, TleRecord
from rs_service.db.session import session_scope
from rs_service.sync.celestrak import FetchResult
from rs_service.sync.runner import run_sync

ITEMS = [
    {
        "OBJECT_NAME": "GAOFEN-2",
        "NORAD_CAT_ID": 40118,
        "EPOCH": "2026-10-18T22:54:30.780288",
        "MEAN_MOTION": 14.8104031,
        "ECCENTRICITY": 0.00070703,
        "INCLINATION": 97.9972,
        "RA_OF_ASC_NODE": 355.9574,
        "ARG_OF_PERICENTER": 181.1663,
        "MEAN_ANOMALY": 178.9534,
        "BSTAR": 0.0001,
    },
    {
        "OBJECT_NAME": "GAOFEN-3",
        "NORAD_CAT_ID": 41727,
        "EPOCH": "2026-10-18T23:10:00.000000",
        "MEAN_MOTION": 14.6,
        "ECCENTRICITY": 0.001,
        "INCLINATION": 98.4,
        "RA_OF_ASC_NODE": 10.0,
        "ARG_OF_PERICENTER": 20.0,
        "MEAN_ANOMALY": 30.0,
    },
]


class FakeFetcher:
    def __init__(self, results: list[FetchResult]) -> None:
        self.results = results
        self.calls = 0

    def __call__(self, base_url: str, **kwargs: Any) -> FetchResult:
        self.calls += 1
        return self.results[min(self.calls - 1, len(self.results) - 1)]


def test_sync_success_inserts_and_is_idempotent(seeded_db: str) -> None:
    fetcher = FakeFetcher([FetchResult(ok=True, http_status=200, items=ITEMS)])
    with session_scope(seeded_db) as session:
        outcome = run_sync(
            session,
            fetcher=fetcher,
            retry_delays=[0, 0, 0],
            sleep=lambda _: None,
        )
    assert outcome.status == "ok"
    assert outcome.inserted == 2

    with session_scope(seeded_db) as session:
        assert session.scalar(select(func.count()).select_from(TleRecord)) == 2
        assert session.scalar(select(func.count()).select_from(TleCurrent)) == 2
        state = session.get(SyncState, 1)
        assert state is not None and state.last_success_at

    fetcher2 = FakeFetcher([FetchResult(ok=True, http_status=200, items=ITEMS)])
    with session_scope(seeded_db) as session:
        outcome2 = run_sync(
            session,
            fetcher=fetcher2,
            retry_delays=[0, 0, 0],
            sleep=lambda _: None,
        )
    assert outcome2.status == "ok"
    assert outcome2.inserted == 0
    assert outcome2.unchanged == 2


def test_sync_retries_then_abandons(seeded_db: str) -> None:
    failure = FetchResult(
        ok=False,
        http_status=500,
        error_code="http_500",
        error_message="boom",
    )
    fetcher = FakeFetcher([failure] * 4)
    with session_scope(seeded_db) as session:
        outcome = run_sync(
            session,
            fetcher=fetcher,
            max_retries=3,
            retry_delays=[0, 0, 0],
            sleep=lambda _: None,
        )
    assert outcome.status == "abandoned"
    assert outcome.attempts == 4
    assert fetcher.calls == 4

    with session_scope(seeded_db) as session:
        runs = list(session.scalars(select(SyncRun).order_by(SyncRun.id)))
        assert [run.status for run in runs] == ["retrying", "retrying", "retrying", "abandoned"]
        state = session.get(SyncState, 1)
        assert state is not None
        assert state.circuit_open_until is not None

    # 熔断期内再次调用：直接 skipped，不再请求源
    fetcher2 = FakeFetcher([failure])
    with session_scope(seeded_db) as session:
        outcome2 = run_sync(session, fetcher=fetcher2, retry_delays=[0], sleep=lambda _: None)
    assert outcome2.status == "skipped"
    assert fetcher2.calls == 0


def test_sync_not_modified_is_not_a_failure(seeded_db: str) -> None:
    result = FetchResult(
        ok=False,
        http_status=403,
        error_code="not_modified",
        error_message="GP data has not updated",
        not_modified=True,
    )
    fetcher = FakeFetcher([result])
    with session_scope(seeded_db) as session:
        outcome = run_sync(session, fetcher=fetcher, retry_delays=[0], sleep=lambda _: None)
    assert outcome.status == "skipped_not_modified"
    assert fetcher.calls == 1


def test_dry_run_writes_nothing(seeded_db: str) -> None:
    fetcher = FakeFetcher([FetchResult(ok=True, http_status=200, items=ITEMS)])
    with session_scope(seeded_db) as session:
        outcome = run_sync(session, dry_run=True, fetcher=fetcher)
        assert outcome.status == "dry_run"
        assert outcome.inserted == 2
        assert session.scalar(select(func.count()).select_from(SyncRun)) == 0
        assert session.scalar(select(func.count()).select_from(TleRecord)) == 0
