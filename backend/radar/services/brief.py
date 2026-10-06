"""The ANTI RADAR Morning Brief.

Selection is deterministic: every "what changed" line is computed from stored
scores, metric snapshots, jobs and signals, and carries the signal ids that back
it plus entity references. A candidate line with no backing signal is dropped —
the brief never claims something the data doesn't show. Only the short summary
paragraph may come from an LLM (``narratives.morning_brief_summary``), and it is
labeled with its generator.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import is_technical_class, job_class, job_start
from radar.llm import narratives
from radar.models import Company, EntityTopic, Job, MetricSnapshot, Person, Repository, Topic
from radar.schemas.api import BriefItem, IntelCard, MorningBrief, Narrative, PaperCard, StrangeSignal
from radar.scoring.extract import window_delta
from radar.services import presenters as P
from radar.services.entities import signal_activity
from radar.services.feed import (
    candidate_companies,
    candidate_people,
    recent_papers,
    strange_signals,
    top_company_cards,
    top_founder_cards,
)
from radar.settings import get_settings

MAX_ITEMS = 5
FOUNDER_SURFACE_FLOOR = 40
HIRING_DAYS = 14
_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def _num(n: int) -> str:
    return _WORDS.get(n, str(n))


def _ref(kind: str, e: Any) -> dict[str, Any]:
    slug = getattr(e, "slug", None) or getattr(e, "full_name", None)
    name = getattr(e, "name", None) or getattr(e, "full_name", None)
    return {"type": kind, "id": str(e.id), "slug": slug, "name": name, "is_demo": bool(e.is_demo)}


def _ids(signals: Sequence[Any], limit: int) -> list[str]:
    return [str(s.id) for s in signals[:limit]]


# ── candidate "what changed" lines ───────────────────────────────────────────


def _theme_acceleration(session: Session, companies: Sequence[Company]) -> dict[str, Any] | None:
    """Theme with the most companies whose score rose over 7 days AND that produced signals this week."""
    if not companies:
        return None
    ids = [c.id for c in companies]
    snaps = P.latest_snapshots(session, ids)
    d7 = P.score_deltas(session, snaps, 7)
    act = signal_activity(session, ids, clock.now() - timedelta(days=7))
    themes: dict[str, list[uuid.UUID]] = defaultdict(list)
    for eid, theme in session.execute(
            select(EntityTopic.entity_id, Topic.theme).join(Topic, Topic.id == EntityTopic.topic_id)
            .where(EntityTopic.entity_id.in_(ids)).distinct()):
        themes[theme].append(eid)
    by_id = {c.id: c for c in companies}
    best: tuple[str, list[Company]] | None = None
    for theme, cids in sorted(themes.items()):
        accel = [by_id[c] for c in cids if (d7.get(c) or 0) > 0 and act.get(c, {}).get("count")]
        if len(accel) >= 2 and (best is None or len(accel) > len(best[1])):
            best = (theme, accel)
    if best is None:
        return None
    theme, accel = best
    accel.sort(key=lambda c: -(d7.get(c.id) or 0))
    names = ", ".join(f"{c.name} (+{d7[c.id]:g})" for c in accel[:4])
    return {
        "text": (f"{P.theme_label(theme)} activity accelerated across {_num(len(accel)).lower()} tracked companies "
                 f"— ANTI SCORE up over 7 days with fresh signals: {names}."),
        "entity_refs": [_ref("company", c) for c in accel[:4]],
        "evidence": [sid for c in accel[:4] for sid in _ids(act[c.id]["signals"], 2)],
    }


def _founders_surfaced(session: Session, people: Sequence[Person]) -> dict[str, Any] | None:
    cutoff = clock.now() - timedelta(days=7)
    fresh = [p for p in people if p.first_seen_at and p.first_seen_at >= cutoff]
    snaps = P.latest_snapshots(session, [p.id for p in fresh])
    strong = sorted([p for p in fresh if p.id in snaps and snaps[p.id].score >= FOUNDER_SURFACE_FLOOR],
                    key=lambda p: -snaps[p.id].score)
    act = signal_activity(session, [p.id for p in strong], clock.now() - timedelta(days=30))
    strong = [p for p in strong if act.get(p.id, {}).get("count")]
    if not strong:
        return None
    n = len(strong)
    names = ", ".join(f"{p.name} ({round(snaps[p.id].score)})" for p in strong[:4])
    # "Founder" only when every named person has a founder relation on record; a founder *score* is
    # a ranking, not evidence that someone founded a company.
    links = P.founder_links(session, [p.id for p in strong])
    founders = all(links.get(p.id) is not None and links[p.id].is_founder for p in strong)
    noun = ("technical founder", "technical founders") if founders else ("technical person", "technical people")
    subject = f"newly surfaced {noun[0] if n == 1 else noun[1]}"
    verb = "has a founder score" if n == 1 else "have founder scores"
    return {
        "text": f"{_num(n)} {subject} {verb} of {FOUNDER_SURFACE_FLOOR} or more: {names}.",
        "entity_refs": [_ref("person", p) for p in strong[:4]],
        "evidence": [sid for p in strong[:4] for sid in _ids(act[p.id]["signals"], 2)],
    }


def _github_velocity(session: Session, include_demo: bool) -> dict[str, Any] | None:
    today = clock.today()
    q = select(Repository).where(Repository.archived.is_(False))
    if not include_demo:
        q = q.where(Repository.is_demo.is_(False))
    repos = list(session.scalars(q))
    if len(repos) < 2:
        return None
    pts: dict[uuid.UUID, list[tuple[date, float]]] = defaultdict(list)
    for eid, d, v in session.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value)
            .where(MetricSnapshot.entity_id.in_([r.id for r in repos]), MetricSnapshot.metric == "stars",
                   MetricSnapshot.day >= today - timedelta(days=45), MetricSnapshot.day <= today)
            .order_by(MetricSnapshot.day)):
        pts[eid].append((d, v))
    gains = {}
    for r in repos:
        if pts.get(r.id):
            g = window_delta(pts[r.id], today, 0, 30, r.gh_created_at.date() if r.gh_created_at else None)
            if g is not None and g > 0:
                gains[r.id] = g
    if len(gains) < 2:
        return None
    ranked = sorted(gains, key=lambda i: -gains[i])
    top = next(r for r in repos if r.id == ranked[0])
    owner_ids = [top.id] + ([top.company_id] if top.company_id else [])
    act = signal_activity(session, owner_ids, clock.now() - timedelta(days=30))
    sigs = sorted({s.id: s for a in act.values() for s in a["signals"] if s.source_type == "github"}.values(),
                  key=lambda s: s.event_at, reverse=True)
    if not sigs:
        return None
    new = bool(top.gh_created_at and top.gh_created_at >= clock.now() - timedelta(days=90))
    company = session.get(Company, top.company_id) if top.company_id else None
    refs = [_ref("repository", top)] + ([_ref("company", company)] if company else [])
    owner = f" ({company.name})" if company else ""
    return {
        "text": (f"{'A new' if new else 'The'} repository {top.full_name}{owner} led tracked GitHub velocity: "
                 f"+{int(gains[top.id]):,} stars in 30 days, #1 of {len(gains)} repositories gaining stars."),
        "entity_refs": refs, "evidence": _ids(sigs, 3),
    }


def _hiring(session: Session, companies: Sequence[Company]) -> dict[str, Any] | None:
    if not companies:
        return None
    by_id = {c.id: c for c in companies}
    lo = clock.now() - timedelta(days=HIRING_DAYS)
    counts: dict[uuid.UUID, int] = defaultdict(int)
    for j in session.scalars(select(Job).where(Job.company_id.in_(list(by_id)))):
        if job_start(j) >= lo and is_technical_class(job_class(j)):
            counts[j.company_id] += 1
    if not counts:
        return None
    act = signal_activity(session, list(counts), lo)
    backed = sorted([c for c in counts if any(s.source_type == "jobs" for s in act.get(c, {}).get("signals", []))],
                    key=lambda c: (-counts[c], by_id[c].name))
    if not backed:
        return None
    total = sum(counts[c] for c in backed)
    names = ", ".join(f"{by_id[c].name} ({counts[c]})" for c in backed[:4])
    subj = "company" if len(backed) == 1 else "companies"
    return {
        "text": (f"{_num(len(backed))} tracked {subj} added {total} technical role{'s' if total != 1 else ''} "
                 f"in the last {HIRING_DAYS} days: {names}."),
        "entity_refs": [_ref("company", by_id[c]) for c in backed[:4]],
        "evidence": [str(s.id) for c in backed[:4] for s in act[c]["signals"] if s.source_type == "jobs"][:8],
    }


def _strange(s: StrangeSignal | None) -> dict[str, Any] | None:
    if s is None:
        return None
    ev = [e.signal_id for e in s.evidence if e.signal_id]
    if not ev:
        return None
    refs = [{**e, "is_demo": e.get("is_demo", s.is_demo)} for e in s.entities]
    return {"text": f"Strange signal: {s.headline}.", "entity_refs": refs, "evidence": ev}


def _top_mover(session: Session, companies: Sequence[Company], used: set[str]) -> dict[str, Any] | None:
    ids = [c.id for c in companies]
    snaps = P.latest_snapshots(session, ids)
    d7 = P.score_deltas(session, snaps, 7)
    movers = sorted([c for c in companies if (d7.get(c.id) or 0) > 0 and str(c.id) not in used],
                    key=lambda c: -(d7[c.id] or 0))
    if not movers:
        return None
    c = movers[0]
    act = signal_activity(session, [c.id], clock.now() - timedelta(days=7))
    if not act.get(c.id):
        return None
    return {"text": f"{c.name}'s ANTI SCORE rose +{d7[c.id]:g} over 7 days to {round(snaps[c.id].score)}.",
            "entity_refs": [_ref("company", c)], "evidence": _ids(act[c.id]["signals"], 3)}


def what_changed(session: Session, *, include_demo: bool, strange: StrangeSignal | None) -> list[BriefItem]:
    companies = candidate_companies(session, include_demo=include_demo)
    people = candidate_people(session, include_demo=include_demo, floor=0)
    raw = [_theme_acceleration(session, companies), _founders_surfaced(session, people),
           _github_velocity(session, include_demo), _hiring(session, companies), _strange(strange)]
    items = [r for r in raw if r and r["evidence"]]
    if len(items) < MAX_ITEMS:
        used = {ref["id"] for r in items for ref in r["entity_refs"]}
        mover = _top_mover(session, companies, used)
        if mover:
            items.append(mover)
    return [BriefItem(n=i, text=r["text"], entity_refs=r["entity_refs"], evidence=list(dict.fromkeys(r["evidence"])))
            for i, r in enumerate(items[:MAX_ITEMS], 1)]


# ── markdown ─────────────────────────────────────────────────────────────────


def _link(ref: dict[str, Any]) -> str:
    path = {"company": "/companies/", "person": "/founders/"}.get(ref.get("type", ""))
    demo = " `DEMO`" if ref.get("is_demo") else ""
    if path and ref.get("slug"):
        return f"[{ref['name']}]({path}{ref['slug']}){demo}"
    if ref.get("type") == "repository" and ref.get("slug"):
        host = "github.example" if ref.get("is_demo") else "github.com"
        return f"[{ref['slug']}](https://{host}/{ref['slug']}){demo}"
    return f"{ref.get('name')}{demo}"


def _delta(d: float | None) -> str:
    return f" ({'+' if d > 0 else ''}{d:g} 7d)" if d else ""


def _card_md(c: IntelCard, path: str) -> list[str]:
    demo = " `DEMO`" if c.is_demo else ""
    lines = [f"- **[{c.name}]({path}{c.slug})**{demo} — {c.kind_label.title()}, ANTI SCORE {c.score}{_delta(c.delta_7d)}"]
    if c.one_liner:
        lines.append(f"  - {c.one_liner}")
    for e in c.why_surfaced[:3]:
        src = f" ([source]({e.source_url}))" if e.source_url else ""
        lines.append(f"  - {e.title}{src}")
    return lines


def _paper_md(p: PaperCard) -> list[str]:
    demo = " `DEMO`" if p.is_demo else ""
    title = f"[{p.title}]({p.url})" if p.url else p.title
    lines = [f"- **{title}**{demo} — {', '.join(p.authors[:4]) or 'Unknown authors'}"
             + (f" · {p.published_at:%b %-d}" if p.published_at else "")]
    if p.why_it_matters:
        lines.append(f"  - {p.why_it_matters}")
    if p.code_urls:
        lines.append(f"  - Code: {p.code_urls[0]}")
    return lines


def render_markdown(day: date, items: Sequence[BriefItem], founders: Sequence[IntelCard],
                    companies: Sequence[IntelCard], research: Sequence[PaperCard], strange: StrangeSignal | None,
                    narrative: Narrative | None, demo_present: bool) -> str:
    gen = narrative.generator if narrative else "template"
    label = {"llm": "LLM-generated", "cached": "LLM-generated (cached)", "template": "deterministic template"}[gen]
    out = [f"# ANTI RADAR MORNING BRIEF — {day:%B} {day.day}, {day.year}".upper(), "",
           f"_Conviction Before Consensus · summary: {label} · generated {clock.now():%Y-%m-%d %H:%M} UTC_", ""]
    note = P.data_note(False) if clock.as_of() else None
    if note:
        out += [f"> {note}", ""]
    if demo_present:
        out += ["> Entries marked `DEMO` are fictional examples included to demonstrate the product.", ""]
    if narrative:
        out += ["## Summary", "", narrative.text, ""]
    out += ["## What changed", ""]
    if items:
        for it in items:
            refs = ", ".join(_link(r) for r in it.entity_refs[:4])
            out.append(f"{it.n}. {it.text}" + (f" — {refs}" if refs else ""))
    else:
        out.append("_No significant changes detected today._")
    out.append("")
    out += ["## People to watch", ""]
    for c in founders:
        out += _card_md(c, "/founders/")
    if not founders:
        out.append("_None above the founder-score floor today._")
    out += ["", "## Companies to watch", ""]
    for c in companies:
        out += _card_md(c, "/companies/")
    if not companies:
        out.append("_No scored companies yet._")
    out += ["", "## Research", ""]
    for p in research:
        out += _paper_md(p)
    if not research:
        out.append("_No recent thesis-relevant papers._")
    out += ["", "## Strange signal", ""]
    if strange:
        refs = ", ".join(_link({**e, "is_demo": strange.is_demo}) for e in strange.entities[:4])
        out += [f"**{strange.headline}**" + (" `DEMO`" if strange.is_demo else ""), "", strange.detail, ""]
        if refs:
            out.append(f"Entities: {refs}")
        for e in strange.evidence[:4]:
            out.append(f"- {e.title}" + (f" ([source]({e.source_url}))" if e.source_url else ""))
    else:
        out.append("_No strange signals today._")
    footer = ("_Lines under “What changed” are computed from stored signals; each is backed by the "
              "signal ids in the JSON brief. The summary is labeled with how it was produced._")
    out += ["", "---", footer, ""]
    return "\n".join(out)


# ── public ───────────────────────────────────────────────────────────────────


def morning_brief(session: Session, *, include_demo: bool | None = None) -> MorningBrief:
    include_demo = get_settings().demo_mode if include_demo is None else include_demo
    day = clock.today()
    strange_all = strange_signals(session, include_demo=include_demo, limit=1)
    strange = strange_all[0] if strange_all else None
    items = what_changed(session, include_demo=include_demo, strange=strange)
    founders = top_founder_cards(session, include_demo=include_demo, limit=3)
    companies = top_company_cards(session, include_demo=include_demo, limit=3)
    research = recent_papers(session, include_demo=include_demo, days=30, limit=3)
    narrative = Narrative.model_validate(narratives.morning_brief_summary([i.model_dump() for i in items]))
    demo_present = any(c.is_demo for c in [*founders, *companies]) or any(p.is_demo for p in research) or bool(
        strange and strange.is_demo) or any(r.get("is_demo") for i in items for r in i.entity_refs)
    md = render_markdown(day, items, founders, companies, research, strange, narrative, demo_present)
    return MorningBrief(date=day, generated_at=clock.now(), what_changed=items, founders_to_know=founders, companies_to_watch=companies,
                        research=research, strange_signal=strange, narrative=narrative, markdown=md,
                        generator=narrative.generator)
