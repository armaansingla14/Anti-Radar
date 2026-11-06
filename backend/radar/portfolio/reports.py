"""PORTFOLIO ANALYST: deterministic reports from stored MaterialEvents (no LLM; ``generator='template'``).

* ``portfolio_review`` — one per portfolio company (with >= 1 MATERIAL / WATCH impact in the window, or on demand):
  COMPANY, PERIOD, WHAT CHANGED, MATERIAL EVENTS by area, OPEN QUESTIONS, SUGGESTED REVIEW, SOURCES.
* ``portfolio_brief`` — one per day: 5 THINGS WORTH KNOWING, NEEDS ATTENTION, TECHNICAL CHANGES, COMPETITIVE MOVES,
  TALENT CHANGES, NOISE FILTERED (the funnel).

Every factual sentence is a claim ``{text, epistemic, source_ids}`` citing S-ids registered in ``sources``;
interpretation is INFERENCE. Titles from external pages are untrusted: they are quoted, never followed, and a title
with instruction-like text is withheld (flagged in the trace). A financing seen only in a third-party story is
reported as unconfirmed, without repeating its claim. Both kinds are evaluated by the V3 agent control plane.
"""

from __future__ import annotations

import time as _time
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.llm.guard import detect_injection
from radar.models import Company, EventImpact, MaterialEvent, PortfolioQuestion, PortfolioRun, ResearchReport
from radar.portfolio import attention, candidates
from radar.portfolio.run import WINDOW_DAYS, attention_items, portfolio_companies

AGENT = "PORTFOLIO ANALYST"
GROUPS = ("TECHNICAL", "PRODUCT", "TALENT", "COMMERCIAL", "FINANCING", "COMPETITION", "RISK", "OTHER")
TYPE_GROUP = {"TECHNICAL": "TECHNICAL", "PRODUCT": "PRODUCT", "TALENT": "TALENT", "COMMERCIAL": "COMMERCIAL",
              "FINANCING": "FINANCING", "RISK": "RISK", "REGULATORY": "OTHER", "STRATEGIC": "OTHER",
              "COMPETITIVE": "COMPETITION"}
ORDER = {"MATERIAL": 0, "WATCH": 1, "NOISE": 2}
ACTION_LABEL = {"REVIEW": "Review", "MONITOR": "Monitor", "UPDATE_COMPETITIVE_MAP": "Update competitive map",
                "RUN_DILIGENCE": "Run diligence", "PREPARE_FOUNDER_QUESTIONS": "Prepare founder questions",
                "NO_ACTION": "No action"}


class Sources:
    """S-id registry: one id per URL, in first-cited order."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._by_url: dict[str, str] = {}
        self.flags: list[dict[str, Any]] = []

    def add(self, *, url: str | None, title: str, source_type: str, snippet: str, fetched_at: str | None) -> str | None:
        if not url:
            return None
        if url in self._by_url:
            return self._by_url[url]
        sid = f"S{len(self.items) + 1}"
        self._by_url[url] = sid
        self.items.append({"id": sid, "url": url, "title": title[:300], "source_type": source_type,
                           "snippet": snippet[:500], "fetched_at": fetched_at})
        return sid


def _safe_title(ev: MaterialEvent, reg: Sources) -> str | None:
    hits = detect_injection(ev.title)
    if hits:
        reg.flags.append({"id": None, "url": (ev.source_urls or [None])[0], "phrases": hits[:3]})
        return None
    return ev.title


def _report_source_type(src: dict[str, Any]) -> str:
    st, pub = src.get("source_type"), src.get("publisher")
    if st == "website":
        return "company_announcement" if pub == "company" else "website"
    return str(st or "website")


def _register(ev: MaterialEvent, subject: Company, reg: Sources) -> list[str]:
    """S-ids for an event's sources (the snippet repeats the event facts the claim states)."""
    ids: list[str] = []
    meta = ev.meta or {}
    srcs = meta.get("sources") or [{"url": u, "title": ev.title, "source_type": "website", "publisher": "company"}
                                   for u in ev.source_urls or []]
    day = ev.event_at.date().isoformat()
    for s in srcs[:6]:
        st = _report_source_type(s)
        if st == "jobs":
            titles = "; ".join(meta.get("titles") or [])[:300]
            snippet = f"Public job board of {subject.name}: {ev.title}, latest {day}. {titles}"
            url = s.get("url")
            title = f"{subject.name} job postings"
        else:
            snippet = f"{s.get('title') or ev.title} — {day}. {(s.get('snippet') or ev.description or '')[:300]}"
            url, title = s.get("url"), s.get("title") or ev.title
        sid = reg.add(url=url, title=title, source_type=st, snippet=snippet, fetched_at=s.get("observed_at"))
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def _fmt_amount(v: Any) -> str | None:
    if not isinstance(v, int | float) or v <= 0:
        return None
    return f"${v:,.0f}"


