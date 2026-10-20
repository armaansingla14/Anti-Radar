"""Meeting Brief: a one-page pre-meeting brief on one person (``ResearchReport.kind == 'meeting_brief'``).

1. Gather the person's *own* evidence deterministically into a :class:`SourceRegistry`
   (profile + field provenance, their repositories, signals on/linked to them and the signals
   their Founder Score cites, papers, organization links, their formation candidate).
2. Ask the deep-tier model for a :class:`MeetingBrief` (structured output) when a provider is
   available; otherwise — or on any LLM error — build a deterministic TEMPLATE brief where every
   claim cites a registered source and the five questions are templated from evidence.
3. :func:`validate_citations` strips any citation outside this person's registry (and downgrades
   an uncited FACT), so a brief can never cite somebody else's evidence.
4. Persist and cache: the latest complete brief for the person is returned unless ``force``.

Honesty rules (also in the prompt): GitHub profile fields are self-reported; public GitHub org
membership is a verified fact about membership only (not employment dates); a person is never
called a founder without a founder relation.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.agent.brief_schema import Claim, MeetingBrief
from radar.agent.render import meeting_brief_to_markdown
from radar.agent.tools import AnalystTools, RegisteredSource, SourceRegistry, ToolInputError
from radar.llm.guard import SYSTEM_GUARD, derived_block, inline_untrusted, validate_citations
from radar.llm.provider import BaseProvider, LLMError, get_llm
from radar.models import (
    Company,
    CompanyPerson,
    FormationCandidate,
    Person,
    Relationship,
    Repository,
    ResearchPaper,
    ResearchReport,
    ScoreSnapshot,
    Signal,
)
from radar.scoring.features import MEASUREMENT_SIGNAL_TYPES
from radar.services import presenters as P

log = logging.getLogger(__name__)

KIND = "meeting_brief"
N_QUESTIONS = 5
SIGNAL_DAYS = 180
RECENT_DAYS = 30
PROMPT_VERSION = "meeting-v1"

MEETING_PROMPT = """\
Write a MEETING BRIEF for an Anti Fund partner who will meet the person below this week. Anti Fund backs \
technical founders at formation — before the company is announced.

