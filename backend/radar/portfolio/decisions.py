"""The portfolio decision log (EVENT → REVIEW → QUESTION → ACTION).

System steps (detected, classified, question_raised, comps_updated) carry a dedup key so an idempotent re-run never
writes the same line twice. Human steps (reviewed, diligence_requested, monitoring_changed, question status) are
written only when a person acts, with their name as the actor, and are never deduplicated.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import PortfolioDecision

KINDS = ("detected", "classified", "question_raised", "reviewed", "diligence_requested", "comps_updated",
         "monitoring_changed")
SYSTEM_KINDS = frozenset({"detected", "classified", "question_raised", "comps_updated"})


def decision_key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def log_system(session: Session, *, company_id: uuid.UUID, kind: str, note: str, key_parts: tuple[Any, ...],
               event_id: uuid.UUID | None = None, question_id: uuid.UUID | None = None) -> PortfolioDecision | None:
    """Write one system step unless the same step (``key_parts``) is already logged. Returns the new row or None."""
    if kind not in SYSTEM_KINDS:
        raise ValueError(f"{kind!r} is not a system step")
    key = decision_key(kind, *key_parts)
    if session.scalar(select(PortfolioDecision.id).where(PortfolioDecision.dedup_key == key)) is not None:
        return None
    row = PortfolioDecision(id=uuid.uuid4(), company_id=company_id, event_id=event_id, question_id=question_id,
                            kind=kind, actor="system", note=note[:2000], dedup_key=key, created_at=clock.now())
    session.add(row)
    session.flush([row])
    return row


def log_human(session: Session, *, company_id: uuid.UUID, kind: str, actor: str, note: str = "",
              event_id: uuid.UUID | None = None, question_id: uuid.UUID | None = None) -> PortfolioDecision:
    """A step a person took (never deduplicated). ``actor`` is the reviewer's name, never 'system'."""
    if kind not in KINDS or kind in ("detected", "classified", "question_raised"):
        raise ValueError(f"{kind!r} is not a human step")
    actor = (actor or "").strip()[:120] or "analyst"
    if actor == "system":
        raise ValueError("a human step needs a human actor")
    row = PortfolioDecision(id=uuid.uuid4(), company_id=company_id, event_id=event_id, question_id=question_id,
                            kind=kind, actor=actor, note=(note or "")[:2000], dedup_key=None,
                            created_at=clock.wall_now())
    session.add(row)
    session.flush([row])
    return row
