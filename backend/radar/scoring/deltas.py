"""Score deltas from ScoreSnapshot history."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.models.core import ScoreSnapshot


def score_at(session: Session, entity_id: uuid.UUID, day: date) -> float | None:
    """Score from the nearest snapshot on or before ``day``."""
    return session.scalar(
        select(ScoreSnapshot.score)
        .where(ScoreSnapshot.entity_id == entity_id, ScoreSnapshot.day <= day)
        .order_by(ScoreSnapshot.day.desc())
        .limit(1)
    )


def score_delta(session: Session, entity_id: uuid.UUID, day: date, days: int) -> float | None:
    """Score(day) - Score(day - days), each from the nearest snapshot on or before; None if either is missing."""
    now = score_at(session, entity_id, day)
    then = score_at(session, entity_id, day - timedelta(days=days))
    if now is None or then is None:
        return None
    return round(now - then, 2)


def score_series(
    session: Session, entity_id: uuid.UUID, since: date, until: date
) -> list[tuple[date, float]]:
    return [
        (d, s)
        for d, s in session.execute(
            select(ScoreSnapshot.day, ScoreSnapshot.score)
            .where(
                ScoreSnapshot.entity_id == entity_id, ScoreSnapshot.day >= since, ScoreSnapshot.day <= until
            )
            .order_by(ScoreSnapshot.day)
        ).all()
    ]