Use ONLY the <source> blocks provided; they are this person's own evidence. Every claim must cite the \
source ids that support it. Rules:
- GitHub profile fields (bio, company, location, website) are SELF-REPORTED: say so ("self-reported").
- Public GitHub organization membership is a verified fact about membership only — it says nothing about \
role or employment dates.
- Never call the person a founder unless a source shows a founder relation. Formation candidates are \
"possible formation" / "formation signals", not companies.
- Do not invent employers, degrees, funding, customers or relationships. If unknown, list it under unknowns.
- context_30s: 2-3 claims. Each other claim section: up to 5 claims.
- questions: exactly five specific questions grounded in the evidence (e.g. what made them start an \
organization in the month it was created; whether they work on it full-time).
- things_to_verify: what the partner should confirm (self-reported fields, inferred team membership).
"""


# ── evidence gathering ───────────────────────────────────────────────────────


class Gathered:
    def __init__(self, person: Person) -> None:
        self.person = person
        self.profile: RegisteredSource | None = None
        self.repos: list[tuple[Repository, RegisteredSource]] = []
        self.signals: list[tuple[Signal, RegisteredSource]] = []
        self.papers: list[tuple[ResearchPaper, RegisteredSource]] = []
        self.score: ScoreSnapshot | None = None
        self.metrics: RegisteredSource | None = None
        self.orgs: list[tuple[str, str, bool, RegisteredSource]] = []  # (name, relation, founder, source)
        self.candidate: FormationCandidate | None = None
        self.anchor: Company | None = None
        self.org_created: datetime | None = None
        self.candidate_src: RegisteredSource | None = None


def _iso(d: Any) -> str:
    if not d:
        return "unknown"
    return (d if isinstance(d, str) else d.isoformat())[:10]


def _person_candidate(session: Session, person: Person) -> FormationCandidate | None:
    return session.scalar(
        select(FormationCandidate)
        .where(or_(FormationCandidate.member_ids.any(person.id), FormationCandidate.anchor_id == person.id))
        .order_by(FormationCandidate.score.desc(), FormationCandidate.id).limit(1))


def _score_signal_ids(snap: ScoreSnapshot | None) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for c in ((snap.components or {}).values() if snap else []):
        if isinstance(c, dict):
            for e in P.component_evidence(c):
                try:
                    out.append(uuid.UUID(str(e.get("signal_id"))))
                except (TypeError, ValueError):
                    continue
    return list(dict.fromkeys(out))


def gather(session: Session, person: Person, registry: SourceRegistry) -> Gathered:
    """Register only this person's own evidence (deterministic order → stable S-ids)."""
    g = Gathered(person)
    tools = AnalystTools(session, registry, live_fetch=False)
    try:
        g.profile, _prov, g.repos = tools.profile(person)
        _windows, g.score, g.metrics = tools.metrics(person)
        seen: set[uuid.UUID] = set()
        for s, src in tools.signals(person, days=SIGNAL_DAYS, limit=30):
            seen.add(s.id)
            g.signals.append((s, src))
        cited = [i for i in _score_signal_ids(g.score) if i not in seen]
        if cited:
            for s in session.scalars(select(Signal).where(Signal.id.in_(cited)).order_by(Signal.event_at.desc())):
                g.signals.append((s, tools.signal_source(s)))
        g.signals.sort(key=lambda t: (t[0].event_at, str(t[0].id)), reverse=True)
        g.papers = tools.papers(person, limit=8)
    finally:
        tools.close()

    # Organization links: company_people / FOUNDED / MEMBER_OF relations, and public GitHub org memberships.
    rels = session.execute(
        select(Relationship, Company).join(Company, Company.id == Relationship.dst_id)
        .where(Relationship.src_id == person.id, Relationship.kind.in_(("FOUNDED", "MEMBER_OF")))
        .order_by(Company.name)).all()
    links = {cp.company_id: cp for cp in session.scalars(select(CompanyPerson).where(CompanyPerson.person_id == person.id))}
    done: set[uuid.UUID] = set()
    for rel, c in rels:
        cp = links.get(c.id)
        founder = rel.kind == "FOUNDED" or bool(cp and cp.is_founder)
        lines = [f"Organization: {c.name}", f"Relation: {rel.kind} ({rel.epistemic})",
                 f"Role: {(cp.role if cp else None) or 'unknown'} | Founder relation: {founder}",
                 f"Evidence dates: {_iso(rel.first_seen_at)} – {_iso(rel.last_seen_at)}",
                 *[f"Evidence: {e.get('title')} — {e.get('detail') or ''}" for e in (rel.evidence or [])[:3]]]
        src = registry.register(url=(cp.source_url if cp and cp.source_url else f"radar://company/{c.slug}"),
                                title=f"{person.name} — {rel.kind.replace('_', ' ').lower()} {c.name}",
                                source_type="system", content="\n".join(lines), key=f"org:{person.id}:{c.id}")
        g.orgs.append((c.name, rel.kind, founder, src))
        done.add(c.id)
    for cid, cp in links.items():
        if cid in done:
            continue
        c = session.get(Company, cid)
        if c is None:
            continue
        lines = [f"Organization: {c.name}", f"Role: {cp.role or 'unknown'} | Founder relation: {cp.is_founder}",
                 f"Link confidence: {cp.confidence:.2f}"]
        src = registry.register(url=cp.source_url or f"radar://company/{c.slug}", title=f"{person.name} — {c.name}",
                                source_type="system", content="\n".join(lines), key=f"org:{person.id}:{c.id}")
        g.orgs.append((c.name, "FOUNDED" if cp.is_founder else "MEMBER_OF", bool(cp.is_founder), src))
    gh_orgs = [o for o in ((person.attrs or {}).get("github_orgs") or []) if isinstance(o, str) and o]
    if gh_orgs and person.github_handle:
        src = registry.register(
            url=f"https://github.com/{person.github_handle}", title=f"Public GitHub organizations of {person.name}",
            source_type="github",
            content=("Public GitHub organization memberships returned by the GitHub API (membership only; "
                     "no role or dates): " + ", ".join(gh_orgs[:20])), key=f"ghorgs:{person.id}")
        for o in gh_orgs[:20]:
            g.orgs.append((o, "PUBLIC_GITHUB_ORG", False, src))

    # Formation candidate the person belongs to.
    cand = _person_candidate(session, person)
    if cand is not None:
        g.candidate = cand
        if cand.anchor_type == "company":
            g.anchor = session.get(Company, cand.anchor_id)
            if g.anchor is not None:
                created = [r.gh_created_at for r in session.scalars(
                    select(Repository).where(Repository.company_id == g.anchor.id)) if r.gh_created_at]
                org_sig = session.scalar(select(Signal.event_at).where(
                    Signal.entity_id == g.anchor.id, Signal.signal_type == "org_created").order_by(Signal.event_at))
                g.org_created = org_sig or (min(created) if created else None)
        comps = []
        for key, c in (cand.components or {}).items():
            if isinstance(c, dict):
                comps.append(f"{c.get('label') or key}: {c.get('value')}/{c.get('max')} — {c.get('explanation') or ''}")
        lines = [f"Formation candidate: {cand.title}", f"Label: {cand.label} | Tier: {cand.tier} | "
                 f"Formation Score {cand.score:.0f}/100 (deterministic, computed {_iso(cand.day)})",
                 f"Members tracked: {len(cand.member_ids or [])} | Independent signals: {cand.independent_signals} | "
                 f"Families: {', '.join(cand.signal_families or []) or 'none'}",
                 f"Organization first observed/created: {_iso(g.org_created)}",
                 *comps, *[f"Why now: {e.get('title')} ({_iso(e.get('event_at'))})"
                           for e in (cand.why_now or []) if isinstance(e, dict)]]
        g.candidate_src = registry.register(url=f"radar://formation/{cand.slug}",
                                            title=f"ANTI RADAR formation candidate: {cand.title}",
                                            source_type="demo" if cand.is_demo else "system",
                                            content="\n".join(lines), key=f"formation:{cand.id}")
    return g


