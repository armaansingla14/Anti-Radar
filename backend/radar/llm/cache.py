"""Persistent LLM response cache (table ``llm_cache``).

The key is ``sha256(model|purpose|prompt_version|canonical-json(inputs))`` so any
change to the model, prompt version, schema or input text produces a new entry.
The cache is also the mechanism for shipping pre-generated demo outputs: export
the table to JSONL, commit it, import it on a machine with no API key, and every
cached narrative is served without a live provider.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from radar.db import session_scope
from radar.models import LLMCache

log = logging.getLogger(__name__)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace variance, non-JSON types stringified."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def cache_key(model: str, purpose: str, prompt_version: str, inputs: Any) -> str:
    basis = "|".join([model, purpose, prompt_version, canonical_json(inputs)])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def cache_get(key: str) -> dict[str, Any] | None:
    """Cached response payload, or None (also None if the database is unreachable)."""
    try:
        with session_scope() as s:
            row = s.get(LLMCache, key)
            return dict(row.response) if row is not None else None
    except Exception as e:  # noqa: BLE001 — a cache failure must never break the caller
        log.warning("llm cache read failed: %s", e)
        return None


def cache_put(key: str, purpose: str, model: str, response: dict[str, Any]) -> None:
    try:
        with session_scope() as s:
            stmt = pg_insert(LLMCache).values(key=key, purpose=purpose, model=model, response=response)
            s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"response": response, "model": model}))
    except Exception as e:  # noqa: BLE001
        log.warning("llm cache write failed: %s", e)


def export_cache(path: Path | str, purposes: list[str] | None = None) -> int:
    """Write cache rows as JSONL (sorted by key for stable diffs). Returns the row count."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with session_scope() as s:
        q = select(LLMCache).order_by(LLMCache.key)
        if purposes:
            q = q.where(LLMCache.purpose.in_(purposes))
        rows = s.scalars(q).all()
        lines = [
            canonical_json({"key": r.key, "purpose": r.purpose, "model": r.model, "response": r.response})
            for r in rows
        ]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def import_cache(path: Path | str) -> int:
    """Upsert JSONL rows produced by :func:`export_cache`. Malformed lines are skipped."""
    n = 0
    with session_scope() as s:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                key, purpose, model, response = rec["key"], rec["purpose"], rec["model"], rec["response"]
            except (ValueError, KeyError, TypeError):
                log.warning("skipping malformed cache line")
                continue
            if not isinstance(response, dict) or len(key) != 64:
                continue
            stmt = pg_insert(LLMCache).values(key=key, purpose=purpose, model=model, response=response)
            s.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"response": response, "model": model}))
            n += 1
    return n