def event_claim(ev: MaterialEvent, subject: Company, reg: Sources) -> dict[str, Any]:
    """The factual sentence for one event, citing its sources."""
    ids = _register(ev, subject, reg)
    out = _event_claim(ev, subject, reg, ids)
    if not ids:  # nothing to cite (e.g. postings without URLs): never present it as a cited fact
        out["epistemic"] = "INFERENCE"
    return out


def _event_claim(ev: MaterialEvent, subject: Company, reg: Sources, ids: list[str]) -> dict[str, Any]:
    meta = ev.meta or {}
    pubs = set(meta.get("publishers") or [])
    day = ev.event_at.date().isoformat()
    title = _safe_title(ev, reg)
    quoted = f"“{title}”" if title else "an item whose title is withheld (instruction-like text)"
    st = ev.event_subtype
    base = {"source_ids": ids, "event_id": str(ev.id), "materiality": ev.materiality}
    if ev.event_type == "FINANCING" and "company" not in pubs and "registry" not in pubs:
        return {**base, "epistemic": "INFERENCE",
                "text": f"A third-party story about a possible {st.replace('_', ' ')} involving {subject.name} "
                        f"appeared on {day}; no company or registry source confirms it."}
    if st == "government_contract" and "registry" in pubs:
        amt = _fmt_amount(meta.get("amount"))
        return {**base, "epistemic": "FACT",
                "text": f"USAspending.gov records a federal award to {subject.name} starting {day}: {quoted}"
                        + (f" ({amt} obligated)." if amt else ".")}
    if st in ("specialized_hiring_cluster", "new_role_class", "hiring_acceleration", "role_closures"):
        return {**base, "epistemic": "FACT", "text": f"Public job board of {subject.name}: {ev.title} (latest {day})."}
    if st == "repository_release" and meta.get("repo"):
        return {**base, "epistemic": "FACT", "text": f"{meta['repo']} published release {quoted} on {day}."}
    if "company" in pubs:
        return {**base, "epistemic": "FACT", "text": f"{subject.name} published {quoted} on {day}."}
    if "registry" in pubs:
        return {**base, "epistemic": "FACT", "text": f"A public record dated {day} lists {quoted} for {subject.name}."}
    points = meta.get("points")
    return {**base, "epistemic": ev.epistemic if ev.epistemic == "FACT" else "INFERENCE",
            "text": f"A third-party source dated {day} links to {subject.name}: {quoted}"
                    + (f" ({points} points on Hacker News)." if points else ".")}


def meaning_claim(ev: MaterialEvent, ids: list[str]) -> dict[str, Any]:
    return {"text": f"{ev.why_it_matters} ({ev.materiality} by {ev.materiality_rule}: {ev.materiality_reason})",
            "epistemic": "INFERENCE", "source_ids": ids, "event_id": str(ev.id)}


def _company_ref(c: Company) -> dict[str, Any]:
    return {"id": str(c.id), "slug": c.slug, "name": c.name, "website": c.website}


