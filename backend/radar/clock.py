"""Single source of "now" so demos and tests are deterministic (TODAY_OVERRIDE)."""

from __future__ import annotations

from datetime import date, datetime, time, timezone

from radar.settings import get_settings


def today() -> date:
    o = get_settings().today_override
    return date.fromisoformat(o) if o else datetime.now(timezone.utc).date()


def now() -> datetime:
    o = get_settings().today_override
    real = datetime.now(timezone.utc)
    if not o:
        return real
    d = date.fromisoformat(o)
    if d == real.date():
        return real
    return datetime.combine(d, time(12, 0), tzinfo=timezone.utc)
