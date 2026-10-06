"""Unknown Yesterday: entities that entered the radar recently, with their current scores."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.models.core import Company, Person, ScoreSnapshot


def _latest_scores(session: Session, ids: list[uuid.UUID], day: date) -> dict[uuid.UUID, float]:
    if not ids:
        return {}
    latest = (
        select(ScoreSnapshot.entity_id, func.max(ScoreSnapshot.day).label("d"))
        .where(ScoreSnapshot.entity_id.in_(ids), ScoreSnapshot.day <= day)
        .group_by(ScoreSnapshot.entity_id)
        .subquery()
    )
    rows = session.execute(
        select(ScoreSnapshot.entity_id, ScoreSnapshot.score).join(
            latest, (latest.c.entity_id == ScoreSnapshot.entity_id) & (latest.c.d == ScoreSnapshot.day)
        )
    ).all()
    return {eid: s for eid, s in rows}


def new_entities(session: Session, day: date, days: int = 7) -> dict[str, list[dict[str, Any]]]:
    """Companies and people first seen in (day - days, day], best-scored first."""
    lo = datetime.combine(day - timedelta(days=days), time.max, tzinfo=UTC)
    hi = datetime.combine(day, time.max, tzinfo=UTC)
    companies = list(
        session.scalars(
            select(Company).where(
                Company.first_seen_at > lo, Company.first_seen_at <= hi, Company.dismissed.is_(False)
            )
        )
    )
    people = list(
        session.scalars(
            select(Person).where(
                Person.first_seen_at > lo, Person.first_seen_at <= hi, Person.dismissed.is_(False)
            )
        )
    )
    scores = _latest_scores(session, [c.id for c in companies] + [p.id for p in people], day)

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
