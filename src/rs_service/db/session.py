from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..settings import settings
from .base import Base

_engine: Engine | None = None
_engine_url: str | None = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_engine(url: str | None = None) -> Engine:
    global _engine, _engine_url
    target = url or settings.sqlalchemy_url()
    if _engine is None or _engine_url != target:
        if _engine is not None:
            _engine.dispose()
        _engine = create_engine(
            target,
            future=True,
            connect_args={"check_same_thread": False},
        )
        _configure_sqlite(_engine)
        _engine_url = target
    return _engine


def get_session_factory(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    session = get_session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    settings.ensure_dirs()
    Base.metadata.create_all(get_engine(url))


def reset_engine() -> None:
    """测试用：释放当前 engine，下次按新 URL 重建。"""
    global _engine, _engine_url
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_url = None
