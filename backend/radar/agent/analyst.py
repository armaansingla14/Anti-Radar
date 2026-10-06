"""RADAR ANALYST — the research agent behind "Investigate".

Flow for :func:`investigate`:

a. Deterministic pre-gather (profile, signals, metrics, people, papers). Every item
   is registered as a source first, so even without an LLM the brief is sourced.
b. With a live LLM: a deep-tier tool loop (≤10 turns, budget-capped) gathers more
   evidence, then a structured ``AntiBrief`` is written from the source blocks and
   its citations are validated against the registry.
c. Without an LLM (or on refusal/failure): a deterministic template brief built
   only from database facts, every claim citing its source id.

Progress is reported through ``on_event`` dicts:
``{'type': 'step'|'tool_call'|'tool_result'|'done'|'error', ...}``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from radar import clock
from radar.agent.brief_schema import BRIEF_CLAIM_SECTIONS, AntiBrief, Claim, ClaimEpistemic
from radar.agent.render import brief_to_markdown
from radar.agent.tools import AnalystTools, Gathered, SourceRegistry, resolve_entity
from radar.llm.guard import SYSTEM_GUARD, escape_content, validate_citations
from radar.llm.narratives import template_questions, why_anti_template, why_now_template
from radar.llm.prompts import load_prompt
from radar.llm.provider import BaseProvider, LLMError, get_llm
from radar.models import Company, Person, ResearchReport
from radar.settings import get_settings
from radar.thesis.loader import get_thesis

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]
MAX_TURNS = 10
LOOP_BUDGET_SHARE = 0.7  # the rest of the per-investigation budget is reserved for the final brief
_FAMILY_LABEL = {"github": "GitHub", "arxiv": "arXiv", "hackernews": "Hacker News", "jobs": "hiring",
                 "website": "website", "x": "X"}


class _Events:
    def __init__(self, sink: EventSink | None) -> None:
        self.sink = sink
        self.trace: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.trace.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception as e:  # noqa: BLE001 — a broken listener must not abort research
                log.warning("event sink failed: %s", e)


def _load_entity(session: Session, entity_type: str, entity_id: str | uuid.UUID) -> Company | Person:
    return resolve_entity(session, entity_type, str(entity_id))


def gather(session: Session, tools: AnalystTools, entity: Company | Person, emit: _Events) -> Gathered:
    """Deterministic pre-gather; registers sources in a stable order."""
    etype = "company" if isinstance(entity, Company) else "person"
    emit({"type": "step", "label": f"Loading {etype} profile and provenance"})
    prof, prov, repos = tools.profile(entity)
    g = Gathered(entity=entity, entity_type=etype, profile_sid=prof.id, provenance_sids=[p.id for p in prov],
                 repos=[(r, s.id) for r, s in repos])
    emit({"type": "step", "label": "Collecting signals from the last 90 days"})
    g.signals = [(sig, src.id) for sig, src in tools.signals(entity, days=90)]
    emit({"type": "step", "label": "Computing metric windows and latest Anti Score"})
    windows, score, msrc = tools.metrics(entity)
    g.metrics, g.score, g.metrics_sid = windows, score, (msrc.id if msrc else None)
    if isinstance(entity, Company):
        emit({"type": "step", "label": "Loading founders and team"})
        g.people = [(p, cp, s.id) for p, cp, s in tools.people(entity)]
    emit({"type": "step", "label": "Loading linked research papers"})
    g.papers = [(p, s.id) for p, s in tools.papers(entity)]
    emit({"type": "step", "label": f"Pre-gathered {len(tools.registry.ids())} sources"})
    return g


# ── deterministic template ───────────────────────────────────────────────────


def _c(text: str, epistemic: ClaimEpistemic, *sids: str | None) -> Claim:
    return Claim(text=text, epistemic=epistemic, source_ids=[s for s in dict.fromkeys(sids) if s])


def _sig_epistemic(value: str | None) -> ClaimEpistemic:
    return value if value in ("FACT", "INFERENCE", "MODEL_OPINION") else "FACT"  # type: ignore[return-value]


def template_brief(session: Session, g: Gathered, tools: AnalystTools) -> AntiBrief:
    """A brief built only from gathered DB facts. Every claim cites registered sources."""
    e = g.entity
    today = clock.today()
    reg = tools.registry
    what: list[Claim] = []
    if isinstance(e, Company) and e.description:
        what.append(_c(e.description, "FACT", g.profile_sid))
    if isinstance(e, Person) and e.bio:
        what.append(_c(f"{e.name}: {e.bio}", "FACT", g.profile_sid))
    for r, sid in g.repos[:3]:
        if r.description:
            what.append(_c(f"GitHub repository {r.full_name}: {r.description} ({r.stars:,} stars).", "FACT", sid))

    founders: list[Claim] = []
    if isinstance(e, Company):
        for p, cp, sid in g.people[:6]:
            role = (cp.role if cp else None) or "role unknown"
            prior = f"; prior affiliations: {', '.join(p.prior_affiliations)}" if p.prior_affiliations else ""
            founders.append(_c(f"{p.name} — {role}{' (founder)' if cp and cp.is_founder else ''}{prior}.",
                               "FACT", sid))
    elif e.current_company or e.prior_affiliations:
        cur = f"Currently {e.current_role or 'role unknown'} at {e.current_company}" if e.current_company else ""
        prior = f"prior affiliations: {', '.join(e.prior_affiliations)}" if e.prior_affiliations else ""
        founders.append(_c("; ".join(x for x in (cur, prior) if x) + ".", "FACT", g.profile_sid))

    since30 = clock.now() - timedelta(days=30)
    changed = [_c(f"{s.event_at.date().isoformat()}: {s.title}", _sig_epistemic(s.epistemic), sid)
               for s, sid in g.signals if s.event_at >= since30][:8]
    top = sorted(g.signals, key=lambda x: (x[0].normalized_value or 0.0, x[0].event_at), reverse=True)[:10]
    signals = [_c(f"{s.title} ({_FAMILY_LABEL.get(s.source_type, s.source_type)}, {s.event_at.date().isoformat()})",
                  _sig_epistemic(s.epistemic), sid) for s, sid in top]

    why_now: list[Claim] = []
    wn = why_now_template(session, e)
    if wn["citations"]:
        sid_for = {}
        for sig_id in wn["citations"]:
            sid = reg.id_for_key(f"signal:{sig_id}")
            if sid is None:
                match = next((s for s, _ in g.signals if str(s.id) == sig_id), None)
                sid = tools.signal_source(match).id if match is not None else None
            sid_for[sig_id] = sid
        why_now.append(_c(wn["text"], "INFERENCE", *sid_for.values()))

    edge: list[Claim] = []
    growing = sorted((w for w in g.metrics if (w.pct_30d or 0) >= 10), key=lambda w: w.pct_30d or 0, reverse=True)
    edge += [_c(w.line() + ".", "FACT", g.metrics_sid) for w in growing[:3]]
    comps = (g.score.components or {}) if g.score else {}
    tm = comps.get("technical_momentum")
    if isinstance(tm, dict) and tm.get("value") is not None:
        edge.append(_c(f"Technical momentum component scored {tm['value']}/{tm.get('max')} on "
                       f"{g.score.day.isoformat()}.", "INFERENCE", g.metrics_sid))
    for p, sid in g.papers[:3]:
        when = p.published_at.date().isoformat() if p.published_at else "date unknown"
        edge.append(_c(f"Linked paper: \"{p.title}\" (arXiv {p.arxiv_id}, {when}).", "FACT", sid))

    matter: list[Claim] = [_c(why_anti_template(session, e, get_thesis())["text"], "INFERENCE", g.profile_sid)]
    att = (g.score.attention or {}) if g.score else {}
    if att.get("flagged"):
        matter.append(_c(f"Technical activity percentile {att.get('technical')} vs public attention percentile "
                         f"{att.get('attention')}: attention gap flagged.", "INFERENCE", g.metrics_sid))

    wrong: list[Claim] = []
    fams = {s.source_type for s, _ in g.signals}
    if len(fams) == 1:
        fam = next(iter(fams))
        wrong.append(_c(f"All recent signals come from a single source family ({_FAMILY_LABEL.get(fam, fam)}); "
                        "single-source activity can reflect one launch moment rather than durable momentum.",
                        "INFERENCE", *[sid for _, sid in g.signals[:3]]))
    elif not fams:
        wrong.append(_c("No signals were recorded in the last 90 days.", "INFERENCE"))
    if isinstance(e, Company) and e.kind in ("project", "lab"):
        wrong.append(_c(f"{e.name} is classified as a {e.kind}, not yet a company.", "FACT", g.profile_sid))
    if att.get("coverage") == "low":
        wrong.append(_c("Public-attention coverage is low, so the attention gap may reflect missing data rather "
                        "than overlooked quality.", "INFERENCE", g.metrics_sid))
    wrong.append(_c("No customer, revenue or funding evidence was collected; traction is inferred from technical "
                    "activity only.", "INFERENCE"))

    contra: list[Claim] = [_c(w.line() + ".", "FACT", g.metrics_sid)
                           for w in g.metrics if (w.delta_30d or 0) < 0][:4]
    stale_cutoff = clock.now() - timedelta(days=90)
    for r, sid in g.repos:
        if r.archived:
            contra.append(_c(f"Repository {r.full_name} is archived.", "FACT", sid))
        elif r.pushed_at and r.pushed_at < stale_cutoff:
            contra.append(_c(f"Repository {r.full_name} has had no push since {r.pushed_at.date().isoformat()}.",
                             "FACT", sid))

    brief = AntiBrief(
        subject=e.name, date=today.isoformat(), what_they_do=what, why_now=why_now, what_changed=changed,
        founders=founders, technical_edge=edge, market=[], competitors=[], why_this_could_matter=matter,
        why_this_could_be_wrong=wrong, contradictory_signals=contra,
        questions_to_ask_founders=template_questions(session, e), signals=signals, unknowns=[],
    )
    labels = {"what_they_do": "What they do", "why_now": "Why now", "what_changed": "Recent changes",
              "founders": "Founders", "technical_edge": "Technical edge", "market": "Market",
              "competitors": "Competitors", "contradictory_signals": "Contradictory signals",
              "signals": "Signals", "why_this_could_matter": "Fit", "why_this_could_be_wrong": "Bear case"}
    brief.unknowns = [f"{labels[k]}: Unknown — no evidence collected." for k in BRIEF_CLAIM_SECTIONS
                      if not getattr(brief, k)]
    brief.unknowns += ["Funding history and investors: not collected.", "Customer traction and revenue: not collected."]
    return brief


# ── LLM path ─────────────────────────────────────────────────────────────────


def _subject_line(g: Gathered) -> str:
    return f"Subject: {g.entity_type} \"{g.entity.name}\" (id {g.entity.id}). Today: {clock.today().isoformat()}."


def _llm_brief(
    provider: BaseProvider, g: Gathered, tools: AnalystTools, emit: _Events
) -> tuple[AntiBrief, str, float, dict[str, Any]]:
    """Tool loop + structured brief. Returns (brief, model, cost, validation)."""
    budget = get_settings().llm_max_usd_per_investigation

    def execute(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        emit({"type": "tool_call", "tool": name, "input": args})
        content, is_error = tools.execute(name, args)
        if is_error:
            summary = content[:200]
        else:
            ids = tools.last_ids
            summary = f"{len(ids)} source(s): {', '.join(ids)}" if ids else "no results"
            flagged = [i for i in ids if (s := tools.registry.get(i)) and s.suspicious]
            if flagged:
                summary += f" — possible prompt injection detected in {', '.join(flagged)} (treated as data)"
        emit({"type": "tool_result", "tool": name, "summary": summary, "is_error": is_error})
        return content, is_error

    emit({"type": "step", "label": "Investigating with tools"})
    user = (f"{_subject_line(g)}\n\nPre-gathered evidence ({len(tools.registry.ids())} sources):\n\n"
            f"{tools.registry.blocks(max_total_chars=60_000)}\n\n"
            "Investigate the subject following the research steps, then write your research notes.")
    loop = provider.tool_loop(
        purpose="analyst.investigate", tier="deep", system=SYSTEM_GUARD + "\n\n" + load_prompt("analyst"),
        user=user, tools=tools.specs(), execute=execute, max_turns=MAX_TURNS, budget_usd=budget * LOOP_BUDGET_SHARE,
    )
    emit({"type": "step", "label": f"Research loop finished ({loop.turns} turns, {loop.tool_calls} tool calls, "
                                   f"stop: {loop.stop_reason}, ${loop.cost_usd:.3f})"})
    if loop.cost_usd >= budget:
        raise LLMError(f"investigation budget exhausted (${loop.cost_usd:.2f} of ${budget:.2f})")

    emit({"type": "step", "label": "Writing structured Anti Brief"})
    final_user = (f"{_subject_line(g)}\n\nSources:\n\n{tools.registry.blocks(max_total_chars=120_000)}\n\n"
                  f"<analyst_notes>\n{escape_content(loop.final_text or '(no notes)')}\n</analyst_notes>")
    res = provider.structured(
        "analyst.brief", "deep", SYSTEM_GUARD + "\n\n" + load_prompt("brief"), final_user, AntiBrief,
        cache=False, max_tokens=16000,
    )
    brief = res.parsed
    brief.subject = g.entity.name
    brief.date = clock.today().isoformat()
    report = validate_citations(brief, tools.registry.ids())
    emit({"type": "step", "label": f"Validated citations: {report.total_claims} claims, "
                                   f"{len(report.invalid_ids)} invalid ids removed, {report.downgraded} downgraded"})
    return brief, res.model, round(loop.cost_usd + res.cost_usd, 6), report.as_dict()


# ── entry point ──────────────────────────────────────────────────────────────


def investigate(
    session: Session,
    entity_type: str,
    entity_id: str | uuid.UUID,
    *,
    live_fetch: bool = True,
    on_event: EventSink | None = None,
    provider: BaseProvider | None = None,
) -> ResearchReport:
    """Research one company/person and persist a ``ResearchReport`` (kind 'brief')."""
    emit = _Events(on_event)
    tools: AnalystTools | None = None
    try:
        entity = _load_entity(session, entity_type, entity_id)
        registry = SourceRegistry(session, is_demo=entity.is_demo)
        tools = AnalystTools(session, registry, live_fetch=live_fetch)
        g = gather(session, tools, entity, emit)
        provider = provider or get_llm()

        brief: AntiBrief | None = None
        model: str | None = None
        cost = 0.0
        generator = "template"
        notes: list[str] = []
        validation: dict[str, Any] | None = None
        if provider.available:
            try:
                brief, model, cost, validation = _llm_brief(provider, g, tools, emit)
                generator = "llm"
            except LLMError as e:
                log.warning("analyst LLM path failed, using template: %s", e)
                emit({"type": "step", "label": f"LLM unavailable ({type(e).__name__}); using deterministic template"})
                notes.append("The model could not complete this brief; this is a deterministic template from database "
                             "facts only.")
        else:
            emit({"type": "step", "label": "No LLM configured; building deterministic template brief"})
        if brief is None:
            brief = template_brief(session, g, tools)
            validation = validate_citations(brief, registry.ids()).as_dict()
        flags = registry.flags()
        if flags:
            emit({"type": "step", "label": f"Prompt-injection patterns found in {len(flags)} source(s); "
                                           "content was treated as data only", "flags": flags})

        sources = registry.refs()
        report = ResearchReport(
            kind="brief", entity_type=g.entity_type, entity_id=entity.id, title=f"Anti Brief — {entity.name}",
            body=brief.model_dump(mode="json"),
            markdown=brief_to_markdown(brief, sources, generator=generator, model=model, notes=notes),
            sources=sources, model=model, generator=generator, status="complete",
            trace=[], cost_usd=cost, is_demo=entity.is_demo,
        )
        session.add(report)
        session.flush()
        emit({"type": "done", "report_id": str(report.id)})
        report.trace = [*emit.trace, {"type": "validation", **(validation or {})}]
        session.flush()
        return report
    except Exception as e:
        emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        raise
    finally:
        if tools is not None:
            tools.close()
