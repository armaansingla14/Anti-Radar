"""Hiring signal engine: role classification and per-company hiring summaries."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.models.core import Job
from radar.thesis.loader import Thesis, get_thesis

NON_TECHNICAL_CLASSES = frozenset({"gtm"})


def classify_role(title: str | None, thesis: Thesis | None = None) -> str | None:
    """Delegates to the single role taxonomy in providers.roles."""
    from radar.providers.roles import classify_role as _classify

    return _classify(title or "", None, thesis or get_thesis()) if title else None


def is_technical_class(role_class: str | None) -> bool:
    return role_class is not None and role_class not in NON_TECHNICAL_CLASSES


def job_class(j: Job, thesis: Thesis | None = None) -> str | None:
    return j.role_class or classify_role(j.title, thesis)


def job_start(j: Job) -> datetime:
    """When the role appeared: the ATS posting date if known, else our first sighting."""
    return j.posted_at or j.first_seen_at


def _end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def is_open(j: Job, day: date) -> bool:
    end = _end(day)
    return j.first_seen_at <= end and (j.closed_at is None or j.closed_at > end)


def hiring_summary(
    session: Session, company_id: uuid.UUID, day: date, thesis: Thesis | None = None
) -> dict[str, Any]:
    """{open_roles, technical_roles, by_class, new_30d, new_prev_30d, closed_30d, acceleration}.

    ``acceleration`` = roles opened in the last 30 days minus roles opened in the 30 days before.
    """
    thesis = thesis or get_thesis()
    jobs = list(session.scalars(select(Job).where(Job.company_id == company_id)))
    end = _end(day)
    lo30, lo60 = end - timedelta(days=30), end - timedelta(days=60)
    by_class: dict[str, int] = {}
    open_roles = technical = 0
    for j in jobs:
        if not is_open(j, day):
            continue
        cls = job_class(j, thesis) or "other"
        open_roles += 1
        by_class[cls] = by_class.get(cls, 0) + 1
        if is_technical_class(job_class(j, thesis)):
            technical += 1
    new_30 = sum(1 for j in jobs if lo30 < job_start(j) <= end)
    new_prev = sum(1 for j in jobs if lo60 < job_start(j) <= lo30)
    closed_30 = sum(1 for j in jobs if j.closed_at is not None and lo30 < j.closed_at <= end)
    return {
        "open_roles": open_roles,
        "technical_roles": technical,
        "by_class": dict(sorted(by_class.items(), key=lambda kv: -kv[1])),
        "new_30d": new_30,
        "new_prev_30d": new_prev,
        "closed_30d": closed_30,
        "acceleration": new_30 - new_prev,
    }
