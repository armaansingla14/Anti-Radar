"""Tiny in-process memo for expensive derived views (strange signals, topic moves).

Keyed on a cheap *data version* (newest score snapshot + newest signal), so results
are reused across requests until new data is ingested or scored — never stale.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import ScoreSnapshot, Signal, UserFeedback

T = TypeVar("T")
_store: dict[tuple, tuple[tuple, Any]] = {}
_lock = threading.Lock()
MAX_ENTRIES = 64


def data_version(session: Session) -> tuple:
    return (
        clock.today(),
        session.scalar(select(func.max(ScoreSnapshot.created_at))),
        session.scalar(select(func.max(Signal.created_at))),
        session.scalar(select(func.count()).select_from(UserFeedback)),  # dismissals change visible sets
    )


def memoized(session: Session, key: tuple, compute: Callable[[], T]) -> T:
    version = data_version(session)
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] == version:
            return hit[1]
    value = compute()
    with _lock:
        if len(_store) >= MAX_ENTRIES:
            _store.clear()
        _store[key] = (version, value)
    return value


def clear() -> None:
    with _lock:
        _store.clear()
