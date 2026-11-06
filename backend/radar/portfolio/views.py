"""Read model for the Portfolio API (deterministic ordering; JSON-ready dicts validated by schemas/portfolio.py).

Positions are serialised through an explicit allow-list (``position_out``): ``private_fields`` is never read here.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompRelationship,
    EventImpact,
    MaterialEvent,
    PortfolioDecision,
    PortfolioPosition,
    PortfolioQuestion,
    PortfolioRun,
    ResearchReport,
    ReviewDecision,
    ScoreSnapshot,
)
from radar.portfolio import attention, candidates
from radar.portfolio.metrics import compare_windows, stored_proxies
from radar.portfolio.run import attention_items

STALE_DAYS = 7
REVIEW_INTERVAL_DAYS = 30
EVENT_TYPES_COUNTED = ("TECHNICAL", "COMPETITIVE", "FINANCING", "PRODUCT", "TALENT", "COMMERCIAL", "RISK")
POSITION_FIELDS = ("portfolio_name", "status", "publicly_disclosed", "source_url", "source_checked_at",
                   "investment_stage", "investment_year", "sector", "thesis_tags")


def _iso(d: Any) -> str | None:
    return d.isoformat() if d else None


def company_brief(c: Company) -> dict[str, Any]:
    return {"id": str(c.id), "slug": c.slug, "name": c.name, "website": c.website, "domain": c.domain,
            "github_org": c.github_org, "is_demo": bool(c.is_demo)}


def position_out(p: PortfolioPosition) -> dict[str, Any]:
    """Allow-listed public fields only (never ``private_fields`` or ``meta``)."""
    out = {f: getattr(p, f) for f in POSITION_FIELDS}
    out["source_checked_at"] = _iso(p.source_checked_at)
    out["thesis_tags"] = list(p.thesis_tags or [])
    return out


def resolve_portfolio_company(session: Session, ref: str) -> tuple[Company, PortfolioPosition] | None:
    try:
        c = session.get(Company, uuid.UUID(ref))
    except ValueError:
        c = session.scalar(select(Company).where(Company.slug == ref))
    if c is None:
        return None
    p = session.scalar(select(PortfolioPosition).where(PortfolioPosition.company_id == c.id)
                       .order_by(PortfolioPosition.created_at).limit(1))
    return (c, p) if p is not None else None


def data_day(session: Session) -> date:
    """The latest monitoring day (PortfolioRun), else the data clock's today."""
    return session.scalar(select(func.max(PortfolioRun.day))) or clock.today()


def event_out(ev: MaterialEvent, subject: Company, *, relation: str | None = None) -> dict[str, Any]:
    meta = ev.meta or {}
    return {
        "id": str(ev.id), "company": company_brief(subject), "event_type": ev.event_type,
        "event_subtype": ev.event_subtype, "title": ev.title, "description": ev.description,
        "event_at": ev.event_at.isoformat(), "detected_at": _iso(ev.detected_at),
        "source_urls": list(ev.source_urls or []),
        "sources": [{k: s.get(k) for k in ("url", "title", "source_type", "family", "publisher", "observed_at")}
                    for s in (meta.get("sources") or []) if isinstance(s, dict)],
        "confidence": ev.confidence, "epistemic": ev.epistemic, "materiality": ev.materiality,
        "materiality_rule": ev.materiality_rule, "materiality_reason": ev.materiality_reason, "novelty": ev.novelty,
        "impact_areas": list(ev.impact_areas or []), "why_it_matters": ev.why_it_matters,
        "suggested_review": ev.suggested_review, "status": ev.status, "reviewed_at": _iso(ev.reviewed_at),
        "reviewed_by": ev.reviewed_by, "raw_count": ev.raw_count, "families": list(meta.get("families") or []),
        "relation": relation, "performance_claim": bool(meta.get("performance_claim")), "is_demo": bool(ev.is_demo),
    }


def _subjects(session: Session, events: list[MaterialEvent]) -> dict[uuid.UUID, Company]:
    ids = {e.company_id for e in events}
    return {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(list(ids))))} if ids else {}


