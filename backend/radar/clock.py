"""Single source of "now".

Precedence:
1. TODAY_OVERRIDE (tests, scripted demos)
2. an "as-of" date set at startup when the database only contains the committed
   real-data snapshot (no live ingestion yet) — so a snapshot captured on
   2026-10-06 still reads correctly when the repo is cloned months later
3. the real current time

`now()`/`today()` are the *data* clock (what day the radar is reasoning about).
Operational timestamps (IngestionRun.started_at/finished_at) must use
`wall_now()` — real time, never pinned — so they can't be confused with the
snapshot day.
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


def wall_now() -> datetime:
    """Real wall-clock time; ignores TODAY_OVERRIDE and the snapshot pin."""
    return datetime.now(timezone.utc)


def now() -> datetime:
    real = datetime.now(timezone.utc)
    d = _fixed_day()
    if not d or d == real.date():
        return real
    return datetime.combine(d, time(18, 0), tzinfo=timezone.utc)


def is_live_run(provider: str, params: dict | None) -> bool:
    """A run that fetched fresh data (not the snapshot replay, not the fictional demo set)."""
    return provider not in ("snapshot", "demo") and not (params or {}).get("snapshot")


def init_from_db() -> date | None:
    """Pin "today" to the snapshot date when no live ingestion has happened in this database.

    Live runs are recognised by provider/params (see `is_live_run`), not by comparing
    timestamps: older databases stamped runs with the pinned time, so ordering is unreliable.
    """
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
            rows = s.execute(
                select(IngestionRun.provider, IngestionRun.params).where(
                    IngestionRun.provider.not_in(["snapshot", "demo"]),
                    IngestionRun.status.in_(["ok", "partial"]),
                )
            )
            live = any(is_live_run(p, params) for p, params in rows)
            set_as_of(None if live else snap_day)
            return _as_of
    except Exception:  # noqa: BLE001 — DB not ready: fall back to real time
        set_as_of(None)
        return None