def _store(session: Session, *, kind: str, day: date, entity: Company | None, title: str, body: dict[str, Any],
           markdown: str, sources: list[dict[str, Any]], trace: list[dict[str, Any]]) -> ResearchReport:
    q = select(ResearchReport).where(ResearchReport.kind == kind,
                                     ResearchReport.body["date"].as_string() == day.isoformat())
    q = q.where(ResearchReport.entity_id == entity.id) if entity is not None else q.where(
        ResearchReport.entity_id.is_(None))
    existing = list(session.scalars(q.order_by(ResearchReport.created_at)))
    report = existing[0] if existing else ResearchReport(id=uuid.uuid4(), kind=kind)
    for dup in existing[1:]:
        session.delete(dup)
    report.entity_type = "company" if entity is not None else None
    report.entity_id = entity.id if entity is not None else None
    report.title = title[:500]
    report.body = body
    report.markdown = markdown
    report.sources = sources
    report.model = None
    report.generator = "template"
    report.status = "complete"
    report.trace = trace
    report.cost_usd = 0.0
    report.is_demo = bool(entity.is_demo) if entity is not None else False
    if not existing:
        session.add(report)
    session.flush()
    return report


def _trace(t0: float, steps: list[dict[str, Any]], reg: Sources) -> list[dict[str, Any]]:
    out = list(steps)
    if reg.flags:
        out.append({"type": "injection_flags", "flags": reg.flags})
    out.append({"type": "timing", "ms": round((_time.perf_counter() - t0) * 1000, 1)})
    return out


# ── portfolio_review ─────────────────────────────────────────────────────────


def build_review(session: Session, company: Company, day: date, *, window_days: int = WINDOW_DAYS
                 ) -> tuple[dict[str, Any], str, list[dict[str, Any]], Sources, int]:
    start, end = candidates.window_bounds(day, window_days)
    rows = session.execute(
        select(EventImpact, MaterialEvent).join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
        .where(EventImpact.company_id == company.id, MaterialEvent.event_at >= start, MaterialEvent.event_at <= end,
               MaterialEvent.status != "DISMISSED")).all()
    rows.sort(key=lambda r: (ORDER.get(r[0].materiality_for_company, 3), r[0].relation != "SELF",
                             -r[1].event_at.timestamp(), str(r[1].id)))
    subjects = {c.id: c for c in session.scalars(select(Company).where(
        Company.id.in_([ev.company_id for _, ev in rows])))} if rows else {}
    noise = session.scalar(select(PortfolioRun.counts).where(PortfolioRun.day == day)) or {}
    per = (noise.get("per_company") or {}).get(company.slug, {})
    reg = Sources()
    groups: dict[str, list[dict[str, Any]]] = {g: [] for g in GROUPS}
    what_changed: list[dict[str, Any]] = []
    items: list[attention.AttentionItem] = []
    for imp, ev in rows:
        subj = subjects[ev.company_id]
        claim = event_claim(ev, subj, reg)
        claim["materiality"] = imp.materiality_for_company
        claim["relation"] = imp.relation
        group = "COMPETITION" if imp.relation != "SELF" else TYPE_GROUP.get(ev.event_type, "OTHER")
        groups[group].append(claim)
        if imp.relation != "SELF":
            groups[group].append({"text": f"{imp.impact_reason} ({imp.materiality_for_company} for {company.name} "
                                          f"by M8.)", "epistemic": "INFERENCE", "source_ids": claim["source_ids"],
                                  "event_id": str(ev.id)})
        else:
            groups[group].append(meaning_claim(ev, claim["source_ids"]))
        if len(what_changed) < 6:
            what_changed.append(claim)
        meta = ev.meta or {}
        items.append(attention.AttentionItem(
            event_id=str(ev.id), company_id=str(company.id), company_name=company.name, company_slug=company.slug,
            subject_name=subj.name, relation=imp.relation, materiality=imp.materiality_for_company,
            event_type=ev.event_type, subtype=ev.event_subtype, title=ev.title, event_at=ev.event_at,
            families=list(meta.get("families") or []), source_urls=list(ev.source_urls or []),
            performance_claim=bool(meta.get("performance_claim")),
            diligence_claim=(meta.get("diligence_claim") or {}).get("text") if meta.get("diligence_claim") else None,
            meta={"role_class": meta.get("role_class")}))
    card = attention.build_card(items, end) if items else None
    sid_of = {s["url"]: s["id"] for s in reg.items}
    questions = []
    for q in session.scalars(select(PortfolioQuestion).where(PortfolioQuestion.company_id == company.id,
                                                             PortfolioQuestion.status == "OPEN")
                             .order_by(PortfolioQuestion.created_at, PortfolioQuestion.id)):
        ev = session.get(MaterialEvent, q.created_from_event_id) if q.created_from_event_id else None
        url = (ev.source_urls or [None])[0] if ev else None
        questions.append({"question": q.question, "reason": q.reason, "source": q.source, "question_id": str(q.id),
                          "event_id": str(q.created_from_event_id) if q.created_from_event_id else None,
                          "source_ids": [sid_of[url]] if url in sid_of else []})
    if card is not None:
        action, reason = card.suggested_action, card.action_reason
        why = card.why_surfaced
    else:
        action, reason, why = "NO_ACTION", "no material or watch events in the period", "no material change"
    all_ids = sorted({i for c in what_changed for i in c["source_ids"]}, key=lambda s: int(s[1:]))
    body = {
        "agent": AGENT, "kind": "portfolio_review", "date": day.isoformat(), "company": _company_ref(company),
        "period": {"start": start.date().isoformat(), "end": day.isoformat(), "days": window_days},
        "summary": {"text": f"{company.name}: {why}." if card else
                    f"No material or watch event for {company.name} in the {window_days} days to {day.isoformat()}.",
                    "epistemic": "INFERENCE", "source_ids": all_ids[:6]},
        "what_changed": what_changed,
        "material_events": groups,
        "open_questions": questions,
        "suggested_review": {"action": action, "label": ACTION_LABEL[action],
                             "reason": {"text": f"Suggested: {ACTION_LABEL[action].lower()}, because {reason}.",
                                        "epistemic": "INFERENCE", "source_ids": all_ids[:6]}},
        "noise_filtered": {"raw_changes": per.get("raw", 0), "noise_events": per.get("noise", 0)},
        "limitations": "Public sources only (company site and newsroom, job board, GitHub, arXiv, Hacker News, "
                       "USAspending). No private data; proxies are not internal metrics.",
    }
    return body, render_review(body, reg.items), reg.items, reg, len(rows)