def card_out(card: attention.Card, companies: dict[str, Company]) -> dict[str, Any]:
    d = card.as_dict()
    c = companies.get(card.company_id)
    d["company"] = company_brief(c) if c else {"id": card.company_id, "slug": card.company_slug,
                                               "name": card.company_name}
    for k in ("company_id", "company_name", "company_slug"):
        d.pop(k, None)
    return d


def needs_attention(session: Session, day: date, *, company_ids: list[uuid.UUID] | None = None,
                    top: int = attention.TOP_N, min_priority: str = "MEDIUM") -> list[dict[str, Any]]:
    items = attention_items(session, day, company_ids=company_ids)
    _, end = candidates.window_bounds(day)
    cards = attention.rank(items, end, top=top, min_priority=min_priority)
    comps = {str(c.id): c for c in session.scalars(select(Company).where(
        Company.id.in_([uuid.UUID(x.company_id) for x in cards])))} if cards else {}
    return [card_out(c, comps) for c in cards]


def funnel(session: Session, day: date | None = None) -> dict[str, Any]:
    q = select(PortfolioRun).order_by(PortfolioRun.day.desc(), PortfolioRun.started_at.desc()).limit(1)
    if day is not None:
        q = select(PortfolioRun).where(PortfolioRun.day <= day).order_by(PortfolioRun.day.desc()).limit(1)
    run = session.scalar(q)
    if run is None:
        return {"available": False, "reason": "no monitoring run yet (radar portfolio run)"}
    c = run.counts or {}
    return {**{k: int(c.get(k) or 0) for k in ("raw_changes", "candidates", "after_dedup", "material", "watch",
                                                "noise", "companies_surfaced")},
            "day": run.day.isoformat(), "window": c.get("window"), "available": True, "reason": None}


def _portfolio(session: Session) -> list[tuple[Company, PortfolioPosition]]:
    rows = session.execute(select(Company, PortfolioPosition).join(PortfolioPosition,
                                                                    PortfolioPosition.company_id == Company.id)).all()
    return sorted(((c, p) for c, p in rows), key=lambda cp: cp[0].name.lower())


def monitoring(session: Session) -> dict[str, Any]:
    from radar.services.freshness import data_age, feed_as_of

    portfolio = _portfolio(session)
    pids = [c.id for c, _ in portfolio]
    comp_ids = set(session.scalars(select(CompRelationship.comp_company_id).where(
        CompRelationship.company_id.in_(pids)))) if pids else set()
    tracked = list(dict.fromkeys(pids + sorted(comp_ids - set(pids), key=str)))
    ages = data_age(session, "company", tracked) if tracked else {}
    fresh = stale = never = 0
    for cid in tracked:
        a = ages.get(cid)
        if a is None or a.age_days is None:
            never += 1
        elif a.age_days <= STALE_DAYS:
            fresh += 1
        else:
            stale += 1
    run = session.scalar(select(PortfolioRun).order_by(PortfolioRun.day.desc(), PortfolioRun.started_at.desc())
                         .limit(1))
    providers = []
    if run is not None and run.providers:
        for key, p in sorted(run.providers.items()):
            providers.append({"key": key, "label": p.get("label") or key, "state": p.get("state") or "unknown",
                              "reason": p.get("reason"), "last_success_at": p.get("last_success_at")})
    else:
        from radar.services.status import system_status

        for p in system_status(session).providers:
            providers.append({"key": p.provider, "label": p.label, "state": p.state, "reason": p.reason,
                              "last_success_at": _iso(p.last_success_at)})
    updated = session.scalar(select(func.max(MaterialEvent.detected_at)))
    return {"tracked": len(tracked), "portfolio_companies": len(pids), "comps": len(comp_ids - set(pids)),
            "fresh": fresh, "stale": stale, "never_observed": never, "stale_after_days": STALE_DAYS,
            "providers": providers, "events_updated_at": _iso(updated),
            "data_as_of": feed_as_of(session).data_as_of.isoformat()}


def movers(session: Session, day: date, limit: int = 8) -> list[dict[str, Any]]:
    out = []
    for c, _ in _portfolio(session):
        windows = compare_windows(stored_proxies(session, c.id, day))
        for w in windows:
            if w["comparable"] and w["delta"]:
                out.append({"company": company_brief(c), "metric": w})
    out.sort(key=lambda m: (-abs(m["metric"]["delta"] or 0), m["company"]["slug"], m["metric"]["metric"]))
    return out[:limit]


