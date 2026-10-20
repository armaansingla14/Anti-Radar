"""Tiny in-process memo for expensive derived views (strange signals, topic moves, feeds).

Keyed on a cheap *data version* (newest score snapshot + newest signal + feedback count), so
results are reused across requests until new data is ingested or scored — never stale.

``view_version`` extends that with the user-visible state the read views also depend on (entity
edits, watchlist, formation / meeting rebuilds). Views whose output also drifts with wall time
("last 24 h" windows on live data) pass a ``ttl`` so a hit is never older than that many seconds.

The store is a small LRU: inserting past ``MAX_ENTRIES`` evicts the least recently used entry
(per-query keys such as search must not flush the home feed).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    FormationCandidate,
    MeetingRecommendation,
    Person,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
    UserFeedback,
    WatchlistItem,
)

T = TypeVar("T")
# key -> (version, stored_at monotonic, value)
_store: OrderedDict[tuple, tuple[tuple, float, Any]] = OrderedDict()
_lock = threading.Lock()
MAX_ENTRIES = 128


def data_version(session: Session) -> tuple:
    return (
        clock.today(),
        session.scalar(select(func.max(ScoreSnapshot.created_at))),
        session.scalar(select(func.max(Signal.created_at))),
        session.scalar(select(func.count()).select_from(UserFeedback)),  # dismissals change visible sets
    )


def view_version(session: Session) -> tuple:
    """State beyond ``data_version`` that the read views render: entity rows (names, dismissals,
    enrichment), papers, watchlist membership, formation candidates and meeting recommendations.
    One round trip of indexed / small-table maxima."""
    row = session.execute(select(
        select(func.max(Company.updated_at)).scalar_subquery(),
        select(func.max(Person.updated_at)).scalar_subquery(),
        select(func.max(ResearchPaper.updated_at)).scalar_subquery(),
        select(func.count()).select_from(WatchlistItem).scalar_subquery(),
        select(func.max(WatchlistItem.created_at)).scalar_subquery(),
        select(func.max(FormationCandidate.updated_at)).scalar_subquery(),
        select(func.max(MeetingRecommendation.created_at)).scalar_subquery(),
    )).one()
    return tuple(row)


def memoized(session: Session, key: tuple, compute: Callable[[], T], *, ttl: float | None = None) -> T:
    version = data_version(session)
    with _lock:
        hit = _store.get(key)
        if hit and hit[0] == version and (ttl is None or time.monotonic() - hit[1] < ttl):
            _store.move_to_end(key)
            return hit[2]
    value = compute()
    with _lock:
        _store[key] = (version, time.monotonic(), value)
        _store.move_to_end(key)
        while len(_store) > MAX_ENTRIES:
            _store.popitem(last=False)
    return value


def memoized_view(session: Session, key: tuple, compute: Callable[[], T], *, ttl: float | None = None) -> T:
    """``memoized`` for a read view: the key also carries ``view_version``."""
    return memoized(session, (*key, *view_version(session)), compute, ttl=ttl)


def clear() -> None:
    with _lock:
        _store.clear()