def generate_review(session: Session, company: Company, day: date, *, window_days: int = WINDOW_DAYS
                    ) -> ResearchReport:
    t0 = _time.perf_counter()
    body, md, sources, reg, n = build_review(session, company, day, window_days=window_days)
    return _store(session, kind="portfolio_review", day=day, entity=company,
                  title=f"Portfolio review — {company.name} — {day:%b} {day.day}, {day.year}", body=body,
                  markdown=md, sources=sources,
                  trace=_trace(t0, [{"type": "step", "label": f"{n} impacts in the period"}], reg))


def generate_reviews(session: Session, day: date, *, window_days: int = WINDOW_DAYS) -> list[ResearchReport]:
    """One review per portfolio company with >= 1 MATERIAL / WATCH impact in the window."""
    start, end = candidates.window_bounds(day, window_days)
    ids = set(session.scalars(
        select(EventImpact.company_id).join(MaterialEvent, MaterialEvent.id == EventImpact.event_id)
        .where(MaterialEvent.event_at >= start, MaterialEvent.event_at <= end,
               EventImpact.materiality_for_company.in_(["MATERIAL", "WATCH"]))))
    out = []
    for cid, company in sorted(portfolio_companies(session).items(), key=lambda kv: kv[1].slug):
        if cid in ids:
            out.append(generate_review(session, company, day, window_days=window_days))
    return out