# ── deterministic template ───────────────────────────────────────────────────


def _c(text: str, epistemic: str, *srcs: RegisteredSource | None) -> Claim:
    return Claim(text=text, epistemic=epistemic, source_ids=[s.id for s in srcs if s is not None])


def _month(d: datetime) -> str:
    return f"{d:%B %Y}"


def template_questions(g: Gathered) -> list[str]:
    p = g.person
    qs: list[str] = []
    anchor = P.clean_label(g.anchor.name) if g.anchor is not None else None
    founder_of = {o for o, _, founder, _ in g.orgs if founder}
    if anchor and g.org_created and g.anchor is not None and g.anchor.name in founder_of:
        qs.append(f"What made you start {anchor} in {_month(g.org_created)}?")
    elif anchor and g.org_created:
        qs.append(f"The {anchor} GitHub organization appeared in {_month(g.org_created)} — what got it started, "
                  "and what is your part in it?")
    elif g.repos:
        qs.append(f"What problem led you to build {g.repos[0][0].full_name}?")
    qs.append("Are you working on this full-time?")
    if g.candidate is not None and len(g.candidate.member_ids or []) > 1 and anchor:
        qs.append(f"Who else is building {anchor} with you, and how did you start working together?")
    if g.papers:
        qs.append(f"How does your work on “{g.papers[0][0].title}” translate into something people can use?")
    if g.repos and len(qs) < N_QUESTIONS:
        r = g.repos[0][0]
        qs.append(f"Who is using {r.full_name} today, and what are they asking for?")
    if p.current_company and len(qs) < N_QUESTIONS:
        qs.append(f"Your GitHub profile lists {P.clean_label(p.current_company)} — what is your role there today?")
    for generic in ("What would have to be true in the next 12 months for this to become a company?",
                    "What is the hardest technical problem you have not solved yet?",
                    "Who would you want to hire first, and why?"):
        if len(qs) >= N_QUESTIONS:
            break
        qs.append(generic)
    return list(dict.fromkeys(qs))[:N_QUESTIONS]


