"""System status / observability: provider health, LLM usage, database coverage.

Provider state rules (deterministic):

* ``error``     the last finished run has status ``error``
* ``disabled``  the provider cannot run in this deployment (see ``radar.providers.registry``),
                e.g. X without ``X_BEARER_TOKEN``
* ``never_run`` no finished run recorded
* ``degraded``  last run ``partial``; last success older than the expected cadence;
                or GitHub running unauthenticated (60 req/h)
* ``healthy``   otherwise

In snapshot-replay mode (``clock.as_of()`` set) freshness is judged relative to the
capture (the newest provider run in the snapshot, else the end of the as-of day)
instead of the wall clock, and counts cover the last run only.

The fictional dataset is not a provider: it is reported under ``database.synthetic`` (labeled SYNTHETIC)
and only when ``DEMO_SYNTHETIC`` is on. The snapshot replay is reported as ``database.snapshot``.
"""

from __future__ import annotations

import statistics
import time as _time
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    IngestionRun,
    Job,
    LLMCache,
    LLMCall,
    MetricSnapshot,
    Person,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
)
from radar.providers import registry
from radar.schemas.api import ProviderStatus, SystemStatus
from radar.settings import get_settings

# (provider key, label, expected cadence in hours) — derived from the registry (single source of truth).
PROVIDERS: list[tuple[str, str, float | None]] = [(d.key, d.label, d.cadence_hours) for d in registry.descriptors()]
PSEUDO = registry.PSEUDO_PROVIDERS
SYNTHETIC_LABEL = "SYNTHETIC"
FINISHED = ("ok", "partial", "error", "skipped")
SUCCESS = ("ok", "partial")
_TOKEN_TTL = 600.0
_token_cache: tuple[float, bool] | None = None


def _github_authenticated() -> bool:
    """GITHUB_TOKEN or a logged-in `gh` CLI. Cached (the CLI probe spawns a process)."""
    global _token_cache
    now = _time.monotonic()
    if _token_cache and now - _token_cache[0] < _TOKEN_TTL:
        return _token_cache[1]
    ok = bool(get_settings().resolved_github_token())
    _token_cache = (now, ok)
    return ok


def _reference_now() -> tuple[datetime, bool]:
    a = clock.as_of()
    if a is not None:
        return datetime.combine(a, time.max, tzinfo=UTC), True
    return datetime.now(UTC), False


def _hours(td: timedelta) -> float:
    return td.total_seconds() / 3600


def _provider_status(key: str, label: str, cadence: float | None, runs: list[IngestionRun],
                     ref: datetime, snapshot: bool) -> ProviderStatus:
    """``runs`` newest first."""
    settings = get_settings()
    finished = [r for r in runs if r.status in FINISHED]
    last = finished[0] if finished else None
    last_ok = next((r for r in finished if r.status in SUCCESS), None)
    if snapshot:
        # Snapshot replay: summarize the whole capture for this provider.
        window = [r for r in finished if (r.params or {}).get("snapshot")] or finished[:1]
    else:
        window = [r for r in finished if r.started_at and r.started_at >= ref - timedelta(hours=24)]
    lat = [r.latency_ms_p50 for r in window if r.latency_ms_p50 is not None]
    reasons: list[str] = []

    if last is None:
        state = "never_run"
        reasons.append("first run in progress" if runs else "no runs recorded yet")
    elif last.status == "error":
        state = "error"
        reasons.append("last run failed")
    else:
        state = "healthy"
        if last.status == "partial":
            state = "degraded"
            reasons.append(f"last run partial ({len(last.errors or [])} errors)")
        if cadence and last_ok is not None:
            seen = last_ok.finished_at or last_ok.started_at
            if seen is not None and _hours(ref - seen) > cadence:
                state = "degraded"
                reasons.append(f"stale: last success {_hours(ref - seen):.0f}h ago (expected every {cadence:.0f}h)")
        elif cadence and last_ok is None:
            state = "degraded"
            reasons.append("no successful run yet")
    av = registry.availability(key, settings, github_authenticated=_github_authenticated() if key == "github" else None)
    if av.state == "disabled":
        state = "disabled"
        reasons.insert(0, av.reason or "disabled")
        desc = registry.get(key)
        if finished and desc.note:
            reasons.append(desc.note)
    elif av.state == "degraded" and state in ("healthy", "degraded", "never_run"):
        if state != "never_run":
            state = "degraded"
        reasons.insert(0, av.reason or "degraded")
    if snapshot:
        reasons.append(f"Snapshot replay — captured {clock.as_of():%Y-%m-%d}")

    return ProviderStatus(
        provider=key, label=label, state=state, reason="; ".join(reasons) or None,
        last_success_at=(last_ok.finished_at or last_ok.started_at) if last_ok else None,
        last_run_at=last.started_at if last else (runs[0].started_at if runs else None),
        records_collected=sum(r.records_fetched or 0 for r in window),
        records_rejected=sum(r.records_rejected or 0 for r in window),
        signals_created=sum(r.signals_created or 0 for r in window),
        errors=[str(e)[:300] for e in (last.errors or [])][:10] if last else [],
        rate_limit_remaining=last.rate_limit_remaining if last else None,
        latency_ms_p50=round(statistics.median(lat), 1) if lat else None,
        runs_24h=len(window),
    )


