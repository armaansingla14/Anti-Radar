from sqlalchemy import func, select

from radar.db import session_scope
from radar.models import Company, ScoreSnapshot, Signal


def test_fictional_seed_is_labeled_and_scored(seeded):
    with session_scope() as s:
        companies = s.scalars(select(Company)).all()
        assert companies and all(c.is_demo for c in companies)
        assert all(c.domain is None or c.domain.endswith(".example") for c in companies)
        assert s.scalar(select(func.count()).select_from(Signal).where(Signal.is_demo.is_(False))) == 0
        halcyon = s.scalar(select(Company).where(Company.name == "Halcyon Dynamics"))
        days = s.scalars(select(ScoreSnapshot.day).where(ScoreSnapshot.entity_id == halcyon.id)).all()
        assert len(days) >= 5  # reconstructed history + today


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]


def test_seed_is_real_only_by_default_and_if_empty_skips(db_url, monkeypatch):
    from radar.demo.seed import reset_database, seed_demo
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "demo_synthetic", False)  # the default (a local .env may say otherwise)
    reset_database()
    try:
        out = seed_demo(snapshot=False, history=False, llm_outputs=False)
        assert out["fictional_included"] is False
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(Company).where(Company.is_demo.is_(True))) == 0
        seed_demo(snapshot=False, fictional=True, history=False, llm_outputs=False)
        assert seed_demo(if_empty=True, fictional=True) == {"skipped": "database not empty"}
    finally:
        reset_database()


def test_demo_snapshot_pins_clock_to_manifest_regardless_of_live_runs(db, monkeypatch):
    from datetime import date

    from radar import clock
    from radar.demo.seed import snapshot_manifest
    from radar.models import IngestionRun

    db.add(IngestionRun(provider="snapshot", status="ok", params={"as_of": "2026-01-01"}))
    db.add(IngestionRun(provider="github", status="ok", params={}))
    db.commit()
    try:
        assert clock.init_from_db() is None  # live data present: no pin
        monkeypatch.setattr(clock, "snapshot_pinned", lambda: True)
        manifest = snapshot_manifest()
        expected = date.fromisoformat(manifest["as_of"]) if manifest else date(2026, 1, 1)
        assert clock.init_from_db() == expected and clock.as_of() == expected
    finally:
        clock.set_as_of(None)
