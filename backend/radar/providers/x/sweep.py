"""Sweep step ``x``: official X API discovery when X_BEARER_TOKEN is set, otherwise a recorded skip.

Without a token the step records ``IngestionRun(provider='x', status='skipped',
errors=['API access not configured'])`` so the status page shows X as DISABLED with a reason —
never an error, never a silent gap. Manual post ingest (``ops.ingest_x_manual``) is unaffected.
"""

from __future__ import annotations

from typing import Any

from radar import clock
from radar.settings import Settings, get_settings

NOT_CONFIGURED = "API access not configured"


def record_skipped(reason: str = NOT_CONFIGURED, *, params: dict[str, Any] | None = None):
    """Persist a finished ``skipped`` run for provider 'x'. Returns (run, None) like ``ops._run``."""
    from radar.db import session_scope
    from radar.models import IngestionRun

    now = clock.wall_now()
    with session_scope() as s:
        run = IngestionRun(provider="x", status="skipped", started_at=now, finished_at=now, errors=[reason],
                           params={"mode": "disabled", "credential_env": "X_BEARER_TOKEN", **(params or {})})
        s.add(run)
        s.flush()
        s.expunge(run)
    return run, None


def ingest_x_sweep(settings: Settings | None = None, *, seeds=None, provider=None):
    """Run one budgeted discovery pass (recorded via ``ops._run``) or record the skip."""
    settings = settings or get_settings()
    if not settings.x_bearer_token:
        return record_skipped()

    from radar import ops
    from radar.providers.x.api import OfficialXProvider
    from radar.providers.x.discovery import discover, load_x_seeds

    seeds = seeds or load_x_seeds()
    provider = provider or OfficialXProvider(settings.x_bearer_token)
    params: dict[str, Any] = {"mode": "official_search", "request_budget": seeds.limits.request_budget,
                              "seed_accounts": len(seeds.seed_accounts)}

    def fn():
        batch, info = discover(provider, seeds)
        params.update(info)  # the runner persists params after fn() returns
        return batch

    return ops._run("x", fn, params, provider.client.stats)