def _last_reviews(session: Session, ids: list[uuid.UUID]) -> dict[str, ReviewDecision]:
    out: dict[str, ReviewDecision] = {}
    if not ids:
        return out
    for r in session.scalars(select(ReviewDecision).where(ReviewDecision.target_type == "portfolio_company",
                                                          ReviewDecision.target_id.in_([str(i) for i in ids]))
                             .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id)):
        out.setdefault(r.target_id, r)
    return out


def since_last_review(session: Session, day: date) -> list[dict[str, Any]]:
    """Per portfolio company: material / watch impacts after its last review (or in the window when never reviewed)."""
    portfolio = _portfolio(session)
    reviews = _last_reviews(session, [c.id for c, _ in portfolio])
    start, end = candidates.window_bounds(day)
    out = []
    for c, _ in portfolio:
        r = reviews.get(str(c.id))
        since = r.created_at if r is not None else start
        rows = session.execute(select(EventImpact.materiality_for_company, func.count())
                               .join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
                               .where(EventImpact.company_id == c.id, MaterialEvent.detected_at > since,
                                      MaterialEvent.event_at <= end,
                                      EventImpact.materiality_for_company.in_(["MATERIAL", "WATCH"]))
                               .group_by(EventImpact.materiality_for_company)).all()
        counts = dict(rows)
        if counts:
            out.append({"company": company_brief(c), "since": _iso(since), "reviewed": r is not None,
                        "material": int(counts.get("MATERIAL", 0)), "watch": int(counts.get("WATCH", 0))})
    out.sort(key=lambda x: (-x["material"], -x["watch"], x["company"]["slug"]))
    return out


def pulse(session: Session) -> dict[str, Any]:
    day = data_day(session)
    start, end = candidates.window_bounds(day)
    portfolio = _portfolio(session)
    pids = [c.id for c, _ in portfolio]
    impacts = session.execute(
        select(EventImpact, MaterialEvent).join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
        .where(EventImpact.company_id.in_(pids), MaterialEvent.event_at >= start, MaterialEvent.event_at <= end,
               EventImpact.materiality_for_company.in_(["MATERIAL", "WATCH"]))).all() if pids else []
    cards = needs_attention(session, day)
    material_ids = {ev.id for imp, ev in impacts if imp.materiality_for_company == "MATERIAL"}
    watch_ids = {ev.id for imp, ev in impacts if imp.materiality_for_company == "WATCH"} - material_ids
    by_type: Counter[str] = Counter()
    seen: set[uuid.UUID] = set()
    for imp, ev in impacts:
        if ev.id in seen:
            continue
        seen.add(ev.id)
        by_type["COMPETITIVE" if imp.relation != "SELF" else ev.event_type] += 1
    noise = session.scalar(select(func.count()).select_from(MaterialEvent).where(
        MaterialEvent.materiality == "NOISE", MaterialEvent.event_at >= start, MaterialEvent.event_at <= end)) or 0
    counts = {"need_attention": len(cards), "material": len(material_ids), "watch": len(watch_ids),
              "noise": int(noise), **{t.lower(): by_type.get(t, 0) for t in EVENT_TYPES_COUNTED}}
    mats = sorted({ev.id: (imp, ev) for imp, ev in impacts if imp.materiality_for_company == "MATERIAL"}.values(),
                  key=lambda x: (-x[1].event_at.timestamp(), str(x[1].id)))[:12]
    subj = _subjects(session, [ev for _, ev in mats])
    brief_id = session.scalar(select(ResearchReport.id).where(ResearchReport.kind == "portfolio_brief")
                              .order_by(ResearchReport.created_at.desc()).limit(1))
    return {"day": day.isoformat(), "counts": counts, "funnel": funnel(session), "needs_attention": cards,
            "material_events": [event_out(ev, subj[ev.company_id], relation=imp.relation) for imp, ev in mats],
            "movers": movers(session, day), "since_last_review": since_last_review(session, day),
            "monitoring": monitoring(session), "brief_id": str(brief_id) if brief_id else None}