def _llm(session: Session) -> dict[str, Any]:
    from radar.llm.provider import get_llm

    s = get_settings()
    provider = get_llm()
    name = (s.llm_provider or "none").lower()
    key_present = bool(s.anthropic_api_key) if name == "anthropic" else bool(s.openai_api_key) if name == "openai" \
        else False
    models: dict[str, str | None] = {}
    for tier in ("fast", "standard", "deep"):
        try:
            models[tier] = provider.model_for(tier)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            models[tier] = None
    since = datetime.now(UTC) - timedelta(hours=24)
    total, hits, cost_total = session.execute(
        select(func.count(), func.count().filter(LLMCall.cache_hit.is_(True)), func.coalesce(func.sum(LLMCall.cost_usd), 0.0))
    ).one()
    calls_24h, cost_24h = session.execute(
        select(func.count(), func.coalesce(func.sum(LLMCall.cost_usd), 0.0)).where(LLMCall.created_at >= since)
    ).one()
    return {
        "provider": name, "active_provider": provider.name, "enabled": bool(key_present and provider.available),
        "models": models, "calls_24h": int(calls_24h), "cost_24h": round(float(cost_24h), 4),
        "cost_total": round(float(cost_total), 4),
        "cache_hit_rate": round(hits / total, 3) if total else None,
        "cached_responses": int(session.scalar(select(func.count()).select_from(LLMCache)) or 0),
        "max_usd_per_investigation": s.llm_max_usd_per_investigation,
    }


def _db_size(session: Session) -> int | None:
    try:
        return int(session.scalar(select(func.pg_database_size(func.current_database()))) or 0)
    except Exception:  # noqa: BLE001 — permissions / non-Postgres
        session.rollback()
        return None


