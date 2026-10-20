"""Grounded narrative blocks for entity pages and the morning brief.

Every helper returns a dict compatible with ``schemas.api.Narrative``
(``text, epistemic, generator, model, citations``). The LLM path (standard tier,
low effort, cached) only sees signals wrapped as ``<source>`` blocks with short
ids (G1, G2, …) that are mapped back to signal UUIDs; citations to anything else
are dropped. If the model is unavailable, refuses, or returns an ungrounded
answer, a deterministic template built from the same facts is returned instead
(``generator='template'``, ``epistemic='INFERENCE'``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.llm.guard import SYSTEM_GUARD, wrap_source
from radar.llm.prompts import load_prompt
from radar.llm.provider import BaseProvider, LLMError, LLMResult, T, get_llm
from radar.models import (
    Company,
    CompanyPerson,
    EntityTopic,
    Person,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
    SignalEntity,
    Topic,
)
from radar.thesis.loader import Thesis, get_thesis

log = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
_STYLE = (
    "\n\nOutput style: `text` is plain prose for an investor. Do not put source ids, brackets or epistemic "
    "labels (FACT/INFERENCE/MODEL_OPINION) in `text`; list supporting ids only in the citation field when the "
    "schema has one. The whole block is displayed as model opinion."
)
WHY_NOW_DAYS = 45
Entity = Company | Person

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight",
                 9: "Nine", 10: "Ten"}
_FAMILY_LABEL = {"github": "GitHub", "arxiv": "arXiv", "hackernews": "Hacker News", "jobs": "hiring",
                 "website": "website", "x": "X", "manual": "manual note", "demo": "demo data", "system": "radar"}

RiskCategory = Literal[
    "defensibility", "company_vs_project", "founder_commitment", "customer_pain", "incumbents",
    "technical", "market", "team", "other",
]
REQUIRED_RISK_CATEGORIES: tuple[str, ...] = (
    "defensibility", "company_vs_project", "founder_commitment", "customer_pain", "incumbents",
)


def narrative(
    text: str, *, epistemic: str = "INFERENCE", generator: str = "template", model: str | None = None,
    citations: Sequence[str] = (),
) -> dict[str, Any]:
    return {"text": text, "epistemic": epistemic, "generator": generator, "model": model,
            "citations": list(dict.fromkeys(citations))}


# ── LLM output schemas ───────────────────────────────────────────────────────


class _NarrOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    citations: list[str]


class _RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    category: RiskCategory
    citations: list[str]


class _RiskOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_RiskItem]


class _TextOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class _MorningOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    item_numbers: list[int]


# ── shared helpers ───────────────────────────────────────────────────────────


def _llm(
    provider: BaseProvider | None, purpose: str, tier: Literal["fast", "standard"], prompt: str, user: str,
    schema: type[T], max_tokens: int = 2000,
) -> LLMResult[T] | None:
    p = provider or get_llm()
    try:
        return p.structured(purpose, tier, SYSTEM_GUARD + "\n\n" + load_prompt(prompt) + _STYLE, user, schema,
                            cache=True, prompt_version=PROMPT_VERSION, max_tokens=max_tokens)
    except LLMError as e:
        log.info("narrative %s falling back to template: %s", purpose, e)
        return None


def _gen(res: LLMResult[Any]) -> str:
    return "cached" if res.cached else "llm"


def related_entity_ids(session: Session, entity: Entity) -> list[Any]:
    """The entity plus its repositories (signals and metrics are often recorded on repos)."""
    ids = [entity.id]
    if isinstance(entity, Company):
        ids += list(session.scalars(select(Repository.id).where(Repository.company_id == entity.id)))
    return ids


def recent_signals(session: Session, entity: Entity, days: int = WHY_NOW_DAYS, limit: int = 25) -> list[Signal]:
    """Signals on the entity, its repositories, or linked via SignalEntity, newest first."""
    ids = related_entity_ids(session, entity)
    since = clock.now() - timedelta(days=days)
    linked = select(SignalEntity.signal_id).where(SignalEntity.entity_id.in_(ids))
    return list(session.scalars(
        select(Signal)
        .where(or_(Signal.entity_id.in_(ids), Signal.id.in_(linked)), Signal.event_at >= since,
               Signal.event_at <= clock.now() + timedelta(days=1))
        .order_by(Signal.event_at.desc()).limit(limit)
    ))


def _latest_score(session: Session, entity: Entity) -> ScoreSnapshot | None:
    return session.scalar(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == entity.id)
                          .order_by(ScoreSnapshot.day.desc()).limit(1))


@dataclass
class _SignalBlocks:
    text: str
    gid_to_signal: dict[str, str]


def _signal_blocks(signals: Sequence[Signal]) -> _SignalBlocks:
    blocks, mapping = [], {}
    for i, s in enumerate(signals, 1):
        gid = f"G{i}"
        mapping[gid] = str(s.id)
        body = f"{s.title}\nType: {s.signal_type} | Date: {s.event_at.date().isoformat()}"
        if s.description:
            body += f"\n{s.description[:400]}"
        blocks.append(wrap_source(gid, s.source_url, s.source_type, body, 800))
    return _SignalBlocks("\n".join(blocks) or "(no signals)", mapping)


def _map_citations(raw: Sequence[str], mapping: dict[str, str]) -> list[str]:
    out = []
    for c in raw:
        key = c.strip().strip("[]").upper()
        if key in mapping:
            out.append(mapping[key])
    return list(dict.fromkeys(out))


def _profile_block(entity: Entity) -> str:
    if isinstance(entity, Company):
        body = (f"Company: {entity.name}\nKind: {entity.kind} | Stage estimate: {entity.stage}\n"
                f"Description: {entity.description or 'Unknown'}\nThesis tags: {', '.join(entity.thesis_tags or [])}")
    else:
        body = (f"Person: {entity.name}\nBio: {entity.bio or 'Unknown'}\n"
                f"Current: {entity.current_role or 'Unknown'} at {entity.current_company or 'Unknown'}\n"
                f"Prior affiliations: {', '.join(entity.prior_affiliations or []) or 'Unknown'}")
    return wrap_source("PROFILE", None, "system", body, 2000)


def _short_date(d: Any) -> str:
    return f"{d:%b} {d.day}"


def _family(s: Signal) -> str:
    return s.source_type or "system"


# ── why now ──────────────────────────────────────────────────────────────────


def why_now_template(session: Session, entity: Entity, signals: Sequence[Signal] | None = None) -> dict[str, Any]:
    """Deterministic why-now from the collision evidence (or recent signals)."""
    signals = list(signals if signals is not None else recent_signals(session, entity))
    if not signals:
        return narrative(f"No new signals recorded for {entity.name} in the past {WHY_NOW_DAYS} days.")
    score = _latest_score(session, entity)
    collision_ids = {str(e.get("signal_id")) for e in ((score.collision or {}).get("evidence") or [])
                     if isinstance(e, dict) and e.get("signal_id")} if score else set()
    ordered = sorted(signals, key=lambda s: (str(s.id) not in collision_ids, -s.event_at.timestamp()))
    reps: dict[str, Signal] = {}
    for s in ordered:
        reps.setdefault(_family(s), s)
    chosen = sorted(list(reps.values())[:4], key=lambda s: s.event_at)
    today = clock.today()
    span = max((today - min(s.event_at.date() for s in chosen)).days, 1)
    parts = [f"{s.title.rstrip('.')} ({_FAMILY_LABEL.get(_family(s), _family(s))}, {_short_date(s.event_at)})"
             for s in chosen]
    if len(reps) >= 2:
        n = len(chosen)
        joined = "; ".join(parts[:-1]) + f"; and {parts[-1]}" if n > 2 else " and ".join(parts)
        text = f"{_NUMBER_WORDS.get(n, str(n))} independent signals appeared within the past {span} days: {joined}."
    else:
        fam = _FAMILY_LABEL.get(_family(chosen[0]), _family(chosen[0]))
        recent = [s for s in signals if _family(s) == _family(chosen[0])][:3]
        chosen = recent
        span = max((today - min(s.event_at.date() for s in recent)).days, 1)
        listed = "; ".join(f"{s.title.rstrip('.')} ({_short_date(s.event_at)})" for s in recent)
        text = (f"{len(signals)} signal{'s' if len(signals) != 1 else ''} in the past {span} days, all from {fam}: "
                f"{listed}. Single-source activity is weaker evidence than independent signals.")
    return narrative(text, citations=[str(s.id) for s in chosen])


def why_now(session: Session, entity: Entity, *, provider: BaseProvider | None = None) -> dict[str, Any]:
    signals = recent_signals(session, entity)
    if not signals:
        return why_now_template(session, entity, signals)
    blocks = _signal_blocks(signals)
    user = (f"Today: {clock.today().isoformat()}\nEntity facts:\n{_profile_block(entity)}\n\n"
            f"Signals from the last {WHY_NOW_DAYS} days:\n{blocks.text}")
    res = _llm(provider, "narrative.why_now", "standard", "why_now", user, _NarrOut, 1200)
    if res is not None:
        cites = _map_citations(res.parsed.citations, blocks.gid_to_signal)
        if cites and res.parsed.text.strip():
            return narrative(res.parsed.text.strip(), epistemic="MODEL_OPINION", generator=_gen(res),
                             model=res.model, citations=cites)
        log.info("why_now LLM output had no valid citations; using template")
    return why_now_template(session, entity, signals)


# ── why Anti ─────────────────────────────────────────────────────────────────


def _topic_labels(session: Session, entity: Entity, thesis: Thesis) -> list[str]:
    keys = list(entity.thesis_tags or []) if isinstance(entity, Company) else []
    keys += list(session.scalars(
        select(Topic.key).join(EntityTopic, EntityTopic.topic_id == Topic.id)
        .where(EntityTopic.entity_id == entity.id).order_by(EntityTopic.score.desc())
    ))
    labels = []
    for k in dict.fromkeys(keys):
        theme_key, _, sub_key = k.partition(".")
        theme = thesis.themes.get(theme_key)
        if theme is None:
            continue
        sub = theme.subcategories.get(sub_key)
        labels.append(f"{theme.label} → {sub.label}" if sub else theme.label)
    return labels[:4]


def _founder_affiliations(session: Session, entity: Entity, thesis: Thesis) -> list[tuple[str, str]]:
    """[(person name, matched tier-1/2 organisation)]."""
    if isinstance(entity, Company):
        people = list(session.scalars(
            select(Person).join(CompanyPerson, CompanyPerson.person_id == Person.id)
            .where(CompanyPerson.company_id == entity.id, CompanyPerson.is_founder.is_(True))
        ))
    else:
        people = [entity]
    out = []
    for p in people:
        for org in p.prior_affiliations or []:
            m = thesis.matched_affiliation(org)
            if m:
                out.append((p.name, m))
                break
    return out


def why_anti_template(session: Session, entity: Entity, thesis: Thesis) -> dict[str, Any]:
    labels = _topic_labels(session, entity, thesis)
    parts = [f"Fits Anti Fund thesis areas: {', '.join(labels)}." if labels
             else "No Anti Fund thesis area has been matched yet, so fit is unclear."]
    if isinstance(entity, Company):
        mult = thesis.stage_multiplier(entity.stage)
        stage = entity.stage.replace("_", "-")
        parts.append(f"Stage estimate: {stage} (fund stage preference {mult:.2f})."
                     if entity.stage != "unknown" else "Stage is unknown.")
    affs = _founder_affiliations(session, entity, thesis)
    if affs:
        parts.append("Technical background: " + "; ".join(f"{n} (ex-{o})" for n, o in affs[:3]) + ".")
    return narrative(" ".join(parts))


def why_anti(session: Session, entity: Entity, thesis: Thesis | None = None, *,
             provider: BaseProvider | None = None) -> dict[str, Any]:
    thesis = thesis or get_thesis()
    template = why_anti_template(session, entity, thesis)
    signals = recent_signals(session, entity, days=90, limit=15)
    blocks = _signal_blocks(signals)
    themes = "; ".join(f"{t.label} (weight {t.weight})" for t in thesis.themes.values())
    user = (f"Fund: {thesis.fund} — {thesis.tagline}\nThesis themes: {themes}\n"
            f"Deterministic thesis reading: {template['text']}\n\nEntity facts:\n{_profile_block(entity)}\n\n"
            f"Signals:\n{blocks.text}")
    res = _llm(provider, "narrative.why_anti", "standard", "why_anti", user, _NarrOut, 1200)
    if res is None or not res.parsed.text.strip():
        return template
    return narrative(res.parsed.text.strip(), epistemic="MODEL_OPINION", generator=_gen(res), model=res.model,
                     citations=_map_citations(res.parsed.citations, blocks.gid_to_signal))


# ── risks & questions ────────────────────────────────────────────────────────


def _template_risks(session: Session, entity: Entity, signals: Sequence[Signal]) -> list[tuple[str, str]]:
    """[(category, question)] — skeptical by construction."""
    name = entity.name
    thesis = get_thesis()
    out: list[tuple[str, str]] = [
        ("defensibility", (f"What stops a well-funded team from reproducing the core technology of {name} within "
                          "12 months — proprietary data, hardware, know-how, or only speed?")),
    ]
    if isinstance(entity, Company):
        projectish = entity.kind in ("project", "lab") or not entity.website
        out.append(("company_vs_project",
                    f"{name} currently looks like a research or open-source project: is there a team and intent "
                    "to build a company around it?" if projectish else
                    f"Is {name} building a product company, or is this still primarily a research effort?"))
        founders = list(session.scalars(
            select(Person).join(CompanyPerson, CompanyPerson.person_id == Person.id)
            .where(CompanyPerson.company_id == entity.id, CompanyPerson.is_founder.is_(True))
        ))
        elsewhere = [p for p in founders if p.current_company and entity.name.lower() not in p.current_company.lower()]
        out.append(("founder_commitment",
                    f"Are the founders full-time on {name}? {elsewhere[0].name} still lists "
                    f"{elsewhere[0].current_company} as their current employer." if elsewhere else
                    f"Are the founders full-time on {name}, and who has committed to join them?"))
        tags = entity.thesis_tags or []
    else:
        out.append(("company_vs_project", (f"Is {name} building toward a company, or is the work an academic or "
                                          "side project?")))
        out.append(("founder_commitment", f"Has {name} actually left their current role"
                    + (f" at {entity.current_company}" if entity.current_company else "")
                    + ", and would they commit full-time?"))
        tags = []
    out.append(("customer_pain", ("Which customer has this pain today, what do they use instead, and what would "
                                 "make them switch and pay?")))
    area = "this area"
    if tags:
        theme = thesis.themes.get(tags[0].partition(".")[0])
        area = theme.label if theme else area
    out.append(("incumbents", (f"How would incumbents in {area} respond if this works — copy, partner, or acquire — "
                              "and why would they not simply build it?")))
    fams = {_family(s) for s in signals}
    if signals and fams == {"github"}:
        out.append(("technical", ("Is the GitHub traction driven by a single launch moment, and is usage sticky "
                                 "(issues, outside contributors, forks) rather than stars alone?")))
    if signals and "jobs" not in fams and isinstance(entity, Company):
        out.append(("team", "No hiring activity has been observed — is the team funded and growing?"))
    return out


def template_questions(session: Session, entity: Entity) -> list[str]:
    return [q for _, q in _template_risks(session, entity, recent_signals(session, entity, days=90))]


def risks_questions(session: Session, entity: Entity, *, provider: BaseProvider | None = None) -> list[dict[str, Any]]:
    signals = recent_signals(session, entity, days=90, limit=20)
    template = _template_risks(session, entity, signals)
    blocks = _signal_blocks(signals)
    user = f"Entity facts:\n{_profile_block(entity)}\n\nSignals:\n{blocks.text}"
    res = _llm(provider, "narrative.risks", "standard", "risks", user, _RiskOut, 2000)
    if res is None or not res.parsed.items:
        return [narrative(q) for _, q in template]
    items = [narrative(i.text.strip(), epistemic="MODEL_OPINION", generator=_gen(res), model=res.model,
                       citations=_map_citations(i.citations, blocks.gid_to_signal))
             for i in res.parsed.items if i.text.strip()]
    covered = {i.category for i in res.parsed.items}
    # Guarantee the skeptical baseline even if the model skipped a category.
    items += [narrative(q) for cat, q in template if cat in REQUIRED_RISK_CATEGORIES and cat not in covered]
    return items


# ── papers ───────────────────────────────────────────────────────────────────


def _thesis_matches(text: str, thesis: Thesis) -> list[tuple[str, str]]:
    """[(subcategory label, keyword)] for thesis keywords present as whole phrases."""
    low = text.lower()
    out = []
    for theme in thesis.themes.values():
        for sub in theme.subcategories.values():
            for kw in sub.keywords:
                if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", low):
                    out.append((f"{theme.label} → {sub.label}", kw))
                    break
    return out


def paper_significance_template(paper: ResearchPaper) -> dict[str, Any]:
    matches = _thesis_matches(f"{paper.title}\n{paper.abstract or ''}", get_thesis())
    parts = []
    if matches:
        areas = ", ".join(dict.fromkeys(label for label, _ in matches[:3]))
        kws = ", ".join(dict.fromkeys(kw for _, kw in matches[:4]))
        parts.append(f"Relevant to Anti Fund thesis areas {areas} (keywords: {kws}).")
    else:
        parts.append("No Anti Fund thesis keywords matched in the title or abstract.")
    if paper.code_urls:
        parts.append(f"Code released: {paper.code_urls[0]}.")
    if paper.published_at:
        cats = ", ".join(paper.categories or []) or "arXiv"
        parts.append(f"Published {paper.published_at.date().isoformat()} in {cats}.")
    return narrative(" ".join(parts))


def paper_significance(paper: ResearchPaper, *, provider: BaseProvider | None = None) -> dict[str, Any]:
    body = (f"Title: {paper.title}\nCategories: {', '.join(paper.categories or [])}\n"
            f"Published: {paper.published_at.date().isoformat() if paper.published_at else 'unknown'}\n"
            f"Code: {', '.join(paper.code_urls or []) or 'none'}\nAbstract: {paper.abstract or 'none'}")
    user = wrap_source("P1", paper.url, "arxiv", body, 4000)
    res = _llm(provider, "narrative.paper", "standard", "paper", user, _TextOut, 800)
    if res is None or not res.parsed.text.strip():
        return paper_significance_template(paper)
    return narrative(res.parsed.text.strip(), epistemic="MODEL_OPINION", generator=_gen(res), model=res.model)


# ── morning brief ────────────────────────────────────────────────────────────


def morning_brief_template(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return narrative("No significant changes on the radar today.")
    lead = items[0]
    text = f"{len(items)} notable change{'s' if len(items) != 1 else ''} on the radar today. Leading: {lead['text']}"
    used = [lead]
    if len(items) > 1:
        text += f" Also: {items[1]['text']}"
        used.append(items[1])
    return narrative(text, citations=[e for i in used for e in (i.get("evidence") or [])])


# Internal ids the model must never show the reader: item ids (I2), source ids (S1, G3, P1), "item 2",
# bracketed numbers ([2]) and UUIDs (signal ids).
_SEP = r"\s*(?:,|;|&|and|/|–|-)\s*"
_BRIEF_BRACKET_REF = re.compile(
    r"\s*(?:"
    rf"[\[(]\s*(?:items?\s*#?\s*)?[IGSP]\d{{1,3}}(?:{_SEP}[IGSP]?\d{{1,3}})*\s*[\])]"  # (I2) [S1, S3]
    rf"|[\[(]\s*items?\s*#?\s*\d{{1,3}}(?:{_SEP}#?\d{{1,3}})*\s*[\])]"  # (item 2) (items 1 and 3)
    rf"|\[\s*#?\d{{1,3}}(?:{_SEP}#?\d{{1,3}})*\s*\]"  # [2] [1, 3]
    r")", re.I)
_BRIEF_ID_TOKEN = re.compile(r"\b[IGSP]\d{1,3}\b")
_BRIEF_ITEM_WORD = re.compile(r"\bitems?\s*#?\s*\d+\b", re.I)
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


def clean_brief_summary(text: str, items: Sequence[dict[str, Any]]) -> str | None:
    """Post-validate a model-written brief summary. Bracketed id references ("(I2)", "[items 1, 3]") are
    stripped; any id that remains in the prose ("I2 says …", "item 3", a UUID, a <source> tag) rejects the
    text (→ None, the caller falls back to the template). Tokens that genuinely occur in an item's own
    text (e.g. a product called "G1") are allowed."""
    corpus = " ".join(str(i.get("text") or "") for i in items)
    allowed = set(_BRIEF_ID_TOKEN.findall(corpus))

    def _strip(m: re.Match[str]) -> str:
        inner = m.group(0)
        toks = _BRIEF_ID_TOKEN.findall(inner)
        if toks and all(t in allowed for t in toks):
            return inner  # e.g. "(G1)" quoted from an item: not an id reference
        return ""

    out = _BRIEF_BRACKET_REF.sub(_strip, text or "")
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    if not out or "<source" in out.lower() or _UUID_RE.search(out) or _BRIEF_ITEM_WORD.search(out):
        return None
    if any(t not in allowed for t in _BRIEF_ID_TOKEN.findall(out)):
        return None
    return out


def morning_brief_summary(items: Sequence[dict[str, Any]], *, provider: BaseProvider | None = None) -> dict[str, Any]:
    """``items`` are BriefItem-like dicts: {n, text, evidence: [signal ids]}.

    The model sees the items as numbered sources and must cite them only in ``item_numbers``. Its text is
    post-validated (``clean_brief_summary``): leaked ids are stripped or the answer is rejected, and an
    answer that cites no provided item is rejected — both fall back to the deterministic template."""
    if not items:
        return morning_brief_template(items)
    by_n = {int(i["n"]): i for i in items}
    blocks = "\n".join(wrap_source(f"I{i['n']}", None, "system", i["text"], 600) for i in items)
    user = (f"Date: {clock.today().isoformat()}\nItems (ids are for item_numbers only — never write them in "
            f"text):\n{blocks}")
    res = _llm(provider, "narrative.morning_brief", "standard", "morning_brief", user, _MorningOut, 1000)
    if res is None or not res.parsed.text.strip():
        return morning_brief_template(items)
    used = [by_n[n] for n in dict.fromkeys(res.parsed.item_numbers) if n in by_n]
    text = clean_brief_summary(res.parsed.text.strip(), items)
    if text is None or not used:
        log.info("morning brief summary rejected (leaked ids or no valid item cited); using template")
        return morning_brief_template(items)
    return narrative(text, epistemic="MODEL_OPINION", generator=_gen(res), model=res.model,
                     citations=[e for i in used for e in (i.get("evidence") or [])])
