from __future__ import annotations

from pathlib import Path

import pytest

from rs_service.db.seed import seed_catalog
from rs_service.db.session import init_db, reset_engine, session_scope


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    url = f"sqlite+pysqlite:///{tmp_path / 'test.db'}"
    init_db(url)
    with session_scope(url) as session:
        seed_catalog(session)
    yield url
    reset_engine()


@pytest.fixture()
def seeded_db(db_url: str) -> str:
    return db_url
