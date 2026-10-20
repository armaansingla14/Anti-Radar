"""Storage retention for the free-tier database (Neon 0.5 GB).

    radar retention          # or a `radar sweep --only retention` step

Policy (values are never lost, only bulky evidence and redundant history):

1. ``score_snapshots`` older than 14 days: evidence lists inside ``components[*].evidence`` and
   ``collision.evidence`` are replaced by ``evidence_ids`` (the signal ids only). Scores,
   component values, raw inputs and explanations are kept.
2. ``score_snapshots`` older than 90 days: one snapshot per entity per ISO week is kept
   (the latest day of that week); the rest are deleted.
3. ``signals.metadata`` older than 90 days: long string values are truncated and long lists
   shortened (``_trimmed: true`` is set); signal rows themselves are kept.
4. ``sources.snippet`` fetched more than 90 days ago is truncated.
5. ``VACUUM (ANALYZE)`` on the touched tables, outside any transaction.

Snapshot/score ages use the data clock (``clock.today()``/``clock.now()``); source fetch times
are wall-clock timestamps and use ``clock.wall_now()``.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from radar import clock
from radar.db import engine, session_scope

log = logging.getLogger("radar.retention")

EVIDENCE_DAYS = 14
HISTORY_DAYS = 90
BULK_DAYS = 90
MAX_STR = 280
MAX_LIST = 20
META_MIN_BYTES = 1024
VACUUM_TABLES = ("score_snapshots", "signals", "sources")


def _strip_evidence(d: Any) -> tuple[Any, bool]:
    """Replace an ``evidence`` list of dicts by ``evidence_ids``; returns (new, changed)."""
    if not isinstance(d, dict):
        return d, False
    ev = d.get("evidence")
    if not isinstance(ev, list) or not ev:
        return d, False
    ids = [str(e.get("signal_id")) for e in ev if isinstance(e, dict) and e.get("signal_id")]
    ids += [str(e) for e in ev if isinstance(e, str)]
    out = {**d, "evidence": [], "evidence_ids": list(dict.fromkeys([*(d.get("evidence_ids") or []), *ids]))}
    return out, True


def prune_snapshot_evidence(days: int = EVIDENCE_DAYS, batch: int = 500) -> int:
    """Step 1. Returns the number of snapshots rewritten."""
    cutoff = clock.today() - timedelta(days=days)
    n = 0
    last_id = None
    while True:
        with session_scope() as s:
            rows = s.execute(
                text(
                    """SELECT id, components, collision FROM score_snapshots
                       WHERE day < :cut
                         AND (jsonb_path_exists(components, '$.*.evidence[0]')
                              OR jsonb_path_exists(collision, '$.evidence[0]'))
                         AND (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid))
                       ORDER BY id LIMIT :lim"""
                ),
                {"cut": cutoff, "last": last_id, "lim": batch},
            ).all()
            if not rows:
                return n
            for sid, components, collision in rows:
                comps = dict(components or {})
                for k, v in list(comps.items()):
                    comps[k], _ = _strip_evidence(v)
                coll, _ = _strip_evidence(collision or {})
                s.execute(
                    text("UPDATE score_snapshots SET components = CAST(:c AS jsonb), collision = CAST(:k AS jsonb) "
                         "WHERE id = :id"),
                    {"c": _json(comps), "k": _json(coll), "id": sid},
                )
                n += 1
            last_id = str(rows[-1][0])


def prune_old_snapshots(days: int = HISTORY_DAYS) -> int:
    """Step 2. Keep one snapshot per entity per ISO week before the cutoff. Returns rows deleted."""
    cutoff = clock.today() - timedelta(days=days)
    with session_scope() as s:
        res = s.execute(
            text(
                """DELETE FROM score_snapshots s USING (
                       SELECT id, row_number() OVER (
                           PARTITION BY entity_id, date_trunc('week', day) ORDER BY day DESC, id) AS rn
                       FROM score_snapshots WHERE day < :cut
                   ) x
                   WHERE s.id = x.id AND x.rn > 1"""
            ),
            {"cut": cutoff},
        )
        return int(res.rowcount or 0)


def _trim(v: Any, depth: int = 0) -> Any:
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else v[: MAX_STR - 1] + "…"
    if isinstance(v, list):
        return [_trim(x, depth + 1) for x in v[:MAX_LIST]]
    if isinstance(v, dict):
        return {k: _trim(x, depth + 1) for k, x in v.items()}
    return v


def trim_signal_metadata(days: int = BULK_DAYS, batch: int = 500) -> int:
    """Step 3. Returns the number of signals whose metadata was trimmed."""
    cutoff = clock.now() - timedelta(days=days)
    n = 0
    last_id = None
    while True:
        with session_scope() as s:
            rows = s.execute(
                text(
                    """SELECT id, metadata FROM signals
                       WHERE detected_at < :cut AND octet_length(metadata::text) > :min
                         AND NOT coalesce((metadata->>'_trimmed')::boolean, false)
                         AND (CAST(:last AS uuid) IS NULL OR id > CAST(:last AS uuid))
                       ORDER BY id LIMIT :lim"""
                ),
                {"cut": cutoff, "min": META_MIN_BYTES, "last": last_id, "lim": batch},
            ).all()
            if not rows:
                return n
            for sid, meta in rows:
                new = {**_trim(dict(meta or {})), "_trimmed": True}
                s.execute(text("UPDATE signals SET metadata = CAST(:m AS jsonb) WHERE id = :id"),
                          {"m": _json(new), "id": sid})
                n += 1
            last_id = str(rows[-1][0])


def trim_source_snippets(days: int = BULK_DAYS) -> int:
    """Step 4. Returns the number of sources whose snippet was truncated."""
    cutoff = clock.wall_now() - timedelta(days=days)
    with session_scope() as s:
        res = s.execute(
            text("UPDATE sources SET snippet = left(snippet, :n) || '…' "
                 "WHERE fetched_at < :cut AND length(snippet) > :n + 1"),
            {"cut": cutoff, "n": MAX_STR},
        )
        return int(res.rowcount or 0)


def db_size_bytes() -> int | None:
    try:
        with engine().connect() as c:
            return int(c.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
    except Exception:  # noqa: BLE001
        return None


def vacuum(tables: tuple[str, ...] = VACUUM_TABLES) -> list[str]:
    """Step 5. VACUUM cannot run inside a transaction block: use an AUTOCOMMIT connection."""
    done = []
    with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        for t in tables:
            c.execute(text(f'VACUUM (ANALYZE) "{t}"'))
            done.append(t)
    return done


def run(*, do_vacuum: bool = True) -> dict[str, Any]:
    """Apply the whole retention policy; returns a summary."""
    before = db_size_bytes()
    out: dict[str, Any] = {
        "snapshots_deleted": prune_old_snapshots(),  # first: no point pruning rows about to be deleted
        "evidence_pruned": prune_snapshot_evidence(),
        "signal_metadata_trimmed": trim_signal_metadata(),
        "source_snippets_trimmed": trim_source_snippets(),
    }
    out["vacuumed"] = vacuum() if do_vacuum else []
    out["db_bytes_before"] = before
    out["db_bytes_after"] = db_size_bytes()
    log.info("retention: %s", out)
    return out


def _json(v: Any) -> str:
    import json

    return json.dumps(v, default=str)


__all__ = ["run", "prune_snapshot_evidence", "prune_old_snapshots", "trim_signal_metadata",
           "trim_source_snippets", "vacuum", "db_size_bytes"]
