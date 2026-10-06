"""Retrospective: run the signal framework over well-known public projects (radar/retro.py).

This is explicitly NOT a backtest. It shows which parts of the framework fire on each
project's *observable* public signals and states plainly which history exists
(GitHub star history and weekly commit activity) and which does not (historical
contributor and attention data before RADAR started capturing).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.models import Company, MetricSnapshot, Repository, Signal
from radar.retro import RETRO
from radar.schemas.api import EvidenceItem, RetroEntry, Retrospective, TimeSeries
from radar.services.presenters import evidence_from_signal, latest_snapshots

DISCLAIMER = (
    "Retrospective signal analysis — this validates the signal framework on companies that are already public. "
    "It is not a backtest and does not claim RADAR would have predicted any investment."
)
METHOD = (
    "For each project we show the public signals RADAR has actually ingested (GitHub repository metadata, "
    "releases, commit activity, Hacker News discussion, linked papers) and the current Anti Score components "
    "of the owning organization. Historical: daily star counts reconstructed from GitHub's stargazer timestamps "
    "and the last 52 weeks of commit activity. Not historical: contributor counts, attention (HN points, "
    "followers) and website/hiring data exist only from the day RADAR first captured them, so the framework "
    "cannot be replayed as of an earlier date without hindsight."
)
HISTORY_MIN_DAYS = 60
MAX_SERIES_POINTS = 400
MAX_EVIDENCE = 8


def _series(points: list[tuple[date, float, str]]) -> TimeSeries:
    step = max(1, len(points) // MAX_SERIES_POINTS)
    sampled = points[::step]
    if points and sampled[-1] != points[-1]:
        sampled.append(points[-1])
    sources = {s for _, _, s in points}
    source = "mixed" if len(sources) > 1 else (sources.pop() if sources else "observed")
    if source not in ("observed", "backfill", "demo", "mixed"):
        source = "observed"
    return TimeSeries(metric="stars", label="GitHub stars", source=source,  # type: ignore[arg-type]
                      points=[{"day": d.isoformat(), "value": v} for d, v, _ in sampled])


def _share(comp: dict[str, Any] | None) -> tuple[float, float] | None:
    if not comp or not comp.get("max"):
        return None
    return float(comp.get("value") or 0.0), float(comp["max"])


def framework_reading(components: dict[str, Any] | None, attention: dict[str, Any] | None) -> str:
    """One deterministic sentence on which framework components fire."""
    if not components:
        return "Not scored yet — no Anti Score snapshot exists for the owning organization."
    tm = _share(components.get("technical_momentum"))
    acc = _share(components.get("acceleration"))
    gap = _share(components.get("attention_gap"))
    att = (attention or {}).get("attention")
    parts: list[str] = []
    if tm:
        level = "High" if tm[0] / tm[1] >= 0.6 else "Moderate" if tm[0] / tm[1] >= 0.3 else "Low"
        parts.append(f"{level} technical momentum ({tm[0]:.0f}/{tm[1]:.0f})")
    if acc:
        word = "accelerating" if acc[0] / acc[1] >= 0.5 else "steady" if acc[0] / acc[1] >= 0.2 else "not accelerating"
        parts.append(f"{word} ({acc[0]:.0f}/{acc[1]:.0f})")
    head = ", ".join(parts) if parts else "Technical components unavailable"
    if gap is None:
        return head + "; attention gap not computed."
    if (attention or {}).get("flagged"):
        return (f"{head}; attention gap {gap[0]:.0f}/{gap[1]:.0f} is flagged — technical signal is ahead of public "
                "attention.")
    if att is not None and att >= 60:
        return (f"{head} but attention gap {gap[0]:.0f}/{gap[1]:.0f} — the project is already widely known "
                f"(attention percentile {att:.0f}); RADAR would rank it as 'too obvious' today.")
    return f"{head}; attention gap {gap[0]:.0f}/{gap[1]:.0f}."


def retrospective(session: Session) -> Retrospective:
    names = [r["repo"].lower() for r in RETRO]
    repos = {r.full_name.lower(): r for r in session.scalars(
        select(Repository).where(func.lower(Repository.full_name).in_(names)))}
    repo_ids = [r.id for r in repos.values()]
    company_ids = [r.company_id for r in repos.values() if r.company_id]
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(company_ids)))} \
        if company_ids else {}
    snaps = latest_snapshots(session, company_ids)

    sigs: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    owner = {r.id: r.id for r in repos.values()}
    for r in repos.values():
        if r.company_id:
            owner.setdefault(r.company_id, r.id)
    if owner:
        for s in session.scalars(select(Signal).where(Signal.entity_id.in_(list(owner)))
                                 .order_by(Signal.event_at.desc())):
            sigs[owner[s.entity_id]].append(s)
    stars: dict[uuid.UUID, list[tuple[date, float, str]]] = defaultdict(list)
    commit_weeks: dict[uuid.UUID, int] = {}
    if repo_ids:
        for rid, d, v, src in session.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value, MetricSnapshot.source)
            .where(MetricSnapshot.entity_id.in_(repo_ids), MetricSnapshot.metric == "stars")
            .order_by(MetricSnapshot.day)
        ):
            stars[rid].append((d, float(v), src))
        commit_weeks = dict(session.execute(
            select(MetricSnapshot.entity_id, func.count()).where(MetricSnapshot.entity_id.in_(repo_ids),
                                                                 MetricSnapshot.metric == "commits_week")
            .group_by(MetricSnapshot.entity_id)).tuples().all())

    entries: list[RetroEntry] = []
    for spec in RETRO:
        full = spec["repo"]
        repo = repos.get(full.lower())
        if repo is None:
            entries.append(RetroEntry(
                name=full, slug=None, why_included=spec["why"], observable_signals=[], signal_families=[],
                history_available=False,
                history_note=f"Not yet ingested — run radar ingest github --repo {full}",
                current_score=None, framework_reading="Not yet ingested.", series=[],
            ))
            continue
        company = companies.get(repo.company_id) if repo.company_id else None
        snap = snaps.get(company.id) if company else None
        series = stars.get(repo.id, [])
        span = (series[-1][0] - series[0][0]).days if len(series) > 1 else 0
        history_available = span >= HISTORY_MIN_DAYS
        notes: list[str] = []
        if series:
            kind = (repo.attrs or {}).get("star_history")
            src = "GitHub's stargazer timestamps" + (" (complete)" if kind == "complete" else
                                                     " (recent pages only)" if kind == "recent" else "")
            notes.append(f"Daily star history back to {series[0][0]:%Y-%m} from {src}")
        else:
            notes.append("No star history captured")
        weeks = commit_weeks.get(repo.id, 0)
        notes.append(f"commit activity: last {weeks} weeks" if weeks else "commit activity: not captured")
        notes.append("no historical contributor/attention data before capture")
        evidence: list[EvidenceItem] = [evidence_from_signal(s) for s in sigs.get(repo.id, [])[:MAX_EVIDENCE]]
        families = sorted({s.source_type for s in sigs.get(repo.id, [])})
        entries.append(RetroEntry(
            name=company.name if company else repo.full_name,
            slug=company.slug if company else None, why_included=spec["why"], observable_signals=evidence,
            signal_families=families, history_available=history_available, history_note="; ".join(notes) + ".",
            current_score=round(snap.score) if snap else None,
            framework_reading=framework_reading(snap.components if snap else None, snap.attention if snap else None),
            series=[_series(series)] if series else [],
        ))
    return Retrospective(disclaimer=DISCLAIMER, method=METHOD, entries=entries, takeaways=_takeaways(entries))


def _takeaways(entries: list[RetroEntry]) -> list[str]:
    ingested = [e for e in entries if not e.history_note.startswith("Not yet ingested")]
    out = [f"{len(ingested)} of {len(entries)} retrospective projects are ingested."]
    if not ingested:
        out.append("Run `radar ingest github --repo <owner/name>` for the projects above to populate this page.")
        return out
    with_hist = sum(1 for e in ingested if e.history_available)
    out.append(f"{with_hist} have at least {HISTORY_MIN_DAYS} days of reconstructed star history.")
    obvious = sum(1 for e in ingested if "too obvious" in e.framework_reading)
    high_tm = sum(1 for e in ingested if e.framework_reading.startswith("High technical momentum"))
    if high_tm:
        out.append(f"{high_tm} show high technical momentum today.")
    if obvious:
        out.append(f"{obvious} would be ranked 'too obvious' today: technical momentum is real but public attention "
                   "already caught up — the attention-gap component behaves as intended on known projects.")
    fams = [len(e.signal_families) for e in ingested]
    out.append(f"Average independent signal families per project: {sum(fams) / len(fams):.1f}.")
    scored = [e.current_score for e in ingested if e.current_score is not None]
    if scored:
        out.append(f"Current Anti Scores range {min(scored)}–{max(scored)} (owning organizations).")
    return out