def template_brief(g: Gathered) -> MeetingBrief:
    p = g.person
    name = P.display_name(p).name
    prof, cand_src, met = g.profile, g.candidate_src, g.metrics
    comps = (g.score.components or {}) if g.score else {}
    sr = (p.attrs or {}).get("self_reported")
    sr_tag = " (self-reported on GitHub)" if sr else ""

    ctx: list[Claim] = []
    head = P.headline(p)
    if p.current_company:
        ctx.append(_c(f"{name} lists {P.clean_label(p.current_company)} as current company{sr_tag}.", "FACT", prof))
    elif head:
        ctx.append(_c(f"{name}: {head}{sr_tag}.", "FACT", prof))
    else:
        ctx.append(_c(f"{name} has no stated affiliation in the collected profile.", "FACT", prof))
    for org, kind, founder, src in g.orgs:
        if founder:
            ctx.append(_c(f"Founder relation recorded with {org}.", "FACT", src))
    if g.candidate is not None:
        ctx.append(_c(f"Part of the formation candidate “{g.candidate.title}” ({g.candidate.label}, "
                      f"Formation Score {g.candidate.score:.0f}).", "INFERENCE", cand_src))
    if g.score is not None:
        ctx.append(_c(f"Founder Score {g.score.score:.0f}/100 as of {g.score.day.isoformat()}.", "INFERENCE", met))

    radar: list[Claim] = []
    for key in ("technical_output", "research", "formation", "pedigree", "attention_gap"):
        c = comps.get(key) or {}
        if (c.get("value") or 0) > 0 and c.get("explanation"):
            radar.append(_c(c["explanation"], "INFERENCE", met))
    if g.candidate is not None:
        for e in (g.candidate.why_now or [])[:2]:
            if isinstance(e, dict) and e.get("title"):
                radar.append(_c(f"Formation evidence: {e['title']}.", "FACT", cand_src))

    built: list[Claim] = []
    for r, src in g.repos[:4]:
        desc = f" — {r.description.strip()[:160]}" if r.description else ""
        built.append(_c(f"{r.full_name}{desc} ({r.stars:,} stars; created {_iso(r.gh_created_at)}).", "FACT", src))
    for paper, src in g.papers[:3]:
        built.append(_c(f"Co-author of “{paper.title}” (arXiv {paper.arxiv_id}, {_iso(paper.published_at)}).",
                        "FACT", src))

    tech: list[Claim] = []
    if p.domains:
        tech.append(_c(f"Domains on radar: {', '.join(p.domains[:4])}.", "FACT", prof))
    prior = [a for a in map(P.clean_label, p.prior_affiliations or []) if a]
    if prior:
        tech.append(_c(f"Prior affiliations listed: {', '.join(prior[:4])}{sr_tag}.", "FACT", prof))
    langs = list(dict.fromkeys(r.language for r, _ in g.repos if r.language))
    if langs:
        tech.append(_c(f"Repository languages: {', '.join(langs[:4])}.", "FACT", *[s for _, s in g.repos[:3]]))
    gh = [o for o, kind, _, _ in g.orgs if kind == "PUBLIC_GITHUB_ORG"]
    if gh:
        src = next(s for o, kind, _, s in g.orgs if kind == "PUBLIC_GITHUB_ORG")
        tech.append(_c(f"Public member of GitHub organizations: {', '.join(gh[:6])} (membership only, no dates).",
                       "FACT", src))
    for org, kind, founder, src in g.orgs:
        if kind == "MEMBER_OF":
            tech.append(_c(f"Recorded as a member of {org} (see the cited source for the basis).", "FACT", src))
    ped = comps.get("pedigree") or {}
    if (ped.get("value") or 0) > 0 and ped.get("explanation"):
        tech.append(_c(ped["explanation"] + (" (based on self-reported affiliations)" if sr else ""), "INFERENCE", met))

    changed: list[Claim] = []
    cutoff = clock.now() - timedelta(days=RECENT_DAYS)
    recent = [(s, src) for s, src in g.signals if s.event_at >= cutoff]
    recent.sort(key=lambda t: (t[0].signal_type in MEASUREMENT_SIGNAL_TYPES, -t[0].event_at.timestamp()))
    for s, src in recent[:5]:
        changed.append(_c(f"{_iso(s.event_at)}: {s.title}.", "FACT", src))
    if g.org_created and g.anchor is not None and g.org_created >= cutoff:
        changed.append(_c(f"{P.clean_label(g.anchor.name)} organization created {_iso(g.org_created)}.", "FACT",
                          cand_src))

    why_anti: list[Claim] = []
    tf = comps.get("thesis_fit") or {}
    if (tf.get("value") or 0) > 0 and tf.get("explanation"):
        why_anti.append(_c(tf["explanation"], "INFERENCE", met))
    if g.candidate is not None:
        why_anti.append(_c("Formation-stage evidence (a young organization and team activity) before any company "
                           "announcement — the stage Anti Fund invests at.", "INFERENCE", cand_src))
    att = (g.score.attention or {}) if g.score else {}
    if att.get("flagged"):
        why_anti.append(_c(f"Attention gap: technical output at the {att.get('technical'):.0f}th percentile vs public "
                           f"attention at the {att.get('attention'):.0f}th.", "INFERENCE", met))

    verify: list[str] = []
    if sr:
        verify.append("Profile fields (company, bio, website, location) are self-reported on GitHub — confirm them.")
    if g.candidate is not None:
        verify.append(f"Team membership in “{g.candidate.title}” is inferred from public GitHub activity "
                      "— confirm who is actually on the team and their roles.")
    if not any(f for *_, f, _ in g.orgs):
        verify.append("No founder relation is recorded — do not assume they founded anything.")
    if g.papers:
        verify.append("Their role on listed papers (lead vs. contributing author).")

    unknowns = ["Whether a company has been incorporated, and its funding status.",
                "Whether they are working on this full-time."]
    if not g.papers:
        unknowns.append("Research record: no papers by this person are on the radar.")
    if not p.prior_affiliations:
        unknowns.append("Employment history: no prior affiliations are recorded.")

    return MeetingBrief(
        name=name, date=clock.today().isoformat(), context_30s=ctx[:3], why_on_radar=radar[:5],
        what_they_built=built[:6], technical_background=tech[:5], what_changed=changed[:5],
        why_anti_fund=why_anti[:4], questions=template_questions(g), things_to_verify=verify, unknowns=unknowns,
    )


