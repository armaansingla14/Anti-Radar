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
