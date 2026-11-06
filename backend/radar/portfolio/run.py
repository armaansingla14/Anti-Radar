"""``run_portfolio(session, day)``: the whole Portfolio Pulse pass for one day (deterministic, offline, idempotent).

collect raw changes → normalise → deduplicate → materiality (M1–M11) → competitor impacts (M8) → persist events,
impacts and the system decision log → Needs Attention → questions → proxy metrics → PORTFOLIO ANALYST reports →
``PortfolioRun`` with the real funnel counts.

Re-running the same day updates events in place (dedup key), keeps human review state, replaces impacts, never
duplicates questions or system log lines, and replaces the day's run row and reports. A NEW (unreviewed) event in the
window that the current data and rules no longer produce is removed.
"""

from __future__ import annotations

import logging
import time as _time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompRelationship,
    DiligenceClaim,
    DiligenceRun,
    EventImpact,
    MaterialEvent,
    PortfolioPosition,
    PortfolioRun,
    Repository,
)
from radar.portfolio import attention, candidates, dedup, materiality, normalize
from radar.portfolio.decisions import log_system
from radar.portfolio.impact import CompLink, impacts_for
from radar.portfolio.metrics import company_proxies, write_proxies
from radar.portfolio.templates import impact_areas, why_it_matters
from radar.portfolio.types import Candidate, CompanyContext, SourceRef

log = logging.getLogger("radar.portfolio")
WINDOW_DAYS = candidates.WINDOW_DAYS


def portfolio_companies(session: Session) -> dict[uuid.UUID, Company]:
    ids = list(session.scalars(select(PortfolioPosition.company_id)))
    return {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(ids)))} if ids else {}


def comp_links(session: Session, portfolio: dict[uuid.UUID, Company]) -> dict[uuid.UUID, list[CompLink]]:
    """comp company id -> the portfolio companies that list it."""
    out: dict[uuid.UUID, list[CompLink]] = defaultdict(list)
    rows = session.execute(select(CompRelationship, Company).join(Company, Company.id == CompRelationship.comp_company_id)
                           .where(CompRelationship.company_id.in_(list(portfolio)))).all() if portfolio else []
    for rel, comp in rows:
        pc = portfolio[rel.company_id]
        out[comp.id].append(CompLink(pc.id, pc.name, comp.id, comp.name, rel.comp_type, rel.why_comparable,
                                     rel.confidence))
    return out


