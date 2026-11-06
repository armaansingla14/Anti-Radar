"""Stage 7: persistent "what changed since we last looked" questions, raised from Needs Attention cards.

One row per (portfolio company, normalised question text): an identical question raised by a later event is not
duplicated (its status, possibly ANSWERED or DISMISSED by a reviewer, is kept). Each new question writes one
``question_raised`` system step in the decision log.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import Company, PortfolioQuestion
from radar.portfolio.attention import Card
from radar.portfolio.decisions import log_human, log_system

STATUSES = ("OPEN", "ANSWERED", "DISMISSED")


def question_key(company_id: uuid.UUID | str, text: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return hashlib.sha256(f"{company_id}|{norm}".encode()).hexdigest()


def persist_questions(session: Session, cards: list[Card]) -> dict[str, Any]:
    created, existing = 0, 0
    for card in cards:
        cid = uuid.UUID(card.company_id)
        for q in card.questions:
            key = question_key(cid, q["text"])
            row = session.scalar(select(PortfolioQuestion).where(PortfolioQuestion.dedup_key == key))
            if row is not None:
                existing += 1
                continue
            ev_id = uuid.UUID(q["event_id"]) if q.get("event_id") else None
            row = PortfolioQuestion(id=uuid.uuid4(), company_id=cid, question=q["text"], reason=q.get("reason") or "",
                                    created_from_event_id=ev_id, source=q.get("source"), status="OPEN",
                                    dedup_key=key, created_at=clock.now())
            session.add(row)
            session.flush([row])
            log_system(session, company_id=cid, event_id=ev_id, question_id=row.id, kind="question_raised",
                       note=q["text"], key_parts=(row.id,))
            created += 1
    return {"created": created, "existing": existing}


def set_status(session: Session, question_id: uuid.UUID, status: str, *, reviewer: str, note: str = ""
               ) -> PortfolioQuestion:
    """A reviewer answers or dismisses a question (logged as a human 'reviewed' step)."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    row = session.get(PortfolioQuestion, question_id)
    if row is None:
        raise LookupError("question not found")
    if session.get(Company, row.company_id) is None:
        raise LookupError("company not found")
    row.status = status
    log_human(session, company_id=row.company_id, question_id=row.id, event_id=row.created_from_event_id,
              kind="reviewed", actor=reviewer, note=f"question marked {status}" + (f": {note}" if note else ""))
    session.flush()
    return row
