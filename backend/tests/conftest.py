"""Shared test fixtures.

Integration tests run against a real Postgres (+pgvector) database named
`radar_test` on the same server as dev (DATABASE_URL host), created on demand.
Unit tests never touch the database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

FIXTURES = Path(__file__).parent / "fixtures"

os.environ.setdefault("LLM_PROVIDER", "none")  # tests never call real LLMs unless explicitly opted in
os.environ.setdefault("TODAY_OVERRIDE", "2026-10-06")


def _test_db_url() -> str:
    from radar.settings import get_settings

    base = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url
    url = make_url(base)
    return str(url.set(database="radar_test")) if not os.environ.get("TEST_DATABASE_URL") else base


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_db_url()
    admin = make_url(url).set(database="postgres")
    eng = create_engine(admin.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            exists = c.execute(text("SELECT 1 FROM pg_database WHERE datname='radar_test'")).scalar()
            if not exists:
                c.execute(text("CREATE DATABASE radar_test"))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Postgres not available for integration tests: {e}")
    finally:
        eng.dispose()
    from radar.db import reset_engine
    from radar.models import Base

    real = make_url(url).render_as_string(hide_password=False)
    e2 = create_engine(real)
    with e2.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.drop_all(e2)
    Base.metadata.create_all(e2)
    e2.dispose()
    reset_engine(real)
    return real


@pytest.fixture()
def db(db_url):
    """A clean database session per test (all tables truncated before the test)."""
    from radar.db import engine, session_scope
    from radar.models import Base

    with engine().begin() as c:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    with session_scope() as s:
        yield s


@pytest.fixture()
def thesis():
    from radar.thesis.loader import get_thesis

    return get_thesis()