def index(session: Session) -> list[dict[str, Any]]:
    day = data_day(session)
    start, end = candidates.window_bounds(day)
    cards = {c["company"]["id"]: c["priority"] for c in needs_attention(session, day, top=100, min_priority="LOW")}
    out = []
    for c, p in _portfolio(session):
        counts = dict(session.execute(select(EventImpact.materiality_for_company, func.count())
                                      .join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
                                      .where(EventImpact.company_id == c.id, MaterialEvent.event_at >= start,
                                             MaterialEvent.event_at <= end)
                                      .group_by(EventImpact.materiality_for_company)).all())
        noise = session.scalar(select(func.count()).select_from(MaterialEvent).where(
            MaterialEvent.company_id == c.id, MaterialEvent.materiality == "NOISE", MaterialEvent.event_at >= start,
            MaterialEvent.event_at <= end)) or 0
        oq = session.scalar(select(func.count()).select_from(PortfolioQuestion).where(
            PortfolioQuestion.company_id == c.id, PortfolioQuestion.status == "OPEN")) or 0
        out.append({"company": company_brief(c), "position": position_out(p),
                    "material": int(counts.get("MATERIAL", 0)), "watch": int(counts.get("WATCH", 0)),
                    "noise": int(noise), "open_questions": int(oq), "priority": cards.get(str(c.id))})
    return out


def questions_out(session: Session, rows: list[PortfolioQuestion], with_company: bool = False) -> list[dict]:
    comps = {c.id: c for c in session.scalars(select(Company).where(
        Company.id.in_([q.company_id for q in rows])))} if rows and with_company else {}
    return [{"id": str(q.id), "company": company_brief(comps[q.company_id]) if q.company_id in comps else None,
             "question": q.question, "reason": q.reason, "source": q.source, "status": q.status,
             "created_from_event_id": str(q.created_from_event_id) if q.created_from_event_id else None,
             "created_at": _iso(q.created_at)} for q in rows]


def decisions_out(rows: list[PortfolioDecision]) -> list[dict[str, Any]]:
    return [{"id": str(d.id), "kind": d.kind, "actor": d.actor, "note": d.note,
             "event_id": str(d.event_id) if d.event_id else None,
             "question_id": str(d.question_id) if d.question_id else None, "created_at": _iso(d.created_at)}
            for d in rows]


def _scores(session: Session, ids: list[uuid.UUID], day: date) -> dict[uuid.UUID, tuple[float | None, float | None]]:
    out: dict[uuid.UUID, tuple[float | None, float | None]] = {}
    if not ids:
        return out
    for s in session.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id.in_(ids), ScoreSnapshot.day <= day)
                             .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc())
                             .distinct(ScoreSnapshot.entity_id)):
        tm = ((s.components or {}).get("technical_momentum") or {}).get("value")
        out[s.entity_id] = (float(tm) if tm is not None else None, float(s.score))
    return out


def comps_of(session: Session, company: Company, day: date) -> list[dict[str, Any]]:
    rels = list(session.scalars(select(CompRelationship).where(CompRelationship.company_id == company.id)))
    comps = {c.id: c for c in session.scalars(select(Company).where(
        Company.id.in_([r.comp_company_id for r in rels])))} if rels else {}
    scores = _scores(session, list(comps), day)
    start, end = candidates.window_bounds(day)
    out = []
    for r in rels:
        cc = comps.get(r.comp_company_id)
        if cc is None:
            continue
        evs = list(session.scalars(select(MaterialEvent).where(
            MaterialEvent.company_id == cc.id, MaterialEvent.materiality.in_(["MATERIAL", "WATCH"]),
            MaterialEvent.event_at >= start, MaterialEvent.event_at <= end)
            .order_by(MaterialEvent.event_at.desc(), MaterialEvent.id).limit(5)))
        tm, score = scores.get(cc.id, (None, None))
        out.append({"company": company_brief(cc), "comp_type": r.comp_type, "why_comparable": r.why_comparable,
                    "source_url": r.source_url, "confidence": r.confidence, "epistemic": r.epistemic,
                    "recent_events": [event_out(e, cc) for e in evs], "technical_momentum": tm,
                    "anti_score": score, "_founded": cc.founded_date})
    out.sort(key=lambda x: ({"DIRECT": 0, "OPEN_SOURCE": 1, "ADJACENT": 2, "INFRASTRUCTURE": 3,
                             "INCUMBENT": 4}.get(x["comp_type"], 5), x["company"]["name"].lower()))
    return out


