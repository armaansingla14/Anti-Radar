"""Alembic is the schema source of truth in deployments: `upgrade head` on an empty database must
produce exactly ``Base.metadata`` (tests elsewhere use ``create_all`` and would not notice drift)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture()
def empty_db_url():
    from tests.conftest import _test_db_url

    base = make_url(_test_db_url())
    name = f"{base.database}_alembic"
    admin = create_engine(base.set(database="postgres").render_as_string(hide_password=False),
                          isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            c.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as e:  # noqa: BLE001
        admin.dispose()
        pytest.skip(f"Postgres not available: {e}")
    url = base.set(database=name).render_as_string(hide_password=False)
    yield url
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _alembic_config(url: str):
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def test_upgrade_head_matches_models(empty_db_url):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command
    from radar.models import Base

    cfg = _alembic_config(empty_db_url)
    command.upgrade(cfg, "head")

    eng = create_engine(empty_db_url)
    try:
        with eng.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": False})
            assert ctx.get_current_revision() == ScriptDirectory.from_config(cfg).get_current_head()
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        eng.dispose()
    assert diff == [], f"models and migrations drifted: {diff}"


def test_downgrade_base_then_upgrade(empty_db_url):
    from alembic import command

    cfg = _alembic_config(empty_db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