def _pretty_bytes(n: int | None) -> str | None:
    if n is None:
        return None
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _database(session: Session, runs: list[IngestionRun], snapshot: bool) -> dict[str, Any]:
    tables = {"companies": Company, "people": Person, "repositories": Repository, "papers": ResearchPaper,
              "signals": Signal, "metric_snapshots": MetricSnapshot, "score_snapshots": ScoreSnapshot, "jobs": Job}
    row = session.execute(select(*[select(func.count()).select_from(m).scalar_subquery().label(k)
                                   for k, m in tables.items()])).one()
    counts = dict(zip(tables, (int(v) for v in row), strict=True))
    # Days on which RADAR itself observed metrics: backfilled history from GitHub and the fictional
    # demo series are both excluded.
    snap_days = session.scalar(select(func.count(distinct(MetricSnapshot.day)))
                               .where(MetricSnapshot.source.not_in(("backfill", "demo")),
                                      MetricSnapshot.is_demo.is_(False))) or 0
    history_from = session.scalar(select(func.min(MetricSnapshot.day)))
    first_sig, last_sig = session.execute(select(func.min(Signal.event_at), func.max(Signal.event_at))).one()
    size = _db_size(session)

    snap_run = next((r for r in runs if r.provider == "snapshot"), None)
    snap_info = None
    if snap_run is not None:
        snap_info = {"as_of": (snap_run.params or {}).get("as_of"),
                     "loaded_at": snap_run.started_at.isoformat() if snap_run.started_at else None}
    live = [r.finished_at or r.started_at for r in runs
            if r.provider not in PSEUDO and r.status in SUCCESS and clock.is_live_run(r.provider, r.params)
            and (r.finished_at or r.started_at)]
    if snapshot and clock.as_of() is not None:
        data_as_of = clock.as_of().isoformat()
    elif live:
        data_as_of = max(live).isoformat()
    else:
        data_as_of = (snap_info or {}).get("as_of")

    out: dict[str, Any] = {
        **counts, "snapshot_days": int(snap_days), "observed_days": int(snap_days),
        "history_from": history_from.isoformat() if history_from else None,
        "first_signal_at": first_sig.isoformat() if first_sig else None,
        "last_signal_at": last_sig.isoformat() if last_sig else None,
        "size_bytes": size, "size_pretty": _pretty_bytes(size),
        "data_as_of": data_as_of, "snapshot": snap_info,
    }
    if get_settings().demo_synthetic:
        demo_companies = session.scalar(select(func.count()).select_from(Company).where(Company.is_demo.is_(True))) or 0
        demo_people = session.scalar(select(func.count()).select_from(Person).where(Person.is_demo.is_(True))) or 0
        out["demo_companies"] = int(demo_companies)
        out["synthetic"] = {"label": SYNTHETIC_LABEL, "enabled": True, "companies": int(demo_companies),
                            "people": int(demo_people),
                            "note": "Fictional example entities (.example domains); never real companies."}
    else:
        out["demo_companies"] = 0
        out["synthetic"] = None
    return out


def system_status(session: Session) -> SystemStatus:
    from radar.scoring.anti_score import SCORE_VERSION

    ref, snapshot = _reference_now()
    run_to_key = {rp: d.key for d in registry.descriptors() for rp in d.run_providers}
    keys = list(run_to_key) + list(PSEUDO)
    # newest 50 runs per provider (one windowed query)
    rn = func.row_number().over(partition_by=IngestionRun.provider, order_by=IngestionRun.started_at.desc()).label("rn")
    sub = select(IngestionRun.id, rn).where(IngestionRun.provider.in_(keys)).subquery()
    runs = list(session.scalars(select(IngestionRun).join(sub, sub.c.id == IngestionRun.id).where(sub.c.rn <= 50)
                                .order_by(IngestionRun.started_at.desc())))
    by_provider: dict[str, list[IngestionRun]] = {d.key: [] for d in registry.descriptors()}
    for r in runs:
        if r.provider in run_to_key:
            by_provider[run_to_key[r.provider]].append(r)
    if snapshot:
        # freshness relative to the capture itself: the last provider run recorded in the snapshot
        captured = [r.finished_at or r.started_at for r in runs
                    if r.provider not in PSEUDO and (r.finished_at or r.started_at)]
        if captured:
            ref = max(captured)
    providers = [_provider_status(k, label, cadence, by_provider[k], ref, snapshot) for k, label, cadence in PROVIDERS]
    llm = _llm(session)
    llm_av = registry.llm_availability()
    llm["state"] = llm_av.state
    llm["reason"] = llm_av.reason
    llm["daily_usd_cap"] = get_settings().llm_daily_usd_cap
    return SystemStatus(
        providers=providers, llm=llm, database=_database(session, runs, snapshot),
        demo_mode=get_settings().demo_synthetic,
        score_version=SCORE_VERSION, last_scored_at=session.scalar(select(func.max(ScoreSnapshot.created_at))),
    )
