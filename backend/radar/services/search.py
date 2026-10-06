"""Natural-language search over companies and people.

``parse_query`` is deterministic first: phrase rules + thesis keyword matching turn a
question into :class:`SearchFilters` plus human-readable interpretation chips. Only
when it finds fewer than two constraints *and* a live LLM is configured is
``llm_parse_query`` consulted, and its output merged into the empty fields.

``execute`` applies the filters with a handful of batched queries (SQL prefilter,
then score/attention/affiliation checks in Python over the candidate set) and
explains every hit with ``match_reasons``.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompanyPerson,
    EntityTopic,
    Job,
    PaperAuthor,
    Person,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
    Topic,
)
from radar.schemas.api import SearchFilters, SearchResponse, SearchResult
from radar.services.presenters import company_metrics, headline, latest_snapshots, score_deltas, theme_label
from radar.services.thesis_map import momentum_share
from radar.thesis.loader import Thesis, get_thesis

Parser = Literal["deterministic", "llm", "hybrid"]

MIN_TOPIC_SCORE = 0.3
MAX_CANDIDATES = 2000
DEFAULT_LIMIT = 25
LOW_ATTENTION_MAX = 40.0
ACCELERATING_SHARE = 0.5
PERSON_ACCELERATING_DELTA = 3.0
# "high technical momentum" queries (sort=momentum) also drop companies below this share of
# technical_momentum + acceleration max — otherwise the sort would list everything.
MOMENTUM_FLOOR = 0.35
NEW_DAYS = 30

_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "eighteen": 18, "twenty": 20, "thirty": 30,
        "few": 3, "couple": 2, "several": 3}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "quarter": 90, "year": 365}

# Phrase -> topic key, checked before generic keyword matching.
TOPIC_PHRASES: list[tuple[str, str]] = [
    (r"outside (?:of )?(?:nvidia(?:'s)? )?cuda", "semiconductors.cuda_alternatives"),
    (r"cuda alternatives?|alternatives? to (?:nvidia(?:'s)? )?cuda|non[- ]nvidia|beyond cuda|without cuda",
     "semiconductors.cuda_alternatives"),
    (r"autonomous defen[cs]e|defen[cs]e autonomy|military autonomy", "defense.autonomy"),
    (r"counter[- ]drones?|anti[- ]drones?", "defense.counter_uas"),
    (r"robot(?:ic)? hands?|dexterous hands?", "robotics.manipulation"),
    (r"nuclear", "energy.fission"),
    (r"chips?", "semiconductors.ai_accelerators"),
]

# Words that name a whole theme.
THEME_WORDS: dict[str, str] = {
    "ai": "ai", "artificial intelligence": "ai", "machine learning": "ai",
    "robotics": "robotics", "robotic": "robotics", "robots": "robotics", "robot": "robotics",
    "defense": "defense", "defence": "defense", "military": "defense", "national security": "defense",
    "energy": "energy", "climate": "energy",
    "semiconductor": "semiconductors", "semiconductors": "semiconductors", "silicon": "semiconductors",
    "manufacturing": "manufacturing", "factory": "manufacturing", "industrial": "manufacturing",
    "frontier infrastructure": "frontier_infrastructure", "infrastructure": "frontier_infrastructure",
    "space": "frontier_infrastructure",
}

HIRING_ALIASES: dict[str, str] = {
    "machine learning": "ml_research", "ml": "ml_research", "ai": "ml_research", "research": "ml_research",
    "researchers": "ml_research", "scientists": "ml_research",
    "robotics": "robotics_controls", "controls": "robotics_controls", "perception": "robotics_controls",
    "embedded": "embedded_firmware", "firmware": "embedded_firmware", "hardware": "embedded_firmware",
    "electrical": "embedded_firmware",
    "silicon": "silicon", "chip": "silicon", "asic": "silicon", "rtl": "silicon",
    "gpu": "gpu_compiler", "compiler": "gpu_compiler", "cuda": "gpu_compiler", "kernel": "gpu_compiler",
    "rf": "rf_defense", "radar": "rf_defense", "gnc": "rf_defense",
    "plasma": "energy_plasma", "nuclear": "energy_plasma", "power electronics": "energy_plasma",
    "manufacturing": "manufacturing", "process": "manufacturing",
    "mechanical": "mechanical", "propulsion": "mechanical",
    "software": "software", "infrastructure": "infra", "infra": "infra", "distributed systems": "infra",
    "sales": "gtm",
}

STAGE_PHRASES: list[tuple[str, list[str], str]] = [
    (r"pre[- ]?seed", ["pre_seed"], "stage: pre-seed"),
    (r"series a", ["series_a"], "stage: Series A"),
    (r"early[- ]stage", ["formation", "pre_seed", "seed"], "stage: early (formation–seed)"),
    (r"stealth|pre[- ]company|formation[- ]stage|just (?:formed|founded)", ["formation", "pre_seed"],
     "stage: stealth / formation"),
    (r"(?<!pre-)(?<!pre )\bseed[- ]stage\b|\bat seed\b", ["seed"], "stage: seed"),
]

PERSON_WORDS = r"founders?|people|persons?|researchers?|scientists?|engineers?|builders?|operators?|cofounders?|co-founders?|talent"
COMPANY_WORDS = r"compan(?:y|ies)|startups?|teams?|projects?|labs?|businesses|ventures?|orgs?|organizations?"
PAPER_WORDS = r"papers?|research papers?|publications?|preprints?|arxiv"

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "with", "to", "who", "that", "which", "are", "is", "be",
    "by", "at", "from", "into", "show", "me", "find", "list", "any", "all", "some", "interesting", "good", "best",
    "top", "working", "building", "doing", "could", "become", "would", "have", "has", "but", "than", "more",
    "most", "very", "high", "low", "using", "based", "like", "what", "where", "this", "these", "those", "their",
    "work", "works", "about", "new", "recent", "recently",
}


@dataclass
class _Parse:
    filters: SearchFilters
    chips: list[str] = field(default_factory=list)
    constraints: int = 0

    def chip(self, text: str, *, constraint: bool = True) -> None:
        if text not in self.chips:
            self.chips.append(text)
            if constraint:
                self.constraints += 1


def _phrase_re(phrase: str) -> str:
    return r"(?<![a-z0-9])" + re.escape(phrase) + r"s?(?![a-z0-9])"


def _consume(text: str, pattern: str) -> tuple[str, list[re.Match]]:
    found = list(re.finditer(pattern, text))
    for m in reversed(found):
        text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return text, found


def _window_days(text: str) -> tuple[int | None, str]:
    m = re.search(
        r"(?:in |over |during |within )?(?:the )?(?:last|past|previous|prior)\s+"
        r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|eighteen|twenty|thirty|few|couple|several)?"
        r"\s*(?:of\s+)?(day|week|month|quarter|year)s?",
        text,
    )
    if m:
        n_raw = m.group(1) or "one"
        n = int(n_raw) if n_raw.isdigit() else _NUM.get(n_raw, 1)
        days = max(1, min(n * _UNIT_DAYS[m.group(2)], 3650))
        return days, text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    for pat, days in ((r"\bthis week\b", 7), (r"\bthis month\b", 30), (r"\bthis year\b", 365), (r"\btoday\b", 1),
                      (r"\byesterday\b", 2), (r"\bthis quarter\b", 90), (r"\blately\b", 60)):
        m = re.search(pat, text)
        if m:
            return days, text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    return None, text


def _known_orgs(thesis: Thesis) -> list[str]:
    out: list[str] = []
    for tiers in (thesis.affiliation_tiers, thesis.demo_affiliation_tiers):
        for names in tiers.values():
            out.extend(names)
    return sorted(dict.fromkeys(out), key=len, reverse=True)


def _tier1(thesis: Thesis) -> list[str]:
    return list(dict.fromkeys(thesis.affiliation_tiers.get("tier_1", []) + thesis.demo_affiliation_tiers.get("tier_1", [])))


def deterministic_parse(query: str, thesis: Thesis | None = None) -> _Parse:
    thesis = thesis or get_thesis()
    raw = (query or "").strip()[:500]
    text = " " + raw.lower() + " "
    p = _Parse(filters=SearchFilters())
    f = p.filters

    # ── affiliation cues (before topic matching: "left major AI labs" must not tag theme AI) ──
    left = False
    text, ms = _consume(text, r"\b(?:who |that )?(?:left|departed|quit|exited|recently left|have left|just left)\s+"
                              r"(?:a |an |the )?(?:major|big|top|frontier|leading|tier[- ]?1|large)\s+"
                              r"(?:ai |research |tech |robotics |frontier )?(?:labs?|companies|orgs?)\b")
    if ms:
        left = True
        f.prior_affiliations = _tier1(thesis)
        p.chip("left a tier-1 lab")
    text, ms = _consume(text, r"\b(?:from|ex|former|formerly at|previously at|alumni of|alums of)\s+"
                              r"(?:major|big|top|frontier|leading|tier[- ]?1)\s+(?:ai |research |tech )?labs?\b")
    if ms and not f.prior_affiliations:
        f.prior_affiliations = _tier1(thesis)
        p.chip("prior affiliation: tier-1 lab")
    cue = r"(left|departed|quit|former|formerly at|previously at|ex-|ex |alumni of|alums of|from)"
    for org in _known_orgs(thesis):
        m = re.search(r"\b" + cue + r"\s*(?:the )?" + re.escape(org.lower()) + r"(?![a-z0-9])", text)
        if m:
            if org not in f.prior_affiliations:
                f.prior_affiliations.append(org)
            if m.group(1) in ("left", "departed", "quit"):
                left = True
            p.chip(f"{'left' if m.group(1) in ('left', 'departed', 'quit') else 'prior affiliation'}: {org}")
            text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]
    # Unknown named org after a cue: take the capitalized words from the original query.
    for m in re.finditer(r"(?i:\b(left|departed|former|formerly at|previously at|ex-|alumni of))\s*"
                         r"((?:[A-Z][\w&.\-]*)(?:\s+[A-Z][\w&.\-]*){0,3})", raw):
        org = m.group(2).strip(" .")
        if org and org.lower() not in {"a", "the"} and not any(org.lower() == o.lower() for o in f.prior_affiliations):
            f.prior_affiliations.append(org)
            cue_word = m.group(1).lower()
            if cue_word in ("left", "departed"):
                left = True
            p.chip(f"{'left' if cue_word in ('left', 'departed') else 'prior affiliation'}: {org}")
            text, _ = _consume(text, re.escape(org.lower()))
    if left and "affiliation_changed" not in f.signal_types:
        f.signal_types.append("affiliation_changed")

    # ── time window ──
    days, text = _window_days(text)

    # ── hiring cues (consume the clause so its words don't become topics) ──
    m = re.search(r"\bhiring\b(?:\s+[a-z0-9&/+\-]+){0,4}?\s+(?:engineers?|roles?|researchers?|scientists?|"
                  r"people|talent|positions?|staff|developers?|physicists?)\b", text) \
        or re.search(r"\bhiring\b(?:\s+(?!and\b|with\b|in\b|but\b)[a-z0-9&/+\-]+){0,2}", text)
    if m:
        clause = m.group(0)
        classes: list[str] = []
        for cls, phrases in thesis.hiring_roles.items():
            if any(re.search(_phrase_re(ph), clause) for ph in phrases):
                classes.append(cls)
        for alias, cls in HIRING_ALIASES.items():
            if re.search(_phrase_re(alias), clause) and cls not in classes:
                classes.append(cls)
        if not classes:
            classes = [c for c in thesis.hiring_roles if c != "gtm"]
            p.chip("hiring: technical roles")
        else:
            p.chip("hiring: " + ", ".join(classes))
        f.hiring_role_classes = classes
        text = text[: m.start()] + " " * (m.end() - m.start()) + text[m.end():]

    # ── attention cues ──
    text, ms = _consume(text, r"\b(?:low|little|limited|minimal|no|less|lower|without much)\s+"
                              r"(?:social |public |media |press |conventional )?(?:attention|hype|buzz|coverage|following)\b"
                              r"|\bunder[- ]the[- ]radar\b|\boverlooked\b|\bunderhyped\b|\bunder[- ]?hyped\b|\bquiet\b")
    if ms:
        f.max_attention = LOW_ATTENTION_MAX
        p.chip(f"attention percentile ≤ {LOW_ATTENTION_MAX:.0f}")
    text, ms = _consume(text, r"\battention gap\b")
    if ms:
        f.min_attention_gap = 20.0
        p.chip("attention gap ≥ 20")

    # ── momentum cues ──
    text, ms = _consume(text, r"\baccelerat(?:ing|ion|e)\b(?:\s+(?:github|commit|star|technical)?\s*"
                              r"(?:activity|growth|momentum|commits|stars)?)?")
    if ms:
        f.accelerating = True
        p.chip("accelerating")
    text, ms = _consume(text, r"\b(?:high|strong|rapid|fast)?\s*(?:technical\s+)?momentum\b|\bbreakout\b|"
                              r"\bfast[- ]growing\b|\bgrowing fast\b|\btrending\b|\bheating up\b")
    if ms:
        f.sort = "momentum"
        p.chip("sort: technical momentum")
    if f.max_attention is not None and f.sort == "score":
        f.sort = "attention_gap"

    # ── recency ──
    text, ms = _consume(text, r"\b(?:new|newly formed|emerging|just launched|brand[- ]new|fresh)\b")
    if ms:
        f.first_seen_within_days = NEW_DAYS
        p.chip(f"new: first seen ≤ {NEW_DAYS} days")
    if days:
        if f.signal_types:
            f.event_within_days = days
            p.chip(f"event within {days} days")
        elif f.first_seen_within_days:
            f.first_seen_within_days = min(days, f.first_seen_within_days) if days < NEW_DAYS else days
        else:
            f.event_within_days = days
            p.chip(f"activity within {days} days")

    # ── stage ──
    for pat, stages, label in STAGE_PHRASES:
        text, ms = _consume(text, pat)
        if ms:
            f.stage = list(dict.fromkeys(f.stage + stages))
            p.chip(label)

    # ── entity types ──
    types: list[str] = []
    paper_hit = re.search(r"\b(?:" + PAPER_WORDS + r")\b", text)
    if paper_hit:
        types.append("paper")
        text, _ = _consume(text, r"\b(?:" + PAPER_WORDS + r")\b")
    if re.search(r"\b(?:" + PERSON_WORDS + r")\b", text):
        types.append("person")
    if re.search(r"\b(?:" + COMPANY_WORDS + r")\b", text) and not (paper_hit and re.search(r"become compan", text)):
        types.append("company")
    if "paper" in types:
        f.entity_types = ["paper"]
        p.chip("papers → authors & linked companies")
        text, ms = _consume(text, r"could become (?:a )?compan(?:y|ies)|spin[- ]?outs?|commerciali[sz]\w*")
        if ms:
            p.chip("spin-out potential: code released, authors without a company")
    elif types:
        f.entity_types = types  # type: ignore[assignment]
        p.chip("type: " + " & ".join("founders/people" if t == "person" else "companies" for t in types),
               constraint=False)
    text, _ = _consume(text, r"\b(?:" + PERSON_WORDS + "|" + COMPANY_WORDS + r")\b")

    # ── topics: special phrases, then thesis keywords / labels ──
    for pat, key in TOPIC_PHRASES:
        text, ms = _consume(text, pat)
        if ms and key not in f.topics:
            f.topics.append(key)
    candidates: list[tuple[str, str]] = []
    for theme in thesis.themes.values():
        for sub in theme.subcategories.values():
            for ph in [sub.label.lower(), *[k.lower() for k in sub.keywords]]:
                candidates.append((ph, sub.key))
    for ph, key in sorted(candidates, key=lambda c: -len(c[0])):
        if len(ph) < 2:
            continue
        if re.search(_phrase_re(ph), text):
            if key not in f.topics:
                f.topics.append(key)
            text, _ = _consume(text, _phrase_re(ph))
    for word, theme in sorted(THEME_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(_phrase_re(word), text):
            # a theme word narrows nothing if a subcategory of that theme was already matched
            if theme not in f.themes and not any(t.startswith(theme + ".") for t in f.topics):
                f.themes.append(theme)
            text, _ = _consume(text, _phrase_re(word))
    for t in f.themes:
        p.chip(f"theme: {theme_label(t)}")
    for key in f.topics:
        tk, _, sk = key.partition(".")
        th = thesis.themes.get(tk)
        label = th.subcategories[sk].label if th and sk in th.subcategories else key
        p.chip(f"topic: {theme_label(tk)} · {label}")

    # ── fallback: free-text keywords ──
    if p.constraints == 0:
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9.\-]{1,}", text) if w not in STOPWORDS]
        if words:
            f.keywords = list(dict.fromkeys(words))[:6]
            p.chip("keywords: " + ", ".join(f.keywords))
    return p


def parse_query(query: str, thesis: Thesis | None = None) -> tuple[SearchFilters, list[str], Parser]:
    """Deterministic parse; LLM merge only when < 2 constraints and a live model is configured."""
    p = deterministic_parse(query, thesis)
    if p.constraints >= 2:
        return p.filters, p.chips, "deterministic"
    from radar.llm.provider import get_llm

    if not get_llm().available:
        return p.filters, p.chips, "deterministic"
    from radar.llm.search_parse import llm_parse_query

    llm = llm_parse_query(query)
    if llm is None:
        return p.filters, p.chips, "deterministic"
    return _merge(p, llm)


def _merge(p: _Parse, llm: SearchFilters) -> tuple[SearchFilters, list[str], Parser]:
    f = p.filters.model_copy(deep=True)
    chips = list(p.chips)
    added = False
    defaults = SearchFilters()
    for name in ("themes", "topics", "prior_affiliations", "hiring_role_classes", "stage", "signal_types"):
        mine, theirs = getattr(f, name), getattr(llm, name)
        new = [v for v in theirs if v not in mine]
        if new:
            setattr(f, name, mine + new)
            chips.append(f"{name.replace('_', ' ')}: {', '.join(new)} (model)")
            added = True
    if llm.keywords and not f.keywords and not (f.themes or f.topics):
        f.keywords = llm.keywords
        chips.append(f"keywords: {', '.join(llm.keywords)} (model)")
        added = True
    for name in ("min_score", "first_seen_within_days", "event_within_days", "max_attention", "min_attention_gap"):
        if getattr(f, name) is None and getattr(llm, name) is not None:
            setattr(f, name, getattr(llm, name))
            chips.append(f"{name.replace('_', ' ')}: {getattr(llm, name)} (model)")
            added = True
    if llm.accelerating and not f.accelerating:
        f.accelerating = True
        chips.append("accelerating (model)")
        added = True
    if f.sort == defaults.sort and llm.sort != defaults.sort:
        f.sort = llm.sort
        added = True
    if f.entity_types == defaults.entity_types and llm.entity_types != defaults.entity_types:
        f.entity_types = llm.entity_types
        added = True
    if not added:
        return p.filters, p.chips, "deterministic"
    return f, chips, ("hybrid" if p.constraints else "llm")


# ── execution ────────────────────────────────────────────────────────────────


@dataclass
class _Hit:
    entity_type: str
    entity: Any
    reasons: list[str]
    score: float | None
    sort_key: float


def _topic_rows(session: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[tuple[str, str, str, float]]]:
    ids = list(set(ids))
    out: dict[uuid.UUID, list[tuple[str, str, str, float]]] = defaultdict(list)
    if not ids:
        return out
    for eid, key, theme, label, score in session.execute(
        select(EntityTopic.entity_id, Topic.key, Topic.theme, Topic.label, EntityTopic.score)
        .join(Topic, Topic.id == EntityTopic.topic_id)
        .where(EntityTopic.entity_id.in_(ids), EntityTopic.score >= MIN_TOPIC_SCORE)
    ):
        out[eid].append((key, theme, label, float(score)))
    return out


def _best_topic(rows: list[tuple[str, str, str, float]], f: SearchFilters) -> tuple[str, str, str, float] | None:
    hits = [r for r in rows if r[0] in f.topics or r[1] in f.themes]
    return max(hits, key=lambda r: r[3]) if hits else None


def _thesis_reason(r: tuple[str, str, str, float]) -> str:
    return f"Thesis: {theme_label(r[1])} · {r[2]} ({r[3]:.2f})"


def _matches_org(orgs: list[str], wanted: list[str]) -> str | None:
    for w in wanted:
        wl = w.lower()
        for o in orgs or []:
            ol = o.lower()
            if re.search(r"(?<![a-z0-9])" + re.escape(wl) + r"(?![a-z0-9])", ol) or ol == wl:
                return o
    return None


def _days_ago(dt) -> int | None:
    return (clock.now() - dt).days if dt else None


def _signal_index(
    session: Session, owner: dict[uuid.UUID, uuid.UUID], f: SearchFilters
) -> dict[uuid.UUID, list[Signal]]:
    """owner entity -> matching signals (types/window), across the given id -> owner map."""
    if not owner or not (f.signal_types or f.event_within_days):
        return {}
    q = select(Signal).where(Signal.entity_id.in_(list(owner)))
    if f.signal_types:
        q = q.where(Signal.signal_type.in_(f.signal_types))
    if f.event_within_days:
        q = q.where(Signal.event_at >= clock.now() - timedelta(days=f.event_within_days))
    out: dict[uuid.UUID, list[Signal]] = defaultdict(list)
    for s in session.scalars(q.order_by(Signal.event_at.desc())):
        out[owner[s.entity_id]].append(s)
    return out


def _search_companies(session: Session, f: SearchFilters) -> list[_Hit]:
    now = clock.now()
    q = select(Company).where(Company.dismissed.is_(False))
    topic_owner: dict[uuid.UUID, uuid.UUID] = {}
    if f.topics or f.themes:
        tq = (select(EntityTopic.entity_id).join(Topic, Topic.id == EntityTopic.topic_id)
              .where(EntityTopic.score >= MIN_TOPIC_SCORE,
                     or_(Topic.key.in_(f.topics or ["-"]), Topic.theme.in_(f.themes or ["-"]))))
        tagged = set(session.scalars(tq))
        repo_owner = dict(session.execute(select(Repository.id, Repository.company_id).where(
            Repository.id.in_(tagged), Repository.company_id.is_not(None))).tuples().all()) if tagged else {}
        topic_owner = {**{e: e for e in tagged}, **repo_owner}
        q = q.where(Company.id.in_(set(topic_owner.values()) or {uuid.uuid4()}))
    if f.first_seen_within_days:
        q = q.where(Company.first_seen_at >= now - timedelta(days=f.first_seen_within_days))
    if f.stage:
        q = q.where(Company.stage.in_(f.stage))
    if f.keywords:
        q = q.where(or_(*[or_(Company.name.ilike(f"%{k}%"), Company.description.ilike(f"%{k}%"),
                              Company.slug.ilike(f"%{k}%")) for k in f.keywords]))
    if f.hiring_role_classes:
        q = q.where(Company.id.in_(select(Job.company_id).where(Job.role_class.in_(f.hiring_role_classes),
                                                                 Job.closed_at.is_(None))))
    q = q.outerjoin(ScoreSnapshot, (ScoreSnapshot.entity_id == Company.id) & (ScoreSnapshot.day == clock.today()))
    companies = list(session.scalars(q.order_by(ScoreSnapshot.score.desc().nulls_last()).limit(MAX_CANDIDATES)))
    if not companies:
        return []
    ids = [c.id for c in companies]
    snaps = latest_snapshots(session, ids)
    d7 = score_deltas(session, snaps, 7) if f.sort == "delta" else {}
    mets = company_metrics(session, ids)
    topics = _topic_rows(session, ids + [r for r, o in topic_owner.items() if o in set(ids) and r not in ids])
    repo_topics: dict[uuid.UUID, list] = defaultdict(list)
    for rid, cid in topic_owner.items():
        if rid != cid:
            repo_topics[cid].extend(topics.get(rid, []))

    # people (affiliation filter / reasons) and signals
    people_by_company: dict[uuid.UUID, list[Person]] = defaultdict(list)
    if f.prior_affiliations or f.signal_types:
        for cid, person in session.execute(
            select(CompanyPerson.company_id, Person).join(Person, Person.id == CompanyPerson.person_id)
            .where(CompanyPerson.company_id.in_(ids))
        ).tuples():
            people_by_company[cid].append(person)
    owner: dict[uuid.UUID, uuid.UUID] = {c: c for c in ids}
    if f.signal_types or f.event_within_days:
        for rid, cid in session.execute(select(Repository.id, Repository.company_id)
                                        .where(Repository.company_id.in_(ids))).tuples():
            owner[rid] = cid
        for cid, people in people_by_company.items():
            for p in people:
                owner.setdefault(p.id, cid)
    sigs = _signal_index(session, owner, f)
    jobs: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    if f.hiring_role_classes:
        for cid, rc in session.execute(select(Job.company_id, Job.role_class).where(
                Job.company_id.in_(ids), Job.role_class.in_(f.hiring_role_classes), Job.closed_at.is_(None))):
            jobs[cid][rc] += 1

    hits: list[_Hit] = []
    for c in companies:
        snap = snaps.get(c.id)
        reasons: list[str] = []
        if f.topics or f.themes:
            best = _best_topic(topics.get(c.id, []) + repo_topics.get(c.id, []), f)
            if best is None:
                continue
            reasons.append(_thesis_reason(best))
        if f.prior_affiliations:
            matched = [(p, _matches_org(p.prior_affiliations, f.prior_affiliations)) for p in people_by_company[c.id]]
            matched = [(p, o) for p, o in matched if o]
            if not matched:
                continue
            reasons.append(f"Prior affiliation: {matched[0][1]} ({', '.join(p.name for p, _ in matched[:3])})")
        if f.signal_types or f.event_within_days:
            ss = sigs.get(c.id, [])
            if not ss:
                continue
            reasons.append(f"Signal: {ss[0].title} ({ss[0].event_at:%b %-d})")
        if f.hiring_role_classes:
            j = jobs.get(c.id)
            if not j:
                continue
            reasons.append("Hiring: " + ", ".join(f"{n} {k}" for k, n in sorted(j.items(), key=lambda kv: -kv[1])))
        att = (snap.attention or {}) if snap else {}
        if f.max_attention is not None:
            a = att.get("attention")
            if a is None or a > f.max_attention:
                continue
            reasons.append(f"Attention percentile {a:.0f}")
        if f.min_attention_gap is not None:
            g = att.get("gap")
            if g is None or g < f.min_attention_gap:
                continue
            reasons.append(f"Attention gap {g:+.0f}")
        if f.accelerating:
            acc = ((snap.components or {}).get("acceleration") or {}) if snap else {}
            if not acc.get("max") or acc.get("value", 0) / acc["max"] < ACCELERATING_SHARE:
                continue
            reasons.append(f"Acceleration {acc['value']:.1f}/{acc['max']:.0f}")
        if f.min_score is not None and (snap is None or snap.score < f.min_score):
            continue
        if f.first_seen_within_days:
            reasons.append(f"First seen {_days_ago(c.first_seen_at)} days ago")
        if f.stage:
            reasons.append(f"Stage: {c.stage.replace('_', '-')}")
        if f.keywords:
            hay = f"{c.name} {c.description or ''}".lower()
            kw = [k for k in f.keywords if k in hay or k in c.slug]
            reasons.append("Keyword match: " + ", ".join(kw or f.keywords[:1]))
        m = mets.get(c.id, {})
        if m.get("stars_30d") and m["stars_30d"] >= 10:
            reasons.append(f"Stars +{int(m['stars_30d']):,} in 30d")
        if f.sort == "momentum":
            ms = momentum_share(snap.components if snap else None) or 0.0
            if ms < MOMENTUM_FLOOR:
                continue
            reasons.append(f"Technical momentum + acceleration {ms * 100:.0f}% of max")
        hits.append(_Hit("company", c, reasons, snap.score if snap else None,
                         _sort_value(f, snap, c.first_seen_at, d7.get(c.id))))
    return hits


def _sort_value(f: SearchFilters, snap: ScoreSnapshot | None, first_seen, delta: float | None) -> float:
    if f.sort == "momentum":
        return momentum_share(snap.components if snap else None) or 0.0
    if f.sort == "attention_gap":
        return float(((snap.attention or {}).get("gap") or -999) if snap else -999)
    if f.sort == "recent":
        return first_seen.timestamp() if first_seen else 0.0
    if f.sort == "delta":
        return delta if delta is not None else -999.0
    return snap.score if snap else -1.0


def _search_people(session: Session, f: SearchFilters) -> list[_Hit]:
    now = clock.now()
    q = select(Person).where(Person.dismissed.is_(False))
    if f.first_seen_within_days:
        q = q.where(Person.first_seen_at >= now - timedelta(days=f.first_seen_within_days))
    if f.keywords:
        q = q.where(or_(*[or_(Person.name.ilike(f"%{k}%"), Person.bio.ilike(f"%{k}%"), Person.slug.ilike(f"%{k}%"),
                              Person.github_handle.ilike(f"%{k}%")) for k in f.keywords]))
    if f.signal_types or f.event_within_days:
        sq = select(Signal.entity_id).where(Signal.entity_type == "person")
        if f.signal_types:
            sq = sq.where(Signal.signal_type.in_(f.signal_types))
        if f.event_within_days:
            sq = sq.where(Signal.event_at >= now - timedelta(days=f.event_within_days))
        q = q.where(Person.id.in_(sq))
    if f.prior_affiliations:
        q = q.where(func.cardinality(Person.prior_affiliations) > 0)
    q = q.outerjoin(ScoreSnapshot, (ScoreSnapshot.entity_id == Person.id) & (ScoreSnapshot.day == clock.today()))
    people = list(session.scalars(q.order_by(ScoreSnapshot.score.desc().nulls_last()).limit(MAX_CANDIDATES)))
    if not people:
        return []
    ids = [p.id for p in people]
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for pid, cid in session.execute(select(CompanyPerson.person_id, CompanyPerson.company_id)
                                    .where(CompanyPerson.person_id.in_(ids))).tuples():
        links[pid].append(cid)
    company_ids = {c for cs in links.values() for c in cs}
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(company_ids)))} \
        if company_ids else {}
    topics = _topic_rows(session, ids + list(company_ids))
    snaps = latest_snapshots(session, ids)
    d7 = score_deltas(session, snaps, 7) if (f.accelerating or f.sort == "delta") else {}
    sigs = _signal_index(session, {i: i for i in ids}, f)
    jobs: dict[uuid.UUID, int] = defaultdict(int)
    if f.hiring_role_classes and company_ids:
        for cid, n in session.execute(select(Job.company_id, func.count()).where(
                Job.company_id.in_(company_ids), Job.role_class.in_(f.hiring_role_classes),
                Job.closed_at.is_(None)).group_by(Job.company_id)):
            jobs[cid] = int(n)

    hits: list[_Hit] = []
    for p in people:
        snap = snaps.get(p.id)
        reasons: list[str] = []
        if f.topics or f.themes:
            best = _best_topic(topics.get(p.id, []), f)
            via = None
            if best is None:
                for cid in links.get(p.id, []):
                    best = _best_topic(topics.get(cid, []), f)
                    if best:
                        via = companies[cid].name
                        break
            domain = next((d for d in (p.domains or []) if d in f.themes), None)
            if best is not None:
                reasons.append(_thesis_reason(best) + (f" via {via}" if via else ""))
            elif domain:
                reasons.append(f"Domain: {theme_label(domain)}")
            else:
                continue
        if f.prior_affiliations:
            org = _matches_org(p.prior_affiliations, f.prior_affiliations)
            if not org:
                continue
            reasons.append(f"Prior affiliation: {org}")
        if f.signal_types or f.event_within_days:
            ss = sigs.get(p.id, [])
            if not ss:
                continue
            reasons.append(f"Signal: {ss[0].title} ({_days_ago(ss[0].event_at)} days ago)")
        if f.hiring_role_classes:
            n = sum(jobs.get(c, 0) for c in links.get(p.id, []))
            if not n:
                continue
            reasons.append(f"Company hiring: {n} matching open roles")
        att = (snap.attention or {}) if snap else {}
        if f.max_attention is not None:
            a = att.get("attention")
            if a is None or a > f.max_attention:
                continue
            reasons.append(f"Attention percentile {a:.0f}")
        if f.min_attention_gap is not None:
            g = att.get("gap")
            if g is None or g < f.min_attention_gap:
                continue
            reasons.append(f"Attention gap {g:+.0f}")
        if f.accelerating:
            d = d7.get(p.id)
            if d is None or d < PERSON_ACCELERATING_DELTA:
                continue
            reasons.append(f"Founder score +{d:.0f} in 7d")
        if f.min_score is not None and (snap is None or snap.score < f.min_score):
            continue
        if f.stage:
            stages = {companies[c].stage for c in links.get(p.id, []) if c in companies}
            if not stages & set(f.stage) and not (p.current_company is None and "formation" in f.stage):
                continue
            reasons.append("Stage: " + (", ".join(sorted(stages & set(f.stage))) or "pre-company"))
        if f.first_seen_within_days:
            reasons.append(f"First seen {_days_ago(p.first_seen_at)} days ago")
        if f.keywords:
            reasons.append("Keyword match: " + ", ".join(f.keywords[:3]))
        if p.current_company is None and p.prior_affiliations and not f.prior_affiliations:
            reasons.append(f"No current company — previously {p.prior_affiliations[-1]}")
        hits.append(_Hit("person", p, reasons, snap.score if snap else None,
                         _sort_value(f, snap, p.first_seen_at, d7.get(p.id))))
    return hits


def _search_papers(session: Session, f: SearchFilters) -> list[_Hit]:
    """Papers -> their authors (people) and linked companies, ranked by spin-out potential."""
    now = clock.now()
    q = select(ResearchPaper)
    if f.topics or f.themes:
        q = q.where(ResearchPaper.id.in_(
            select(EntityTopic.entity_id).join(Topic, Topic.id == EntityTopic.topic_id).where(
                EntityTopic.score >= MIN_TOPIC_SCORE,
                or_(Topic.key.in_(f.topics or ["-"]), Topic.theme.in_(f.themes or ["-"])))))
    window = f.event_within_days or f.first_seen_within_days or 365
    q = q.where(ResearchPaper.published_at >= now - timedelta(days=window))
    if f.keywords:
        q = q.where(or_(*[or_(ResearchPaper.title.ilike(f"%{k}%"), ResearchPaper.abstract.ilike(f"%{k}%"))
                          for k in f.keywords]))
    papers = list(session.scalars(q.order_by(ResearchPaper.published_at.desc()).limit(500)))
    if not papers:
        return []
    pids = [p.id for p in papers]
    authors: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for paper_id, person_id in session.execute(select(PaperAuthor.paper_id, PaperAuthor.person_id)
                                               .where(PaperAuthor.paper_id.in_(pids))
                                               .order_by(PaperAuthor.position)).tuples():
        authors[paper_id].append(person_id)
    person_ids = {a for v in authors.values() for a in v}
    company_ids = {p.company_id for p in papers if p.company_id}
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(person_ids),
                                                                     Person.dismissed.is_(False)))} \
        if person_ids else {}
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(company_ids),
                                                                         Company.dismissed.is_(False)))} \
        if company_ids else {}
    snaps = latest_snapshots(session, list(people) + list(companies))
    best: dict[uuid.UUID, _Hit] = {}
    for paper in papers:
        has_code = bool(paper.code_urls)
        age = _days_ago(paper.published_at) or 0
        recency = max(0.0, 1 - age / max(window, 1))
        reason = f"Paper: “{paper.title[:90]}” ({paper.published_at:%b %-d, %Y})"
        targets: list[tuple[str, Any]] = [("person", people[a]) for a in authors.get(paper.id, []) if a in people]
        if paper.company_id in companies:
            targets.append(("company", companies[paper.company_id]))
        for etype, ent in targets:
            snap = snaps.get(ent.id)
            no_company = etype == "person" and ent.current_company is None
            potential = (0.4 if has_code else 0) + 0.3 * recency + (0.2 if no_company else 0) + \
                (0.1 * (snap.score / 100) if snap else 0)
            reasons = [reason]
            if has_code:
                reasons.append("Code released")
            if no_company:
                reasons.append("Author not yet at a company")
            elif etype == "person" and getattr(ent, "current_company", None):
                reasons.append(f"Currently at {ent.current_company}")
            if f.max_attention is not None:
                a = ((snap.attention or {}) if snap else {}).get("attention")
                if a is None or a > f.max_attention:
                    continue
                reasons.append(f"Attention percentile {a:.0f}")
            hit = best.get(ent.id)
            if hit is None:
                best[ent.id] = _Hit(etype, ent, reasons, snap.score if snap else None, potential)
            else:
                hit.sort_key += 0.1  # repeated authorship strengthens the case
                if reason not in hit.reasons:
                    hit.reasons.insert(1, reason)
    return list(best.values())


def _subtitle(h: _Hit) -> str | None:
    e = h.entity
    if h.entity_type == "company":
        kind = {"stealth": "Stealth company", "project": "Pre-company project", "lab": "Research lab"}.get(
            e.kind, "Company")
        return f"{kind} · {e.stage.replace('_', '-')}" + (f" · {e.location}" if e.location else "")
    return headline(e)


def execute(session: Session, f: SearchFilters, limit: int = DEFAULT_LIMIT) -> tuple[list[SearchResult], int]:
    hits: list[_Hit] = []
    if "paper" in f.entity_types:
        hits.extend(_search_papers(session, f))
    if "company" in f.entity_types:
        hits.extend(_search_companies(session, f))
    if "person" in f.entity_types:
        hits.extend(_search_people(session, f))
    hits.sort(key=lambda h: (-h.sort_key, -(h.score or 0), h.entity.name))
    results = [
        SearchResult(entity_type=h.entity_type, id=str(h.entity.id), slug=h.entity.slug, name=h.entity.name,
                     subtitle=_subtitle(h), score=round(h.score) if h.score is not None else None,
                     match_reasons=h.reasons[:6], is_demo=h.entity.is_demo)
        for h in hits[:limit]
    ]
    return results, len(hits)


def search(session: Session, query: str, limit: int = DEFAULT_LIMIT) -> SearchResponse:
    filters, chips, parser = parse_query(query)
    results, total = execute(session, filters, limit)
    return SearchResponse(query=query, filters=filters, interpretation=chips, parser=parser, results=results,
                          total=total)