def render_review(body: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    c = body["company"]
    lines = [f"# Portfolio review: {c['name']}", "", f"**Period** {body['period']['start']} → {body['period']['end']}",
             "", f"_{body['summary']['text']}_ (INFERENCE)", "", "## What changed"]
    lines += [f"- [{x['materiality']}] {x['text']} {_cite(x)}" for x in body["what_changed"]] or ["- No change."]
    lines += ["", "## Material events"]
    for g in GROUPS:
        xs = body["material_events"].get(g) or []
        if xs:
            lines += ["", f"### {g}"] + [f"- {x['text']} ({x['epistemic']}) {_cite(x)}" for x in xs]
    lines += ["", "## Open questions"]
    lines += [f"- {q['question']} {_cite(q)}" for q in body["open_questions"]] or ["- None open."]
    sr = body["suggested_review"]
    lines += ["", "## Suggested review", f"**{sr['label']}** — {sr['reason']['text']}"]
    lines += ["", "## Sources"] + [f"- [{s['id']}] {s['title']} — {s['url']}" for s in sources]
    lines += ["", f"_{body['limitations']}_"]
    return "\n".join(lines) + "\n"


def _cite(x: dict[str, Any]) -> str:
    ids = x.get("source_ids") or []
    return " ".join(f"[{i}]" for i in ids)


# ── portfolio_brief ──────────────────────────────────────────────────────────


def build_brief(session: Session, day: date, *, window_days: int = WINDOW_DAYS
                ) -> tuple[dict[str, Any], str, list[dict[str, Any]], Sources]:
    start, end = candidates.window_bounds(day, window_days)
    reg = Sources()
    items = attention_items(session, day, window_days=window_days)
    events = {e.id: e for e in session.scalars(select(MaterialEvent).where(
        MaterialEvent.id.in_([uuid.UUID(i.event_id) for i in items])))} if items else {}
    subjects = {c.id: c for c in session.scalars(select(Company).where(
        Company.id.in_([e.company_id for e in events.values()])))} if events else {}
    claim_cache: dict[uuid.UUID, dict[str, Any]] = {}

    def claim_of(event_id: str) -> dict[str, Any]:
        ev = events[uuid.UUID(event_id)]
        if ev.id not in claim_cache:
            claim_cache[ev.id] = event_claim(ev, subjects[ev.company_id], reg)
        return dict(claim_cache[ev.id])

    ranked = sorted(items, key=lambda i: (ORDER.get(i.materiality, 3), i.relation != "SELF", -i.event_at.timestamp(),
                                          i.company_name, i.event_id))
    five, seen = [], set()
    for i in ranked:
        if i.event_id in seen:
            continue
        seen.add(i.event_id)
        c = claim_of(i.event_id)
        c.update({"materiality": i.materiality, "company": i.company_name, "company_slug": i.company_slug,
                  "relation": i.relation})
        five.append(c)
        if len(five) >= 5:
            break
    cards = attention.rank(items, end, window_days=window_days)
    needs = []
    for card in cards:
        ids = sorted({sid for w in card.what_changed for sid in claim_of(w["event_id"])["source_ids"]},
                     key=lambda s: int(s[1:]))
        needs.append({
            "company": {"slug": card.company_slug, "name": card.company_name, "id": card.company_id},
            "priority": card.priority, "score": card.score,
            "why_surfaced": {"text": f"{card.company_name}: {card.why_surfaced}.", "epistemic": "INFERENCE",
                             "source_ids": ids[:8]},
            "what_changed": [{**claim_of(w["event_id"]), "materiality": w["materiality"], "relation": w["relation"]}
                             for w in card.what_changed],
            "what_this_could_mean": {"text": card.what_this_could_mean, "epistemic": "INFERENCE",
                                     "source_ids": ids[:8]},
            "suggested_action": card.suggested_action, "action_label": ACTION_LABEL[card.suggested_action],
            "action_reason": card.action_reason,
            "questions": [{"question": q["text"], "event_id": q["event_id"], "source": q["source"],
                           "source_ids": claim_of(q["event_id"])["source_ids"][:2]} for q in card.questions],
        })

    def section(pred) -> list[dict[str, Any]]:
        out, seen_ids = [], set()
        for i in ranked:
            if pred(i) and i.event_id not in seen_ids and len(out) < 8:
                seen_ids.add(i.event_id)
                c = claim_of(i.event_id)
                c.update({"materiality": i.materiality, "company": i.company_name, "relation": i.relation})
                out.append(c)
        return out

    technical = section(lambda i: i.relation == "SELF" and i.event_type == "TECHNICAL")
    competitive = section(lambda i: i.relation != "SELF")
    talent = section(lambda i: i.relation == "SELF" and i.event_type == "TALENT")
    counts = session.scalar(select(PortfolioRun.counts).where(PortfolioRun.day == day)) or {}
    funnel = {k: int(counts.get(k) or 0) for k in ("raw_changes", "candidates", "after_dedup", "material", "watch",
                                                    "noise", "companies_surfaced")}
    body = {
        "agent": AGENT, "kind": "portfolio_brief", "date": day.isoformat(),
        "period": {"start": start.date().isoformat(), "end": day.isoformat(), "days": window_days},
        "five_things": five, "needs_attention": needs, "technical_changes": technical,
        "competitive_moves": competitive, "talent_changes": talent,
        "noise_filtered": {**funnel, "text": (
            f"{funnel['raw_changes']} raw changes → {funnel['candidates']} candidates → {funnel['after_dedup']} "
            f"after deduplication → {funnel['material']} material and {funnel['watch']} to watch "
            f"({funnel['noise']} filtered as noise) → {funnel['companies_surfaced']} companies need attention.")},
        "limitations": "Public sources only. No ownership, marks or private metrics. X is not collected without API "
                       "access; press beyond Hacker News is not collected.",
    }
    return body, render_brief(body, reg.items), reg.items, reg


def generate_brief(session: Session, day: date, *, window_days: int = WINDOW_DAYS) -> ResearchReport:
    t0 = _time.perf_counter()
    body, md, sources, reg = build_brief(session, day, window_days=window_days)
    steps = [{"type": "step", "label": f"{len(body['needs_attention'])} companies need attention; "
                                       f"{len(body['five_things'])} items worth knowing"}]
    return _store(session, kind="portfolio_brief", day=day, entity=None,
                  title=f"Portfolio Brief — {day:%A, %B} {day.day}, {day.year}", body=body, markdown=md,
                  sources=sources, trace=_trace(t0, steps, reg))


def render_brief(body: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    lines = [f"# Portfolio Brief — {body['date']}", "", "## 5 things worth knowing"]
    lines += [f"{n}. [{x['materiality']}] {x['company']}: {x['text']} {_cite(x)}"
              for n, x in enumerate(body["five_things"], 1)] or ["No material or watch event in the period."]
    lines += ["", "## Needs attention"]
    for c in body["needs_attention"]:
        lines += [f"### {c['company']['name']} — {c['priority']}", f"{c['why_surfaced']['text']} (INFERENCE)",
                  f"_{c['what_this_could_mean']['text']}_ (INFERENCE)",
                  f"**Suggested:** {c['action_label']} — {c['action_reason']}"]
        lines += [f"- {q['question']} {_cite(q)}" for q in c["questions"]]
    if not body["needs_attention"]:
        lines.append("Nothing needs attention.")
    for key, label in (("technical_changes", "Technical changes"), ("competitive_moves", "Competitive moves"),
                       ("talent_changes", "Talent changes")):
        lines += ["", f"## {label}"]
        lines += [f"- [{x['materiality']}] {x['company']}: {x['text']} {_cite(x)}" for x in body[key]] or ["- None."]
    lines += ["", "## Noise filtered", body["noise_filtered"]["text"]]
    lines += ["", "## Sources"] + [f"- [{s['id']}] {s['title']} — {s['url']}" for s in sources]
    lines += ["", f"_{body['limitations']}_"]
    return "\n".join(lines) + "\n"


def latest_review_id(session: Session, company_id: uuid.UUID) -> str | None:
    rid = session.scalar(select(ResearchReport.id).where(ResearchReport.kind == "portfolio_review",
                                                         ResearchReport.entity_id == company_id)
                         .order_by(ResearchReport.created_at.desc()).limit(1))
    return str(rid) if rid else None
