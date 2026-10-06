"""Single source of "now".

Precedence:
1. TODAY_OVERRIDE (tests, scripted demos)
2. an "as-of" date set at startup when the database only contains the committed
   real-data snapshot (no live ingestion yet) — so a snapshot captured on
   2026-10-06 still reads correctly when the repo is cloned months later
3. the real current time
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from radar.settings import get_settings

_as_of: date | None = None


def set_as_of(d: date | None) -> None:
    global _as_of
    _as_of = d


def as_of() -> date | None:
    return _as_of


def _fixed_day() -> date | None:
    o = get_settings().today_override
    if o:
        return date.fromisoformat(o)
    return _as_of


def today() -> date:
    d = _fixed_day()
    return d or datetime.now(timezone.utc).date()


def now() -> datetime:
    real = datetime.now(timezone.utc)
    d = _fixed_day()
    if not d or d == real.date():
        return real
    return datetime.combine(d, time(18, 0), tzinfo=timezone.utc)


def init_from_db() -> date | None:
    """Pin "today" to the snapshot date when no live ingestion has happened since it was loaded."""
    from sqlalchemy import select

    from radar.db import session_scope
    from radar.models import IngestionRun

    try:
        with session_scope() as s:
            snap = s.execute(
                select(IngestionRun).where(IngestionRun.provider == "snapshot").order_by(
                    IngestionRun.started_at.desc()).limit(1)
            ).scalar_one_or_none()
            if snap is None:
                set_as_of(None)
                return None
            snap_day = date.fromisoformat(snap.params.get("as_of"))
            live = s.execute(
                select(IngestionRun.id).where(
                    IngestionRun.provider.not_in(["snapshot", "demo"]),
                    IngestionRun.started_at > snap.finished_at,
                    IngestionRun.status.in_(["ok", "partial"]),
                ).limit(1)
            ).first()
            set_as_of(None if live else snap_day)
            return _as_of
    except Exception:  # noqa: BLE001 — DB not ready: fall back to real time
        set_as_of(None)
        return None
