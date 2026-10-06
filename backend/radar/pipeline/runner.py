"""Run a provider and ingest its batch with full observability (IngestionRun rows).

Status semantics:
  ok       fetched and ingested without errors
  partial  errors were reported but some data was ingested
  error    the provider/ingest raised, or errors with no data at all
No failure is silent: every error string is persisted on the run and logged.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from radar import clock
from radar.models import IngestionRun
from radar.pipeline.ingest import IngestStats, ingest_batch
from radar.providers.base import ProviderBatch
from radar.providers.http import HttpStats

log = logging.getLogger("radar.pipeline.runner")

MAX_ERRORS = 50
MAX_ERROR_LEN = 500

SessionFactory = Callable[[], AbstractContextManager[Session]]


def _truncate(errors: list[str]) -> list[str]:
    out = [e if len(e) <= MAX_ERROR_LEN else e[: MAX_ERROR_LEN - 1] + "…" for e in errors[:MAX_ERRORS]]
    if len(errors) > MAX_ERRORS:
        out.append(f"… {len(errors) - MAX_ERRORS} more errors truncated")
    return out


def run_provider(
    session_factory: SessionFactory,
    provider_name: str,
    fn: Callable[[], ProviderBatch],
    *,
    params: dict[str, Any],
    http_stats: HttpStats | None = None,
    is_demo: bool = False,
) -> tuple[IngestionRun, IngestStats | None]:
    """Create an IngestionRun, fetch via `fn`, ingest, and record the outcome.

    `session_factory` is a context-manager factory such as `radar.db.session_scope`
    (commits on exit). Returns the (detached) run and the ingest stats, or None on failure.
    """
    with session_factory() as s:
        run = IngestionRun(provider=provider_name, status="running", params=params, started_at=clock.wall_now())
        s.add(run)
        s.flush()
        run_id = run.id
    t0 = time.monotonic()
    batch: ProviderBatch | None = None
    stats: IngestStats | None = None
    failure: str | None = None
    try:
        batch = fn()
        with session_factory() as s:
            stats = ingest_batch(s, batch, is_demo=is_demo)
    except Exception as e:  # noqa: BLE001 — recorded on the run, never swallowed silently
        failure = f"{type(e).__name__}: {e}"
        stats = None
        log.error("provider %s failed: %s\n%s", provider_name, failure, traceback.format_exc())

    with session_factory() as s:
        run = s.get(IngestionRun, run_id)
        assert run is not None
        errors: list[str] = []
        if batch is not None:
            errors += batch.errors
            counts = batch.counts()
            run.records_fetched = sum(counts.values()) + batch.rejected
        if stats is not None:
            errors += stats.errors
            run.records_rejected = stats.rejected
            run.records_accepted = max(run.records_fetched - stats.rejected, 0)
            run.signals_created = stats.signals_created
        if failure:
            errors.append(failure)
        if http_stats is not None:
            run.request_count = http_stats.requests
            run.rate_limit_remaining = http_stats.rate_limit_remaining
            run.latency_ms_p50 = http_stats.p50
            if http_stats.errors:
                errors.append(f"{http_stats.errors} HTTP request(s) failed after retries")
        run.errors = _truncate(errors)
        has_data = stats is not None and run.records_accepted > 0
        if failure or (errors and not has_data):
            run.status = "error"
        elif errors:
            run.status = "partial"
        else:
            run.status = "ok"
        run.finished_at = clock.wall_now()
        run.params = {**(params or {}), "duration_s": round(time.monotonic() - t0, 3)}
        s.flush()
    if run.status in ("ok", "partial") and not is_demo and clock.is_live_run(provider_name, params):
        # Fresh data has arrived: stop reasoning "as of" the committed snapshot day, so this
        # (possibly long-running) process scores, windows and dates on the real calendar from now on.
        clock.set_as_of(None)
    level = logging.INFO if run.status == "ok" else logging.WARNING
    log.log(level, "provider %s: %s fetched=%d accepted=%d rejected=%d signals=%d errors=%d",
            provider_name, run.status, run.records_fetched, run.records_accepted, run.records_rejected,
            run.signals_created, len(run.errors))
    return run, stats