# ── orchestration ────────────────────────────────────────────────────────────


def latest_brief(session: Session, person_id: uuid.UUID) -> ResearchReport | None:
    return session.scalar(
        select(ResearchReport).where(ResearchReport.kind == KIND, ResearchReport.entity_id == person_id,
                                     ResearchReport.status == "complete")
        .order_by(ResearchReport.created_at.desc()).limit(1))


def _fit_questions(brief: MeetingBrief, fallback: list[str]) -> None:
    qs = [q.strip() for q in brief.questions if isinstance(q, str) and q.strip()]
    for q in fallback:
        if len(qs) >= N_QUESTIONS:
            break
        if q not in qs:
            qs.append(q)
    brief.questions = qs[:N_QUESTIONS]


def generate_meeting_brief(session: Session, person_id: str | uuid.UUID, *, force: bool = False,
                           provider: BaseProvider | None = None) -> ResearchReport:
    """Return the cached brief for the person (unless ``force``) or build, validate and persist a new one."""
    try:
        person = session.get(Person, uuid.UUID(str(person_id)))
    except ValueError:
        person = None
    if person is None:
        raise ToolInputError(f"no person found for {person_id!r}")
    if not force and (cached := latest_brief(session, person.id)) is not None:
        return cached

    registry = SourceRegistry(session, is_demo=person.is_demo)
    g = gather(session, person, registry)
    valid = registry.ids()
    fallback_qs = template_questions(g)
    provider = provider or get_llm()

    brief: MeetingBrief | None = None
    generator, model, cost = "template", None, 0.0
    note: str | None = None
    try:
        name = inline_untrusted(P.display_name(person).name)
        draft = json.dumps(template_brief(g).model_dump(mode="json"), ensure_ascii=False)[:20000]
        # The draft quotes external text (titles, names), so it is escaped and fenced like source content.
        user = (f"Person: \"{name}\" (date {clock.today().isoformat()}).\n\nSources:\n\n{registry.blocks()}\n\n"
                f"Deterministic template draft for reference (JSON; you may improve it but keep citations valid):\n"
                f"{derived_block(draft, 'template draft')}")
        res = provider.structured("analyst.meeting_brief", "deep", SYSTEM_GUARD + "\n\n" + MEETING_PROMPT, user,
                                  MeetingBrief, cache=True, prompt_version=PROMPT_VERSION, max_tokens=6000)
        brief = res.parsed.model_copy(deep=True)
        generator = "cached" if res.cached else "llm"
        model, cost = res.model, res.cost_usd
    except LLMError as e:
        note = f"{type(e).__name__}: {e}"[:300]
        log.info("meeting brief falling back to template: %s", note)
    if brief is None:
        brief = template_brief(g)
    validation = validate_citations(brief, valid).as_dict()
    brief.name = P.display_name(person).name
    brief.date = clock.today().isoformat()
    _fit_questions(brief, fallback_qs)

    sources = registry.refs()
    report = ResearchReport(
        kind=KIND, entity_type="person", entity_id=person.id, title=f"Meeting Brief — {brief.name}",
        body=brief.model_dump(mode="json"),
        markdown=meeting_brief_to_markdown(brief, sources, generator=generator, model=model), sources=sources,
        model=model, generator=generator, status="complete", cost_usd=cost, is_demo=person.is_demo,
        trace=[{"type": "validation", **validation}, {"type": "injection_flags", "flags": registry.flags()},
               *([{"type": "fallback", "reason": note}] if note else []),
               {"type": "gathered", "sources": len(sources), "signals": len(g.signals), "repos": len(g.repos),
                "papers": len(g.papers), "formation_candidate": str(g.candidate.id) if g.candidate else None}],
    )
    session.add(report)
    session.flush()
    return report