def _diligence_claims(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    out: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if not ids:
        return out
    latest: dict[uuid.UUID, uuid.UUID] = {}
    for run in session.scalars(select(DiligenceRun).where(DiligenceRun.company_id.in_(ids),
                                                          DiligenceRun.status == "complete")
                               .order_by(DiligenceRun.started_at)):
        latest[run.company_id] = run.id
    if not latest:
        return out
    for cl in session.scalars(select(DiligenceClaim).where(DiligenceClaim.run_id.in_(list(latest.values())))
                              .order_by(DiligenceClaim.ordinal)):
        out[cl.company_id].append({"id": str(cl.id), "ordinal": cl.ordinal, "text": cl.claim_text,
                                   "claim_type": cl.claim_type, "status": cl.verification_status,
                                   "source_url": cl.source_url})
    return out


def contexts(session: Session, tracked: dict[uuid.UUID, Company], portfolio: set[uuid.UUID]
             ) -> dict[uuid.UUID, CompanyContext]:
    stars: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for r in session.scalars(select(Repository).where(Repository.company_id.in_(list(tracked)))):
        stars[r.company_id][r.full_name.lower()] = r.stars or 0
    claims = _diligence_claims(session, list(portfolio))
    names = sorted(c.name for c in tracked.values())
    return {cid: CompanyContext(company_id=cid, name=c.name, domain=c.domain, github_org=c.github_org,
                                is_portfolio=cid in portfolio, repo_stars=stars.get(cid, {}),
                                diligence_claims=claims.get(cid, []), tracked_names=names)
            for cid, c in tracked.items()}


def _history(session: Session, ids: list[uuid.UUID], before: datetime) -> list[Candidate]:
    """Stored events before the horizon, as novelty context only."""
    out = []
    for ev in session.scalars(select(MaterialEvent).where(MaterialEvent.company_id.in_(ids),
                                                          MaterialEvent.event_at < before)):
        out.append(Candidate(company_id=ev.company_id, event_type=ev.event_type, subtype=ev.event_subtype,
                             title=ev.title, event_at=ev.event_at, sources=[], in_window=False))
    return out


def _source_dict(s: SourceRef) -> dict[str, Any]:
    d = asdict(s)
    d["observed_at"] = s.observed_at.isoformat() if s.observed_at else None
    return d


def _subject_relation(links: list[CompLink]) -> str:
    return "COMPETITOR" if any(link.comp_type == "DIRECT" for link in links) else "ADJACENT"


def run_portfolio(session: Session, day: date | None = None, *, window_days: int = WINDOW_DAYS,
                  reports: bool = True) -> dict[str, Any]:
    t0 = _time.perf_counter()
    day = day or clock.today()
    started = clock.wall_now()
    portfolio = portfolio_companies(session)
    if not portfolio:
        return {"day": day.isoformat(), "skipped": "no portfolio positions (run `radar portfolio sync`)"}
    links = comp_links(session, portfolio)
    tracked_ids = set(portfolio) | set(links)
    tracked = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(list(tracked_ids))))}
    ctxs = contexts(session, tracked, set(portfolio))
    start, end = candidates.window_bounds(day, window_days)

    raw = candidates.collect(session, tracked_ids, day, window_days=window_days)
    cands = normalize.normalize(raw, day)
    merged = dedup.merge(cands)
    history = merged + _history(session, list(tracked_ids), start - timedelta(days=candidates.HORIZON_DAYS))
    # Two merged candidates can still share a stored key (the same URL re-dated more than 7 days apart): one event.
    by_key: dict[str, list[Candidate]] = defaultdict(list)
    for c in merged:
        if c.in_window and start <= c.event_at <= end:
            by_key[dedup.event_key(c)].append(c)
    keyed = [(k, g[0] if len(g) == 1 else dedup.combine(g)) for k, g in by_key.items()]
    in_window = [c for _, c in keyed]

    produced: set[str] = set()
    level_counts: Counter[str] = Counter()
    per_company: dict[str, Counter[str]] = defaultdict(Counter)
    for r in raw:
        if r.in_window:
            per_company[str(r.company_id)]["raw"] += 1
    events: list[MaterialEvent] = []
    for key, c in keyed:
        ctx = ctxs[c.company_id]
        level, rule, reason = materiality.classify(c, ctx, history)
        nov = materiality.novelty(c, history) if c.subtype in materiality.LAUNCH_SUBTYPES else None
        dil = materiality.diligence_link(c, ctx) if c.event_type == "TECHNICAL" else None
        ev = _persist_event(session, key, c, level, rule, reason, nov, dil, tracked[c.company_id],
                            links.get(c.company_id, []), c.company_id in portfolio)
        produced.add(ev.dedup_key)
        events.append(ev)
        level_counts[level] += 1
        per_company[str(c.company_id)][level.lower()] += 1
        _impacts(session, ev, tracked[c.company_id], set(portfolio), links.get(c.company_id, []))
        if level != "NOISE":
            log_system(session, company_id=ev.company_id, event_id=ev.id, kind="detected",
                       note=f"{ev.raw_count} raw change(s) from {', '.join(sorted(ev.meta.get('families') or []))}",
                       key_parts=(ev.id,))
            log_system(session, company_id=ev.company_id, event_id=ev.id, kind="classified",
                       note=f"{ev.event_type}.{ev.event_subtype} → {level} by {rule}: {reason}"[:2000],
                       key_parts=(ev.id, level, rule))
    removed = _remove_stale(session, tracked_ids, start, end, produced)

    cards = attention.rank(attention_items(session, day, window_days=window_days), end, window_days=window_days)
    from radar.portfolio.questions import persist_questions

    q = persist_questions(session, cards)

    metrics_written = 0
    metric_reasons: dict[str, dict[str, str]] = {}
    for cid, company in sorted(tracked.items(), key=lambda kv: kv[1].slug):
        proxies, missing = company_proxies(session, company, day)
        metrics_written += write_proxies(session, cid, day, proxies)
        if missing:
            metric_reasons[company.slug] = missing

    counts = {
        "raw_changes": sum(1 for r in raw if r.in_window),
        "candidates": sum(1 for c in cands if c.in_window and start <= c.event_at <= end),
        "after_dedup": len(in_window),
        "material": level_counts["MATERIAL"], "watch": level_counts["WATCH"], "noise": level_counts["NOISE"],
        "companies_surfaced": len(cards), "portfolio_companies": len(portfolio), "comps": len(set(links)),
        "tracked_companies": len(tracked_ids), "window_days": window_days,
        "window": {"start": start.date().isoformat(), "end": day.isoformat()},
        "questions_created": q["created"], "events_removed": removed, "metrics_written": metrics_written,
        "per_company": {tracked[uuid.UUID(k)].slug: dict(v) for k, v in sorted(per_company.items())
                        if uuid.UUID(k) in tracked},
        "metric_reasons": metric_reasons,
    }
    session.execute(delete(PortfolioRun).where(PortfolioRun.day == day))
    run = PortfolioRun(id=uuid.uuid4(), day=day, started_at=started, finished_at=clock.wall_now(), counts=counts,
                       providers=_providers(session), is_demo=False)
    session.add(run)
    session.flush()
    out: dict[str, Any] = {"day": day.isoformat(), "run_id": str(run.id), **{k: v for k, v in counts.items()
                                                                               if k not in ("per_company",
                                                                                            "metric_reasons")},
                           "needs_attention": [c.company_slug for c in cards]}
    if reports:
        from radar.portfolio.reports import generate_brief, generate_reviews

        out["reviews"] = len(generate_reviews(session, day, window_days=window_days))
        out["brief_id"] = str(generate_brief(session, day, window_days=window_days).id)
    out["ms"] = round((_time.perf_counter() - t0) * 1000, 1)
    return out