def comps_map(session: Session, company: Company) -> dict[str, Any]:
    """direct = DIRECT; emerging = OPEN_SOURCE comps or comps founded in the last 2 years; adjacent = the rest."""
    day = data_day(session)
    all_comps = comps_of(session, company, day)
    young = day - timedelta(days=730)
    direct, emerging, adjacent = [], [], []
    for c in all_comps:
        founded = c.pop("_founded", None)
        if c["comp_type"] == "DIRECT":
            direct.append(c)
        elif c["comp_type"] == "OPEN_SOURCE" or (founded is not None and founded >= young):
            emerging.append(c)
        else:
            adjacent.append(c)
    return {"company": company_brief(company), "direct": direct, "emerging": emerging, "adjacent": adjacent}


def company_view(session: Session, company: Company, position: PortfolioPosition) -> dict[str, Any]:
    from radar.diligence.run import diligence_status
    from radar.portfolio.reports import latest_review_id
    from radar.services.freshness import data_age

    day = data_day(session)
    start, end = candidates.window_bounds(day)
    rows = session.execute(
        select(EventImpact, MaterialEvent).join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
        .where(EventImpact.company_id == company.id, MaterialEvent.event_at >= start, MaterialEvent.event_at <= end)
        .order_by(MaterialEvent.event_at.desc(), MaterialEvent.id)).all()
    subj = _subjects(session, [ev for _, ev in rows])
    own = [event_out(ev, subj[ev.company_id], relation="SELF") for imp, ev in rows if imp.relation == "SELF"]
    comp = [event_out(ev, subj[ev.company_id], relation=imp.relation) for imp, ev in rows if imp.relation != "SELF"]
    noise = session.scalar(select(func.count()).select_from(MaterialEvent).where(
        MaterialEvent.company_id == company.id, MaterialEvent.materiality == "NOISE",
        MaterialEvent.event_at >= start, MaterialEvent.event_at <= end)) or 0
    proxies = stored_proxies(session, company.id, day)
    qs = list(session.scalars(select(PortfolioQuestion).where(PortfolioQuestion.company_id == company.id)
                              .order_by(PortfolioQuestion.status != "OPEN", PortfolioQuestion.created_at.desc(),
                                        PortfolioQuestion.id)))
    last = _last_reviews(session, [company.id]).get(str(company.id))
    trace = list(session.scalars(select(PortfolioDecision).where(PortfolioDecision.company_id == company.id)
                                 .order_by(PortfolioDecision.created_at.desc(), PortfolioDecision.id).limit(50)))
    ages = data_age(session, "company", [company.id])
    age = ages.get(company.id)
    card = needs_attention(session, day, company_ids=[company.id], top=1, min_priority="LOW")
    return {
        "company": company_brief(company), "position": position_out(position), "events": own,
        "competitor_events": comp, "noise_count": int(noise),
        "metrics": compare_windows(proxies), "proxies": sorted(proxies.values(), key=lambda p: p["metric"]),
        "comps": [{k: v for k, v in c.items() if k != "_founded"} for c in comps_of(session, company, day)],
        "questions": questions_out(session, qs),
        "last_review": ({"id": str(last.id), "decision": last.decision, "note": last.note, "reviewer": last.reviewer,
                         "created_at": _iso(last.created_at)} if last else None),
        "next_review": (last.created_at + timedelta(days=REVIEW_INTERVAL_DAYS)).date().isoformat() if last else None,
        "decision_trace": decisions_out(trace),
        "data_age": age.model_dump(mode="json") if age is not None else None,
        "report_id": latest_review_id(session, company.id),
        "diligence": diligence_status(session, company.id),
        "needs_attention": card[0] if card else None,
    }


