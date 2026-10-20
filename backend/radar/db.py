"""Database engine and session helpers (sync SQLAlchemy 2 + psycopg3).

Pooling is sized from settings (``DB_POOL_SIZE``/``DB_MAX_OVERFLOW``; keep these small on Neon free).
Connections are recycled every 5 minutes and psycopg server-side prepared statements are disabled
(``prepare_threshold=None``) so the engine is safe behind a transaction pooler (Neon ``-pooler`` / PgBouncer).
One-shot CLI jobs (GitHub Actions) set ``RADAR_NULLPOOL=1`` to hold no idle connections at all.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from radar.settings import get_settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None

POOL_RECYCLE_SECONDS = 300


def use_nullpool() -> bool:
    return os.environ.get("RADAR_NULLPOOL", "").strip().lower() in ("1", "true", "yes", "on")


def engine_kwargs(url: str, *, nullpool: bool | None = None) -> dict[str, Any]:
    """Keyword arguments for ``create_engine`` (exposed for tests and Alembic)."""
    s = get_settings()
    kw: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(url).get_backend_name() == "postgresql" and make_url(url).get_driver_name() == "psycopg":
        kw["connect_args"] = {"prepare_threshold": None}
    if use_nullpool() if nullpool is None else nullpool:
        kw["poolclass"] = pool.NullPool
    else:
        kw.update(pool_size=max(1, s.db_pool_size), max_overflow=max(0, s.db_max_overflow),
                  pool_recycle=POOL_RECYCLE_SECONDS)
    return kw


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        _engine = create_engine(url, **engine_kwargs(url))
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine(url: str | None = None) -> None:
    """Point the app at a different database (tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    if url:
        get_settings().database_url = url
    engine()


@contextmanager
def session_scope() -> Iterator[Session]:
    engine()
    assert _SessionLocal is not None
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
