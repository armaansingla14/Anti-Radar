"""Unknown Yesterday: entities that entered the radar recently, with their current scores."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.models.core import Company, Person, ScoreSnapshot


def _latest_scores(session: Session, ids, day: date) -> dict[uuid.UUID, float]:
    """Latest score on/before ``day`` per entity. ``ids`` is a list of ids or a scalar subquery
    selecting them (one round trip, no giant IN list)."""
    if isinstance(ids, list) and not ids:
        return {}
    rows = session.execute(
        select(ScoreSnapshot.entity_id, ScoreSnapshot.score)
        .where(ScoreSnapshot.entity_id.in_(ids), ScoreSnapshot.day <= day)
        .distinct(ScoreSnapshot.entity_id)
        .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc())
    ).all()
    return {eid: s for eid, s in rows}


def new_entities(session: Session, day: date, days: int = 7) -> dict[str, list[dict[str, Any]]]:
    """Companies and people first seen in (day - days, day], best-scored first.

    Column-only selects (no ORM hydration) and one DISTINCT ON score query keyed by a subquery:
    a fresh live database can have every row "new", so this must stay cheap at thousands of rows."""
    lo = datetime.combine(day - timedelta(days=days), time.max, tzinfo=UTC)
    hi = datetime.combine(day, time.max, tzinfo=UTC)
    c_win = (Company.first_seen_at > lo, Company.first_seen_at <= hi, Company.dismissed.is_(False))
    p_win = (Person.first_seen_at > lo, Person.first_seen_at <= hi, Person.dismissed.is_(False))
    companies = session.execute(
        select(Company.id, Company.slug, Company.name, Company.kind, Company.stage, Company.first_seen_at,
               Company.is_demo).where(*c_win)).all()
    people = session.execute(
        select(Person.id, Person.slug, Person.name, Person.first_seen_at, Person.is_demo).where(*p_win)).all()
    ids = select(Company.id).where(*c_win).union_all(select(Person.id).where(*p_win)).scalar_subquery()
    scores = _latest_scores(session, ids, day) if (companies or people) else {}

    def rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            rows, key=lambda r: (r["score"] is not None, r["score"] or 0, r["first_seen_at"]), reverse=True
        )

    return {
        "companies": rank(
            [
                {
                    "entity_type": "company",
                    "id": str(c.id),
                    "slug": c.slug,
                    "name": c.name,
                    "kind": c.kind,
                    "stage": c.stage,
                    "first_seen_at": c.first_seen_at,
                    "score": scores.get(c.id),
                    "is_demo": c.is_demo,
                }
                for c in companies
            ]
        ),
        "people": rank(
            [
                {
                    "entity_type": "person",
                    "id": str(p.id),
                    "slug": p.slug,
                    "name": p.name,
                    "first_seen_at": p.first_seen_at,
                    "score": scores.get(p.id),
                    "is_demo": p.is_demo,
                }
                for p in people
            ]
        ),
    }