def list_events(session: Session, *, event_type: str | None = None, materiality: str | None = None,
                company: Company | None = None, relation: str | None = None, offset: int = 0, limit: int = 50
                ) -> dict[str, Any]:
    """Events, newest first. With ``company`` / ``relation`` the list is the impacts on portfolio companies
    (materiality then means materiality for that company); otherwise every stored event."""
    joined = company is not None or relation is not None
    if joined:
        q = select(MaterialEvent, EventImpact.relation).join(EventImpact, EventImpact.event_id == MaterialEvent.id)
        if company is not None:
            q = q.where(EventImpact.company_id == company.id)
        if relation is not None:
            q = q.where(EventImpact.relation == relation)
        if materiality is not None:
            q = q.where(EventImpact.materiality_for_company == materiality)
    else:
        q = select(MaterialEvent)
        if materiality is not None:
            q = q.where(MaterialEvent.materiality == materiality)
    if event_type is not None:
        q = q.where(MaterialEvent.event_type == event_type)
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    q = q.order_by(MaterialEvent.event_at.desc(), MaterialEvent.id).offset(offset).limit(limit)
    rows = [(r[0], r[1]) for r in session.execute(q).all()] if joined else [(e, None) for e in session.scalars(q)]
    subj = _subjects(session, [ev for ev, _ in rows])
    return {"total": int(total), "offset": offset, "limit": limit,
            "items": [event_out(ev, subj[ev.company_id], relation=rel) for ev, rel in rows]}


def event_detail(session: Session, ev: MaterialEvent) -> dict[str, Any]:
    from radar.diligence.run import diligence_status

    subject = session.get(Company, ev.company_id)
    imps = list(session.scalars(select(EventImpact).where(EventImpact.event_id == ev.id)))
    comps = {c.id: c for c in session.scalars(select(Company).where(
        Company.id.in_([i.company_id for i in imps])))} if imps else {}
    imps.sort(key=lambda i: (i.relation != "SELF", comps[i.company_id].name.lower() if i.company_id in comps else ""))
    qs = list(session.scalars(select(PortfolioQuestion).where(PortfolioQuestion.created_from_event_id == ev.id)
                              .order_by(PortfolioQuestion.created_at, PortfolioQuestion.id)))
    trace = list(session.scalars(select(PortfolioDecision).where(PortfolioDecision.event_id == ev.id)
                                 .order_by(PortfolioDecision.created_at, PortfolioDecision.id)))
    dil = None
    if ev.suggested_review == "RUN_DILIGENCE" or (ev.meta or {}).get("diligence_claim"):
        st = diligence_status(session, ev.company_id)
        dil = {"company_slug": subject.slug, "status": st["status"], "run_id": st.get("run_id"),
               "workspace": f"/diligence/{subject.slug}", "run_endpoint": f"/api/diligence/{subject.slug}/run",
               "claim": (ev.meta or {}).get("diligence_claim")}
    return {
        "event": event_out(ev, subject),
        "impacts": [{"company": company_brief(comps[i.company_id]), "relation": i.relation,
                     "impact_reason": i.impact_reason, "confidence": i.confidence,
                     "materiality_for_company": i.materiality_for_company} for i in imps if i.company_id in comps],
        "questions": questions_out(session, qs), "decision_trace": decisions_out(trace),
        "suggested_action": ev.suggested_review, "diligence": dil,
    }


def brief_out(r: ResearchReport) -> dict[str, Any]:
    return {"id": str(r.id), "date": (r.body or {}).get("date") or _iso(r.created_at.date()), "title": r.title,
            "body": r.body or {}, "markdown": r.markdown or "", "sources": r.sources or [],
            "generator": r.generator, "created_at": _iso(r.created_at)}


def brief_for(session: Session, day: str | None = None) -> ResearchReport | None:
    q = select(ResearchReport).where(ResearchReport.kind == "portfolio_brief")
    if day:
        q = q.where(ResearchReport.body["date"].as_string() == day)
    return session.scalar(q.order_by(ResearchReport.body["date"].as_string().desc(),
                                     ResearchReport.created_at.desc()).limit(1))


def brief_archive(session: Session, limit: int = 60) -> list[dict[str, Any]]:
    rows = session.scalars(select(ResearchReport).where(ResearchReport.kind == "portfolio_brief")
                           .order_by(ResearchReport.body["date"].as_string().desc(), ResearchReport.created_at.desc())
                           .limit(limit))
    out, seen = [], set()
    for r in rows:
        d = (r.body or {}).get("date")
        if d in seen:
            continue
        seen.add(d)
        out.append({"id": str(r.id), "date": d, "title": r.title})
    return out