def _persist_event(session: Session, key: str, c: Candidate, level: str, rule: str, reason: str, nov: str | None,
                   dil: dict | None, subject: Company, links: list[CompLink], is_portfolio: bool) -> MaterialEvent:
    ev = session.scalar(select(MaterialEvent).where(MaterialEvent.dedup_key == key))
    if ev is None:
        ev = MaterialEvent(id=uuid.uuid4(), dedup_key=key, company_id=c.company_id, detected_at=clock.now(),
                           status="NEW", is_public=True)
        session.add(ev)
    relation = "SELF" if is_portfolio else _subject_relation(links)
    ev.event_type = c.event_type if c.event_type != "NOISE" else normalize.NOISE_EVENT_TYPE.get(c.subtype,
                                                                                               "STRATEGIC")
    ev.event_subtype = c.subtype
    ev.title = c.title
    ev.description = (c.description or "")[:4000] or None
    ev.event_at = c.event_at
    ev.source_ids = list(dict.fromkeys(s.ref for s in c.sources if s.ref))[:50]
    ev.source_urls = list(dict.fromkeys(s.url for s in c.sources if s.url))[:50]
    ev.confidence = round(float(c.confidence), 3)
    ev.epistemic = c.epistemic if c.epistemic in ("FACT", "INFERENCE") else "INFERENCE"
    ev.materiality = level
    ev.materiality_rule = rule
    ev.materiality_reason = reason[:2000]
    ev.novelty = nov
    ev.impact_areas = impact_areas(c.subtype)
    ev.why_it_matters = why_it_matters(c.subtype, level)
    ev.suggested_review = attention.event_action(
        materiality=level, event_type=c.event_type, subtype=c.subtype, relation=relation,
        performance_claim=bool(c.meta.get("performance_claim")), diligence_claim=dil is not None)
    ev.raw_count = c.raw_count
    ev.is_demo = bool(subject.is_demo)
    ev.meta = {
        **{k: v for k, v in c.meta.items() if k != "titles"}, "titles": c.meta.get("titles"),
        "normalized_by": c.rule, "noise": c.event_type == "NOISE", "subject_relation": relation,
        "families": sorted({s.family for s in c.sources}), "publishers": sorted({s.publisher for s in c.sources}),
        "sources": [_source_dict(s) for s in c.sources[:30]],
        "diligence_claim": {k: dil[k] for k in ("id", "ordinal", "text", "shared") if k in dil} if dil else None,
        "last_run_day": clock.today().isoformat(),
    }
    session.flush([ev])
    return ev


def _impacts(session: Session, ev: MaterialEvent, subject: Company, portfolio: set[uuid.UUID],
             links: list[CompLink]) -> None:
    session.execute(delete(EventImpact).where(EventImpact.event_id == ev.id))
    for imp in impacts_for(subject_id=subject.id, subject_name=subject.name, materiality=ev.materiality,
                           confidence=ev.confidence, portfolio_ids=portfolio, comps_of_subject=links):
        session.add(EventImpact(id=uuid.uuid4(), event_id=ev.id, company_id=imp.company_id, relation=imp.relation,
                                impact_reason=imp.reason, confidence=imp.confidence,
                                materiality_for_company=imp.materiality))
    session.flush()


def _remove_stale(session: Session, ids: set[uuid.UUID], start: datetime, end: datetime, produced: set[str]) -> int:
    stale = [ev for ev in session.scalars(select(MaterialEvent).where(
        MaterialEvent.company_id.in_(list(ids)), MaterialEvent.event_at >= start, MaterialEvent.event_at <= end,
        MaterialEvent.status == "NEW")) if ev.dedup_key not in produced]
    for ev in stale:
        session.delete(ev)
    session.flush()
    return len(stale)


def attention_items(session: Session, day: date, *, window_days: int = WINDOW_DAYS,
                    company_ids: list[uuid.UUID] | None = None) -> list[attention.AttentionItem]:
    """Impacts in the window as Needs Attention inputs (one per event × affected portfolio company)."""
    start, end = candidates.window_bounds(day, window_days)
    q = (select(EventImpact, MaterialEvent).join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
         .where(MaterialEvent.event_at >= start, MaterialEvent.event_at <= end,
                EventImpact.materiality_for_company.in_(["MATERIAL", "WATCH"]), MaterialEvent.status != "DISMISSED"))
    if company_ids is not None:
        q = q.where(EventImpact.company_id.in_(company_ids))
    rows = session.execute(q).all()
    cids = {imp.company_id for imp, _ in rows} | {ev.company_id for _, ev in rows}
    names = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(list(cids))))} if cids else {}
    out = []
    for imp, ev in rows:
        pc, subj = names.get(imp.company_id), names.get(ev.company_id)
        if pc is None or subj is None:
            continue
        meta = ev.meta or {}
        out.append(attention.AttentionItem(
            event_id=str(ev.id), company_id=str(pc.id), company_name=pc.name, company_slug=pc.slug,
            subject_name=subj.name, relation=imp.relation, materiality=imp.materiality_for_company,
            event_type=ev.event_type, subtype=ev.event_subtype, title=ev.title, event_at=ev.event_at,
            families=list(meta.get("families") or []), source_urls=list(ev.source_urls or []),
            performance_claim=bool(meta.get("performance_claim")),
            diligence_claim=(meta.get("diligence_claim") or {}).get("text") if meta.get("diligence_claim") else None,
            meta={"role_class": meta.get("role_class")},
        ))
    out.sort(key=lambda i: (i.company_id, i.event_at, i.event_id))
    return out


def _providers(session: Session) -> dict[str, Any]:
    """Provider health at run time (the monitoring panel), incl. disabled sources such as X without API access."""
    from radar.services.status import system_status

    try:
        st = system_status(session)
    except Exception as e:  # noqa: BLE001 — status is informational; never fail the run over it
        log.warning("portfolio: provider status unavailable: %s", e)
        return {}
    return {p.provider: {"label": p.label, "state": p.state, "reason": p.reason,
                    "last_success_at": p.last_success_at.isoformat() if p.last_success_at else None}
            for p in st.providers}
