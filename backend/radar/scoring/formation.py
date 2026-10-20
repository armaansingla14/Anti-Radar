"""FORMATION RADAR: technical teams (and people) before the company announcement.

Candidates
----------
(a) **team** — a company of kind project/company/stealth whose GitHub organization (or
    first public repository) was created within ``YOUNG_DAYS`` and which has >= 2 linked
    people (listed members, or contributors with >= 3 commits). Labs, academic domains and
    established stages (growth/public) are excluded.
(b) **person** — someone with their *own* formation_language / x_formation_post (self-reported,
    INFERRED) or affiliation_changed signal in the last ``PERSON_SIGNAL_DAYS`` days who is
    on-thesis (fit >= ``THESIS_GATE``) or linked to a young organization, and is not already part
    of a team candidate. Someone whose only evidence is a generic "founder" self-description
    while at an established organization (non-young with > ``ESTABLISHED_FOLLOWERS`` GitHub
    followers, or an established stage) is left out.

Off-thesis team anchors (thesis fit < ``THESIS_GATE``) are excluded.

Evidence scope: a candidate cites only signals about the anchor (the org/person and its own
repositories) plus members' *own* formation signals (affiliation/bio change, formation
language). A member's activity on unrelated repositories is never cited.

FORMATION SCORE (/100, deterministic, every component carries its evidence)
--------------------------------------------------------------------------
* founder_quality    25  top-3 member Founder Scores (0.5/0.3/0.2) and best pedigree tier
* team_convergence   25  ``scoring.convergence`` (prior ties that predate the anchor)
* technical_activity 20  anchor commits / stars / new contributors / releases, percentiles vs
                         the day's candidate cohort; nothing measured -> 0 ("insufficient history")
* formation_evidence 20  distinct kinds of formation evidence (org created, working team, founding
                         team = >= 2 members' first commits within 30 days of creation, first job
                         post, website, launch, formation language, affiliation change, young org)
* novelty_attention  10  first-seen recency (6) + attention gap where measured (4; unmeasured -> 0)

Tiers: high (>= 70, >= 3 independent source families and convergence >= MEDIUM), emerging
(>= 50 and >= 2 families), early (>= 30); anything lower is not stored. Families are the
distinct source families (github, research, hiring, web, community, social) of the dated
evidence actually attached to the candidate. Labels never claim a company was founded: teams are
"EMERGING TEAM" (high/emerging) or "FORMATION SIGNALS DETECTED" (early); people are
"POSSIBLE FORMATION". No LLM is involved anywhere in this module.
"""

from __future__ import annotations

import logging
import math
import uuid
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models.core import (
    Company,
    CompanyMetric,
    CompanyPerson,
    FormationCandidate,
    Job,
    Person,
    RepoContributor,
    Repository,
    ScoreSnapshot,
    Signal,
    SignalEntity,
)
from radar.pipeline.aliases import org_key
from radar.scoring.collision import number_word
from radar.scoring.convergence import (
    JOIN_WINDOW_DAYS,
    ConvergenceResult,
    Member,
    convergence,
    founding_members,
    load_pair_relationships,
)
from radar.scoring.extract import SignalIndex, _merge_topics, _topics, load_signal_index, org_created_date
from radar.scoring.features import FORMATION_SIGNAL_TYPES, SignalLite
from radar.thesis.loader import Thesis, get_thesis

log = logging.getLogger(__name__)

FORMATION_MAX: dict[str, float] = {
    "founder_quality": 25,
    "team_convergence": 25,
    "technical_activity": 20,
    "formation_evidence": 20,
    "novelty_attention": 10,
}
COMPONENT_LABELS = {
    "founder_quality": "Founder Quality",
    "team_convergence": "Team Convergence",
    "technical_activity": "Technical Activity",
    "formation_evidence": "Formation Evidence",
    "novelty_attention": "Novelty / Attention Gap",
}
ORDER = tuple(FORMATION_MAX)

YOUNG_DAYS = 180
PERSON_SIGNAL_DAYS = 180
THESIS_GATE = 0.3
MIN_MEMBERS = 2
MIN_MEMBER_COMMITS = 3
MAX_MEMBERS = 12
TEAM_KINDS = frozenset({"project", "company", "stealth"})
EXCLUDED_STAGES = frozenset({"growth", "public"})
TOP_FOUNDER_WEIGHTS = (0.5, 0.3, 0.2)
FQ_SCORE_SHARE, FQ_TIER_SHARE = 0.75, 0.25
TECH_WEIGHTS = {"commits": 0.35, "stars": 0.30, "contributors": 0.20, "releases": 0.15}
TECH_ANCHORS = {"commits": math.log1p(15), "stars": math.log1p(20), "contributors": math.log1p(1),
                "releases": math.log1p(2)}
COHORT_MIN = 8
EVIDENCE_SATURATION = 2.2
NOVELTY_POINTS, ATTENTION_POINTS = 6.0, 4.0
NOVELTY_DECAY_DAYS = 45.0
ATTENTION_FULL_GAP = 50.0
TIER_HIGH, TIER_EMERGING, TIER_EARLY = 70, 50, 30
HIGH_MIN_FAMILIES = 3
EMERGING_MIN_FAMILIES = 2
HIGH_MIN_CONVERGENCE = ("MEDIUM", "HIGH")
ESTABLISHED_FOLLOWERS = 50
# Source families shown to users (same vocabulary as services/freshness.py and the UI).
FORMATION_FAMILY: dict[str, str] = {"github": "github", "arxiv": "research", "jobs": "hiring", "website": "web",
                                    "hackernews": "community", "x": "social"}
WHY_NOW_MAX = 6

LABEL_TEAM = "EMERGING TEAM"
LABEL_SIGNALS = "FORMATION SIGNALS DETECTED"
LABEL_PERSON = "POSSIBLE FORMATION"

LAUNCH_TYPES = frozenset({"hn_launch", "hn_show", "x_launch_post"})
LANGUAGE_TYPES = frozenset({"formation_language", "x_formation_post"})
CHANGE_TYPES = frozenset({"affiliation_changed", "bio_changed"})
ORG_TYPES = frozenset({"org_created", "repo_created"})
MEMBER_OWN_TYPES = CHANGE_TYPES | LANGUAGE_TYPES  # a member's own formation signals (cited on team candidates)
PERSON_QUALIFYING_TYPES = frozenset({"affiliation_changed", "formation_language", "x_formation_post"})

EVIDENCE_KIND_LABELS = {
    "org_created": "organization/repository created",
    "working_team": "working team on public commits",
    "founding_team": "members committing from the first weeks",
    "first_job": "first job posting",
    "website": "website listed",
    "launch": "launch post",
    "formation_language": "formation language",
    "affiliation_change": "affiliation/bio change",
    "young_org": "joined a young organization",
}


# ── Inputs ───────────────────────────────────────────────────────────────────


@dataclass
class MemberInfo:
    id: uuid.UUID
    name: str
    slug: str | None = None
    founder_score: float | None = None
    tier: float = 0.0
    tier_match: str | None = None
    affiliations: list[str] = field(default_factory=list)
    self_reported: bool = False
    profile_url: str | None = None
    is_demo: bool = False


@dataclass
class EvidenceFact:
    """One piece of formation evidence (EvidenceItem-shaped once rendered)."""

    kind: str
    title: str
    detail: str | None = None
    source_url: str | None = None
    source_type: str | None = None
    event_at: datetime | None = None
    signal_id: uuid.UUID | None = None

    def item(self) -> dict[str, Any]:
        return {
            "signal_id": str(self.signal_id) if self.signal_id else None,
            "title": self.title,
            "detail": self.detail,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "event_at": self.event_at.isoformat() if self.event_at else None,
        }


@dataclass
class CandidateInput:
    kind: str  # team | person
    anchor_type: str  # company | person
    anchor_id: uuid.UUID
    anchor_name: str
    anchor_slug: str
    created: date | None
    first_seen_at: datetime | None
    members: list[MemberInfo]
    metrics: dict[str, float | None]  # commits_4w, stars_30d, new_contributors_30d, releases_90d / person proxies
    facts: list[EvidenceFact]
    signals: list[SignalLite]
    attention: dict[str, Any] | None  # {technical, attention, gap, coverage}; attention None = unmeasured
    thesis_fit: float
    theme: str | None = None
    subtopic: str | None = None
    convergence: ConvergenceResult | None = None
    latest_activity_at: datetime | None = None
    is_demo: bool = False
    joined: dict[uuid.UUID, datetime | None] | None = None  # team: member -> first commit on the anchor


@dataclass
class Scored:
    inp: CandidateInput
    score: float
    components: dict[str, dict[str, Any]]
    families: list[str]
    independent_signals: int
    tier: str | None
    label: str | None
    why_now: list[dict[str, Any]]
    title: str


# ── Pure scoring ─────────────────────────────────────────────────────────────


def _component(key: str, value: float, explanation: str, raw: dict[str, Any] | None = None,
               evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    mx = FORMATION_MAX[key]
    return {
        "key": key,
        "label": COMPONENT_LABELS[key],
        "value": round(min(max(value, 0.0), mx), 2),
        "max": mx,
        "explanation": explanation,
        "raw": raw or {},
        "evidence": evidence or [],
    }


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def founder_quality(members: Sequence[MemberInfo], kind: str = "team") -> dict[str, Any]:
    scored = sorted((m for m in members if m.founder_score is not None), key=lambda m: (-m.founder_score, m.name))
    top = scored[:3]
    best_tier = max((m.tier for m in members), default=0.0)
    tier_member = next((m for m in sorted(members, key=lambda m: (-m.tier, m.name)) if m.tier > 0), None)
    if top:
        ws = TOP_FOUNDER_WEIGHTS[: len(top)]
        mean = sum(w * m.founder_score for w, m in zip(ws, top)) / sum(ws)  # type: ignore[operator]
    else:
        mean = 0.0
    value = FORMATION_MAX["founder_quality"] * (FQ_SCORE_SHARE * min(max(mean / 100, 0), 1)
                                                + FQ_TIER_SHARE * best_tier)
    parts = []
    if top:
        who = "Founder Score" if kind == "person" else f"Top-{len(top)} member Founder Scores"
        parts.append(f"{who} {', '.join(str(round(m.founder_score)) for m in top)}"  # type: ignore[arg-type]
                     f" (weighted {round(mean)})")
    else:
        parts.append("No member Founder Scores computed yet")
    if tier_member is not None:
        tier = "tier-1" if tier_member.tier >= 1.0 else "tier-2"
        sr = ", self-reported" if tier_member.self_reported else ""
        parts.append(f"{tier_member.name} lists {tier_member.tier_match} ({tier}{sr})")
    else:
        parts.append("no member affiliation on the thesis tier lists")
    evidence = []
    for m in (top or list(members)[:3]):
        aff = f"; affiliation {m.tier_match} ({'self-reported' if m.self_reported else 'listed'})" \
            if m.tier_match else ""
        fs = f"Founder Score {round(m.founder_score)}" if m.founder_score is not None else "Founder Score n/a"
        evidence.append({"signal_id": None, "title": m.name, "detail": fs + aff,
                         "source_type": "github" if (m.profile_url or "").startswith("https://github.com") else None,
                         "source_url": m.profile_url, "event_at": None})
    raw = {"founder_scores": [{"person_id": str(m.id), "name": m.name, "founder_score": round(m.founder_score, 1)}
                              for m in top],  # type: ignore[arg-type]
           "weighted_mean": round(mean, 2), "best_tier": best_tier,
           "tier_match": tier_member.tier_match if tier_member else None,
           "tier_self_reported": bool(tier_member and tier_member.self_reported)}
    return _component("founder_quality", value, "; ".join(parts) + ".", raw, evidence)


def team_convergence(conv: ConvergenceResult | None, kind: str) -> dict[str, Any]:
    mx = FORMATION_MAX["team_convergence"]
    if conv is None:
        expl = ("Single person — no team linked yet, so no convergence credit."
                if kind == "person" else "Convergence not computed (anchor creation date unknown).")
        return _component("team_convergence", 0.0, expl, {"applicable": kind != "person"})
    evidence = []
    for t in sorted(conv.ties, key=lambda t: (t.at is None, -(t.at.timestamp() if t.at else 0)))[:8]:
        for e in t.evidence[:1]:
            evidence.append({"signal_id": None, "title": e.get("title") or t.detail, "detail": e.get("detail"),
                             "source_type": e.get("source_type"), "source_url": e.get("source_url"),
                             "event_at": e.get("event_at")})
    raw = {k: v for k, v in conv.to_json().items() if k != "explanation"}
    return _component("team_convergence", mx * conv.score,
                      f"Convergence {conv.strength} ({conv.score:.2f}): {conv.explanation}", raw, evidence)


def tech_row(metrics: dict[str, float | None], kind: str) -> dict[str, float | None]:
    """Weekly-rate log features; None = unmeasured (never coerced to 0)."""
    def lv(v: float | None, per_days: float) -> float | None:
        return None if v is None else math.log1p(max(v, 0.0) * 7 / per_days)

    if kind == "person":
        return {"commits": lv(metrics.get("commits_30d"), 30), "stars": lv(metrics.get("stars_30d"), 30),
                "contributors": None, "releases": None}
    rel = metrics.get("releases_90d")
    return {
        "commits": lv(metrics.get("commits_4w"), 28),
        "stars": lv(metrics.get("stars_30d"), 30),
        "contributors": lv(metrics.get("new_contributors_30d"), 30),
        "releases": None if rel is None else math.log1p(max(rel, 0.0)),
    }


def percentile(values: Sequence[float], x: float | None, anchor_mid: float, cohort_min: int = COHORT_MIN
               ) -> float | None:
    """Empirical percentile in ``values`` (sorted), blended with an absolute anchor for small cohorts."""
    if x is None:
        return None
    if x <= 0:
        return 0.0
    p_abs = x / (x + anchor_mid)
    n = len(values)
    if n == 0:
        return p_abs
    lo, hi = bisect_left(values, x), bisect_right(values, x)
    emp = (lo + 0.5 * (hi - lo)) / n
    if n >= cohort_min:
        return emp
    a = n / cohort_min
    return a * emp + (1 - a) * p_abs


def build_tech_cohort(rows: Iterable[dict[str, float | None]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for k, v in r.items():
            if v is not None:
                out[k].append(v)
    return {k: sorted(v) for k, v in out.items()}


def technical_activity(inp: CandidateInput, cohort: dict[str, list[float]]) -> dict[str, Any]:
    mx = FORMATION_MAX["technical_activity"]
    row = tech_row(inp.metrics, inp.kind)
    pcts = {k: p for k, v in row.items() if (p := percentile(cohort.get(k, []), v, TECH_ANCHORS[k])) is not None}
    raw: dict[str, Any] = {"metrics": inp.metrics, "percentiles": {k: round(v, 4) for k, v in pcts.items()},
                           "cohort_size": max((len(v) for v in cohort.values()), default=0)}
    gh = [s for s in inp.signals if s.source_type == "github"]
    evidence = [s.evidence() for s in sorted(gh, key=lambda s: s.event_at, reverse=True)[:4]]
    if not pcts:
        raw["insufficient_history"] = True
        return _component("technical_activity", 0.0,
                          "Insufficient history — no commit, star, contributor or release series measured yet, "
                          "so no activity credit.", raw, evidence)
    blend = sum(TECH_WEIGHTS[k] * v for k, v in pcts.items()) / sum(TECH_WEIGHTS[k] for k in pcts)
    raw["blend"] = round(blend, 4)
    m = inp.metrics
    bits = []
    if inp.kind == "person":
        if m.get("commits_30d") is not None:
            bits.append(f"~{round(m['commits_30d'])} commits in 30 days (key-repo proxy)")
        if m.get("stars_30d") is not None:
            bits.append(f"+{round(m['stars_30d'])} stars in 30 days on key repos")
    else:
        if m.get("commits_4w") is not None:
            bits.append(f"{round(m['commits_4w'])} commits in 4 weeks")
        if m.get("stars_30d") is not None:
            bits.append(f"+{round(m['stars_30d'])} stars in 30 days")
        if m.get("new_contributors_30d") is not None:
            bits.append(f"{round(m['new_contributors_30d'])} new contributors in 30 days")
        if m.get("releases_90d") is not None:
            bits.append(f"{round(m['releases_90d'])} releases in 90 days")
    missing = [k for k in TECH_WEIGHTS if k not in pcts and not (inp.kind == "person" and k in ("contributors",
                                                                                                 "releases"))]
    expl = f"Activity at the {round(blend * 100)}th percentile of formation candidates: {'; '.join(bits)}"
    if missing:
        expl += f" ({', '.join(missing)} not measured)"
    return _component("technical_activity", mx * blend, expl + ".", raw, evidence)


def formation_evidence(facts: Sequence[EvidenceFact]) -> dict[str, Any]:
    mx = FORMATION_MAX["formation_evidence"]
    by_kind: dict[str, list[EvidenceFact]] = defaultdict(list)
    for f in facts:
        by_kind[f.kind].append(f)
    kinds = sorted(by_kind)
    k = len(kinds)
    value = mx * (1 - math.exp(-k / EVIDENCE_SATURATION))
    evidence = []
    for kd in kinds:
        best = sorted(by_kind[kd], key=lambda f: (f.event_at is None, -(f.event_at.timestamp() if f.event_at else 0)))
        evidence.extend(f.item() for f in best[:2])
    if k:
        expl = (f"{number_word(k, True)} distinct kind{'s' if k != 1 else ''} of formation evidence: "
                f"{', '.join(EVIDENCE_KIND_LABELS.get(x, x) for x in kinds)}.")
    else:
        expl = "No formation evidence beyond the shared anchor."
    return _component("formation_evidence", value, expl, {"kinds": kinds, "count": k}, evidence)


def novelty_attention(inp: CandidateInput, today: date) -> dict[str, Any]:
    first = _aware(inp.first_seen_at)
    age = None if first is None else max((datetime.combine(today, time.max, tzinfo=UTC) - first).days, 0)
    nov = 0.0 if age is None else NOVELTY_POINTS * math.exp(-age / NOVELTY_DECAY_DAYS)
    att = inp.attention or {}
    measured = att.get("attention") is not None
    gap = float(att.get("gap") or 0.0) if measured else None
    att_pts = ATTENTION_POINTS * min(max((gap or 0.0) / ATTENTION_FULL_GAP, 0.0), 1.0) if measured else 0.0
    parts = [f"First seen {age} day{'s' if age != 1 else ''} ago" if age is not None else "First-seen date unknown"]
    if measured:
        parts.append(f"technical {round(att.get('technical') or 0)} vs public attention "
                     f"{round(att['attention'])} percentile (gap {round(gap or 0)})")
    else:
        parts.append("public attention not measured, so no attention-gap credit")
    raw = {"first_seen_days": age, "novelty_points": round(nov, 2), "attention_measured": measured,
           "attention": att or None, "attention_points": round(att_pts, 2)}
    return _component("novelty_attention", nov + att_pts, "; ".join(parts) + ".", raw)


def family_of(source_type: str | None) -> str | None:
    return FORMATION_FAMILY.get(source_type or "")


def families_of(signals: Iterable[SignalLite], facts: Iterable[EvidenceFact],
                ties: Iterable[Any] = ()) -> tuple[list[str], int]:
    """(independent source families, distinct (family, kind) signals) of the evidence attached.

    ``ties`` are convergence prior ties; a dated one (a paper co-authored, a repository worked on
    together before the anchor) is evidence from its own source family."""
    pairs: set[tuple[str, str]] = set()
    for s in signals:
        fam = family_of(s.source_type)
        if fam:
            pairs.add((fam, s.signal_type))
    for f in facts:
        if f.event_at is None:
            continue  # undated listings (a website on a profile) are not independent events
        fam = family_of(f.source_type)
        if fam:
            pairs.add((fam, f.kind))
    for t in ties:
        if getattr(t, "at", None) is None:
            continue  # undated (self-reported shared affiliation): no independent event
        for e in getattr(t, "evidence", None) or []:
            fam = family_of(e.get("source_type"))
            if fam:
                pairs.add((fam, f"prior:{t.kind}"))
    fams = sorted({f for f, _ in pairs})
    return fams, len(pairs)


def tier_for(score: float, n_families: int, convergence_strength: str | None = None) -> str | None:
    """HIGH needs score, breadth (>= 3 families) *and* a team that already worked together (>= MEDIUM);
    EMERGING needs >= 2 families; a single-family candidate is at most an EARLY SIGNAL."""
    if (score >= TIER_HIGH and n_families >= HIGH_MIN_FAMILIES
            and (convergence_strength or "LOW") in HIGH_MIN_CONVERGENCE):
        return "high"
    if score >= TIER_EMERGING and n_families >= EMERGING_MIN_FAMILIES:
        return "emerging"
    if score >= TIER_EARLY:
        return "early"
    return None


def label_for(kind: str, tier: str | None) -> str | None:
    if tier is None:
        return None
    if kind == "person":
        return LABEL_PERSON
    return LABEL_TEAM if tier in ("high", "emerging") else LABEL_SIGNALS


def title_for(inp: CandidateInput, tier: str | None) -> str:
    if inp.kind == "person":
        return f"{inp.anchor_name} — possible formation"
    if tier in ("high", "emerging"):
        return f"Emerging team around {inp.anchor_name}"
    return f"Formation signals around {inp.anchor_name}"


def why_now(components: dict[str, dict[str, Any]], signals: Sequence[SignalLite]) -> list[dict[str, Any]]:
    """Top dated evidence, newest first (deduplicated by title)."""
    items: list[dict[str, Any]] = []
    for key in ("formation_evidence", "team_convergence", "technical_activity"):
        items.extend(components.get(key, {}).get("evidence") or [])
    items.extend(s.evidence() for s in signals)
    dated = [i for i in items if i.get("event_at")]
    dated.sort(key=lambda i: str(i["event_at"]), reverse=True)
    seen: set[str] = set()
    out = []
    for i in dated:
        if i["title"] in seen:
            continue
        seen.add(i["title"])
        out.append({k: i.get(k) for k in ("signal_id", "title", "detail", "source_type", "source_url", "event_at")})
        if len(out) >= WHY_NOW_MAX:
            break
    return out


def score_candidate(inp: CandidateInput, cohort: dict[str, list[float]], today: date) -> Scored:
    comps = {
        "founder_quality": founder_quality(inp.members, inp.kind),
        "team_convergence": team_convergence(inp.convergence, inp.kind),
        "technical_activity": technical_activity(inp, cohort),
        "formation_evidence": formation_evidence(inp.facts),
        "novelty_attention": novelty_attention(inp, today),
    }
    total = round(sum(c["value"] for c in comps.values()), 2)
    conv = inp.convergence
    fams, independent = families_of(inp.signals, inp.facts, conv.ties if conv else ())
    tier = tier_for(total, len(fams), conv.strength if conv else None)
    return Scored(
        inp=inp, score=total, components=comps, families=fams, independent_signals=independent, tier=tier,
        label=label_for(inp.kind, tier), why_now=why_now(comps, inp.signals), title=title_for(inp, tier),
    )


# ── Gathering (DB) ───────────────────────────────────────────────────────────


def _display(entity: Any) -> str:
    try:
        from radar.services.presenters import display_name

        return display_name(entity).name
    except Exception:  # noqa: BLE001 — presenters unavailable: fall back to the raw name
        return getattr(entity, "name", None) or getattr(entity, "slug", "")


def _end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def _fit(theme_scores: dict[str, float], topic_scores: dict[str, float], thesis: Thesis
         ) -> tuple[float, str | None, str | None]:
    best, best_theme = 0.0, None
    for theme, score in theme_scores.items():
        t = thesis.themes.get(theme)
        if t is None:
            continue
        v = t.weight * min(max(score, 0.0), 1.0)
        if v > best:
            best, best_theme = v, theme
    sub = None
    if best_theme:
        subs = [k for k in topic_scores if k.startswith(best_theme + ".")]
        sub = max(subs, key=lambda k: (topic_scores[k], k)) if subs else None
    return best, best_theme, sub


def _is_bot(p: Person) -> bool:
    h = (p.github_handle or "").lower()
    return h.endswith("[bot]") or h.endswith("-bot") or h.endswith("bot]")


def _is_org_account(p: Person, own_keys: set[str]) -> bool:
    """A user account named after the organization itself (a brand account), not a team member."""
    return bool(own_keys) and (org_key(p.name) in own_keys or org_key(p.github_handle or "") in own_keys)


def _member_info(p: Person, snap: ScoreSnapshot | None, thesis: Thesis) -> MemberInfo:
    affs = list(dict.fromkeys(a.strip() for a in [p.current_company, *(p.prior_affiliations or [])] if a and a.strip()))
    tier, match = 0.0, None
    for a in affs:
        t = thesis.affiliation_tier(a)
        if t > tier:
            tier, match = t, thesis.matched_affiliation(a)
    attrs = p.attrs or {}
    return MemberInfo(
        id=p.id, name=_display(p), slug=p.slug,
        founder_score=float(snap.score) if snap is not None else None,
        tier=tier, tier_match=match, affiliations=affs,
        self_reported=bool(attrs.get("self_reported")),
        profile_url=f"https://github.com/{p.github_handle}" if p.github_handle else (p.personal_site or p.linkedin_url),
        is_demo=bool(p.is_demo),
    )


def _signal_fact(s: SignalLite, kind: str) -> EvidenceFact:
    return EvidenceFact(kind=kind, title=s.title, source_url=s.source_url, source_type=s.source_type,
                        event_at=s.event_at, signal_id=s.id)


def _attention_of(snap: ScoreSnapshot | None, kind: str) -> dict[str, Any] | None:
    if snap is None:
        return None
    if kind == "person":
        raw = ((snap.components or {}).get("attention_gap") or {}).get("raw") or {}
        return {"technical": raw.get("technical"), "attention": raw.get("attention"), "gap": raw.get("gap"),
                "coverage": raw.get("coverage")}
    att = snap.attention or {}
    return {"technical": att.get("technical"), "attention": att.get("attention"), "gap": att.get("gap"),
            "coverage": att.get("coverage")}


@dataclass
class _Ctx:
    session: Session
    day: date
    thesis: Thesis
    index: SignalIndex
    snaps: dict[uuid.UUID, ScoreSnapshot]
    # Filled by gather_teams: person -> young organizations (company id, name, created) they are linked to.
    young_links: dict[uuid.UUID, list[tuple[uuid.UUID, str, date]]] = field(default_factory=dict)


def _company_created(c: Company, repos: Sequence[Repository]) -> date | None:
    attrs = c.attrs or {}
    repo_dates = [r.gh_created_at for r in repos]
    if attrs.get("young_org_basis") == "first_public_repo_created_at":
        return org_created_date(None, repo_dates)
    founded = c.founded_date
    if founded is None and attrs.get("gh_org_created_at"):
        try:
            founded = datetime.fromisoformat(str(attrs["gh_org_created_at"]).replace("Z", "+00:00")).date()
        except ValueError:
            founded = None
    return org_created_date(founded, repo_dates)


def gather_teams(ctx: _Ctx) -> list[CandidateInput]:
    from radar.providers.domains import is_academic_domain

    s, day, thesis = ctx.session, ctx.day, ctx.thesis
    companies = [c for c in s.scalars(select(Company).where(Company.dismissed.is_(False)))
                 if c.kind in TEAM_KINDS and (c.stage or "unknown") not in EXCLUDED_STAGES
                 and not (c.domain and is_academic_domain(c.domain))
                 and (c.status or "active") == "active"
                 and c.first_seen_at <= _end(day)]
    if not companies:
        return []
    cids = [c.id for c in companies]
    repos_by: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in s.scalars(select(Repository).where(Repository.company_id.in_(cids))):
        repos_by[r.company_id].append(r)
    young: list[tuple[Company, date]] = []
    for c in companies:
        created = _company_created(c, repos_by.get(c.id, []))
        if created is not None and 0 <= (day - created).days <= YOUNG_DAYS:
            young.append((c, created))
    if not young:
        return []
    yids = [c.id for c, _ in young]
    linked: dict[uuid.UUID, dict[uuid.UUID, float]] = defaultdict(dict)  # company -> person -> commits
    first_commit: dict[uuid.UUID, dict[uuid.UUID, datetime | None]] = defaultdict(dict)  # company -> person -> first
    listed: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for cp in s.scalars(select(CompanyPerson).where(CompanyPerson.company_id.in_(yids))):
        if cp.end_at is not None and cp.end_at < day:
            continue
        if cp.role != "contributor" or cp.is_founder:
            listed[cp.company_id].add(cp.person_id)
    repo_company = {r.id: cid for cid, rs in repos_by.items() for r in rs}
    young_repo_ids = [rid for rid, cid in repo_company.items() if cid in set(yids)]
    if young_repo_ids:
        for rc in s.scalars(select(RepoContributor).where(RepoContributor.repository_id.in_(young_repo_ids))):
            cid = repo_company[rc.repository_id]
            linked[cid][rc.person_id] = linked[cid].get(rc.person_id, 0) + float(rc.contributions or 0)
            fc, prev = _aware(rc.first_commit_at), first_commit[cid].get(rc.person_id)
            first_commit[cid][rc.person_id] = min(d for d in (fc, prev) if d) if (fc or prev) else None
    pids = {p for cid in yids for p in [*listed.get(cid, set()), *linked.get(cid, {})]}
    people = {p.id: p for p in s.scalars(select(Person).where(Person.id.in_(list(pids))))} if pids else {}
    metrics = {cm.company_id: cm.metrics or {} for cm in
               s.scalars(select(CompanyMetric).where(CompanyMetric.company_id.in_(yids)))}
    jobs_by: dict[uuid.UUID, list[Job]] = defaultdict(list)
    for j in s.scalars(select(Job).where(Job.company_id.in_(yids))):
        jobs_by[j.company_id].append(j)
    topics = _topics(s, yids + young_repo_ids)
    for c, created in young:
        for pid in [*listed.get(c.id, set()), *(p for p, n in linked.get(c.id, {}).items() if n >= MIN_MEMBER_COMMITS)]:
            if (c.id, _display(c), created) not in ctx.young_links.setdefault(pid, []):
                ctx.young_links[pid].append((c.id, _display(c), created))

    out: list[CandidateInput] = []
    for c, created in young:
        commits = linked.get(c.id, {})
        own_keys = {k for k in (org_key(c.name), org_key(c.github_org or "")) if k}
        member_ids = [pid for pid in dict.fromkeys([*sorted(listed.get(c.id, set()), key=str),
                                                    *sorted(commits, key=lambda p: (-commits[p], str(p)))])
                      if pid in people and not people[pid].dismissed and not _is_bot(people[pid])
                      and not _is_org_account(people[pid], own_keys)
                      and (pid in listed.get(c.id, set()) or commits.get(pid, 0) >= MIN_MEMBER_COMMITS)]
        if len(member_ids) < MIN_MEMBERS:
            continue
        repos = repos_by.get(c.id, [])
        theme_rows = [row for eid in (c.id, *(r.id for r in repos)) for row in topics.get(eid, [])]
        topic_scores, theme_scores, _ = _merge_topics(theme_rows)
        fit, theme, sub = _fit(theme_scores, topic_scores, thesis)
        if fit < THESIS_GATE:
            continue
        members = [_member_info(people[pid], ctx.snaps.get(pid), thesis) for pid in member_ids]
        members.sort(key=lambda m: (-(m.founder_score or -1), m.name))
        members = members[:MAX_MEMBERS]
        name = _display(c)
        anchor_sigs = ctx.index.for_entities([c.id, *(r.id for r in repos)])
        mids = {m.id for m in members}
        member_sigs = member_signals(ctx.index.for_entities(list(mids)), mids, _anchor_terms(c, name))
        sigs = sorted({x.id: x for x in [*anchor_sigs, *member_sigs]}.values(), key=lambda x: x.event_at,
                      reverse=True)
        facts = _team_facts(c, created, repos, jobs_by.get(c.id, []), anchor_sigs, member_sigs, mids, day)
        joined = {m.id: first_commit.get(c.id, {}).get(m.id) for m in members}
        founding = _founding_fact(c, name, created, joined, {m.id: m.name for m in members})
        if founding is not None:
            facts.append(founding)
        m = metrics.get(c.id, {})
        pushed = [r.pushed_at for r in repos if r.pushed_at]
        latest = max([*(x.event_at for x in sigs), *(_aware(p) for p in pushed)], default=None)
        out.append(CandidateInput(
            kind="team", anchor_type="company", anchor_id=c.id, anchor_name=name, anchor_slug=c.slug,
            created=created, first_seen_at=c.first_seen_at, members=members,
            metrics={k: m.get(k) for k in ("commits_4w", "stars_30d", "new_contributors_30d", "releases_90d")},
            facts=facts, signals=sigs, attention=_attention_of(ctx.snaps.get(c.id), "team"),
            thesis_fit=round(fit, 4), theme=theme, subtopic=sub, latest_activity_at=latest,
            is_demo=bool(c.is_demo or any(mm.is_demo for mm in members)), joined=joined,
        ))
    _attach_convergence(ctx, out, {c.id: c for c, _ in young}, repos_by)
    return out


def _anchor_terms(c: Company, name: str) -> set[str]:
    return {k for k in (org_key(c.name), org_key(c.github_org or ""), org_key(name),
                        org_key((c.domain or "").split(".")[0])) if k and len(k) >= 3}


def member_signals(sigs: Iterable[SignalLite], member_ids: set[uuid.UUID], anchor_terms: set[str]
                   ) -> list[SignalLite]:
    """Of the signals indexed under team members, the ones a team candidate may cite: the member's *own*
    formation signals (affiliation/bio change, formation language/post), and the member's own launch
    post when it names the anchor. Activity on a member's unrelated repositories is not evidence about
    this team (and signals about the anchor itself are already anchor signals)."""
    out = []
    for x in sigs:
        if x.entity_id not in member_ids:
            continue
        if x.signal_type in MEMBER_OWN_TYPES:
            out.append(x)
        elif x.signal_type == "x_launch_post" and any(
                f" {t} " in f" {org_key(x.title)} " for t in anchor_terms):
            out.append(x)
    return out


def _founding_fact(c: Company, name: str, created: date, joined: dict[uuid.UUID, datetime | None],
                   names: dict[uuid.UUID, str]) -> EvidenceFact | None:
    """>= 2 members whose first commit on the anchor falls within JOIN_WINDOW_DAYS of its creation."""
    founders, _ = founding_members(joined, created, JOIN_WINDOW_DAYS)
    if len(founders) < 2:
        return None
    last = max(d for f in founders if (d := joined[f]) is not None)
    who = sorted(names[f] for f in founders)
    shown = ", ".join(who[:4]) + (f" and {len(who) - 4} more" if len(who) > 4 else "")
    url = f"https://github.com/{c.github_org}" if c.github_org else c.website
    return EvidenceFact("founding_team",
                        f"{number_word(len(founders), True)} members committed to {name} within "
                        f"{JOIN_WINDOW_DAYS} days of its creation",
                        detail=f"{shown}; first commits by {last.date().isoformat()} (created {created.isoformat()})",
                        source_url=url, source_type="github", event_at=last)


def _team_facts(c: Company, created: date, repos: Sequence[Repository], jobs: Sequence[Job],
                anchor_sigs: Sequence[SignalLite], member_sigs: Sequence[SignalLite], member_ids: set[uuid.UUID],
                day: date) -> list[EvidenceFact]:
    facts: list[EvidenceFact] = []
    attrs = c.attrs or {}
    basis = attrs.get("young_org_basis") or ("github_org_created_at" if c.github_org else "earliest record")
    org_url = f"https://github.com/{c.github_org}" if c.github_org else c.website
    what = ("first public repository" if basis == "first_public_repo_created_at"
            else ("GitHub organization" if c.github_org else "organization"))
    facts.append(EvidenceFact("org_created", f"{what.capitalize()} {c.github_org or c.name} created "
                                             f"{created.isoformat()} ({(day - created).days} days ago)",
                              detail=f"Basis: {basis}", source_url=org_url,
                              source_type="github" if c.github_org else None,
                              event_at=datetime.combine(created, time(12), tzinfo=UTC)))
    for x in anchor_sigs:
        if x.signal_type in ORG_TYPES:
            facts.append(_signal_fact(x, "org_created"))
        elif x.signal_type == "young_org_team":
            facts.append(_signal_fact(x, "working_team"))
        elif x.signal_type in LAUNCH_TYPES:
            facts.append(_signal_fact(x, "launch"))
        elif x.signal_type in LANGUAGE_TYPES:
            facts.append(_signal_fact(x, "formation_language"))
        elif x.signal_type == "job_posted":
            facts.append(_signal_fact(x, "first_job"))
    for x in member_sigs:
        if x.entity_id not in member_ids:
            continue
        if x.signal_type in CHANGE_TYPES:
            facts.append(_signal_fact(x, "affiliation_change"))
        elif x.signal_type in LANGUAGE_TYPES:
            facts.append(_signal_fact(x, "formation_language"))
        elif x.signal_type == "x_launch_post":
            facts.append(_signal_fact(x, "launch"))
    if jobs:
        first = min(jobs, key=lambda j: (j.posted_at or j.first_seen_at, j.title))
        at = _aware(first.posted_at or first.first_seen_at)
        if at is not None and (day - at.date()).days <= YOUNG_DAYS:
            facts.append(EvidenceFact("first_job", f"First job posting: {first.title}",
                                      detail=f"{len(jobs)} posting{'s' if len(jobs) != 1 else ''} seen",
                                      source_url=first.url, source_type="jobs", event_at=at))
    if c.domain or c.website:
        site = c.website or f"https://{c.domain}"
        facts.append(EvidenceFact("website", f"Website listed: {c.domain or site}",
                                  detail="Listed on the organization's public profile" if c.github_org else None,
                                  source_url=site, source_type="website", event_at=None))
    return facts


def _attach_convergence(ctx: _Ctx, cands: list[CandidateInput], companies: dict[uuid.UUID, Company],
                        repos_by: dict[uuid.UUID, list[Repository]]) -> None:
    rels = load_pair_relationships(ctx.session, [[m.id for m in c.members] for c in cands])
    for cand in cands:
        c = companies[cand.anchor_id]
        keys = {org_key(c.name), org_key(c.github_org or ""), org_key(cand.anchor_name)}
        if c.domain:
            keys.add(org_key(c.domain.split(".")[0]))
        fams, _ = families_of(cand.signals, cand.facts)
        cand.convergence = convergence(
            [Member(m.id, m.name) for m in cand.members],
            rels.get(frozenset(m.id for m in cand.members), []),
            cand.created,  # type: ignore[arg-type]
            anchor_name=cand.anchor_name, families=fams,
            anchor_repo_ids={str(r.id) for r in repos_by.get(c.id, [])},
            anchor_org_keys={k for k in keys if k},
            joined=cand.joined,
        )


def _is_founder_phrase(phrase: str) -> bool:
    return "founder" in phrase.lower()


def generic_founder_only(facts: Sequence[EvidenceFact], own: Sequence[SignalLite],
                         phrases: dict[uuid.UUID, list[str]]) -> bool:
    """True when the person's only formation evidence is a self-description containing "founder"
    (a bio line, not an event): no affiliation change, no formation post, no other phrase, no young org."""
    if any(f.kind != "formation_language" for f in facts):
        return False
    lang = [x for x in own if x.signal_type in LANGUAGE_TYPES]
    if not lang or any(x.signal_type != "formation_language" for x in lang):
        return False
    for x in lang:
        ph = phrases.get(x.id) or []
        if ph:
            if not all(_is_founder_phrase(p) for p in ph):
                return False
        elif "founder" not in x.title.lower():
            return False
    return True


def _established_employer(s: Session, p: Person, young_ids: set[uuid.UUID]) -> Company | None:
    """An established organization the person is at: linked (company_people) or named as current company,
    and not young, with > ESTABLISHED_FOLLOWERS GitHub followers or an established stage."""
    keys = {k for k in (org_key(p.current_company),) if k}
    linked = set(s.scalars(select(CompanyPerson.company_id).where(CompanyPerson.person_id == p.id,
                                                                  CompanyPerson.end_at.is_(None))))
    if not keys and not linked:
        return None
    for c in s.scalars(select(Company)):
        if c.id not in linked and not ({org_key(c.name), org_key(c.github_org or "")} & keys):
            continue
        if c.id in young_ids:
            continue
        followers = (c.attrs or {}).get("followers") or 0
        if (c.stage or "unknown") in EXCLUDED_STAGES or followers > ESTABLISHED_FOLLOWERS:
            return c
    return None


def _person_scope(s: Session, people: dict[uuid.UUID, Person]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Entities a person candidate may cite signals about: the person and repositories they own."""
    handles = {p.github_handle.lower(): pid for pid, p in people.items() if p.github_handle}
    out: dict[uuid.UUID, set[uuid.UUID]] = {pid: {pid} for pid in people}
    if handles:
        for rid, owner in s.execute(select(Repository.id, Repository.owner)
                                    .where(func.lower(Repository.owner).in_(list(handles)))):
            out[handles[(owner or "").lower()]].add(rid)
    return out


def gather_people(ctx: _Ctx, exclude: set[uuid.UUID]) -> list[CandidateInput]:
    s, day, thesis = ctx.session, ctx.day, ctx.thesis
    lo = datetime.combine(day - timedelta(days=PERSON_SIGNAL_DAYS), time.min, tzinfo=UTC)
    hi = _end(day)
    own: dict[uuid.UUID, list[SignalLite]] = defaultdict(list)
    for eid, sigs in ctx.index.by_entity.items():
        for x in sigs.values():
            if x.entity_id == eid and x.signal_type in FORMATION_SIGNAL_TYPES and lo <= x.event_at <= hi:
                own[eid].append(x)
    # Qualifying: the person's own formation language/post or affiliation change (bio edits alone do not).
    pids = {pid for pid, xs in own.items() if any(x.signal_type in PERSON_QUALIFYING_TYPES for x in xs)} - exclude
    if not pids:
        return []
    # Membership of a young-org team (signal on the org, people linked through signal_entities).
    team_sigs: dict[uuid.UUID, list[SignalLite]] = defaultdict(list)
    young_team = {x.id: x for sigs in ctx.index.by_entity.values() for x in sigs.values()
                  if x.signal_type == "young_org_team" and lo <= x.event_at <= hi}
    if young_team:
        for sid, eid, etype in s.execute(select(SignalEntity.signal_id, SignalEntity.entity_id, SignalEntity.entity_type)
                                         .where(SignalEntity.signal_id.in_(list(young_team)),
                                                SignalEntity.entity_id.in_(list(pids)))):
            if etype == "person":
                team_sigs[eid].append(young_team[sid])
    people = {p.id: p for p in s.scalars(select(Person).where(Person.id.in_(list(pids)), Person.dismissed.is_(False)))
              if p.first_seen_at <= hi and not _is_bot(p)}
    lang_ids = [x.id for pid in people for x in own.get(pid, []) if x.signal_type in LANGUAGE_TYPES]
    phrases: dict[uuid.UUID, list[str]] = {}
    if lang_ids:
        for sid, meta in s.execute(select(Signal.id, Signal.meta).where(Signal.id.in_(lang_ids))):
            phrases[sid] = [str(p) for p in ((meta or {}).get("phrases") or [])]
    scope = _person_scope(s, people)
    young_ids = {cid for links in ctx.young_links.values() for cid, _, _ in links}
    out = []
    for pid, p in sorted(people.items(), key=lambda kv: str(kv[0])):
        snap = ctx.snaps.get(pid)
        comps = (snap.components or {}) if snap else {}
        fit_raw = (comps.get("thesis_fit") or {}).get("raw") or {}
        theme_scores = fit_raw.get("theme_scores") or {}
        topic_scores: dict[str, float] = {}
        if fit_raw.get("topic"):
            topic_scores[fit_raw["topic"]] = float(theme_scores.get(fit_raw.get("theme"), 0.0))
        if not theme_scores:
            _, theme_scores, _ = _merge_topics(_topics(s, [pid]).get(pid, []))
        fit, theme, sub = _fit(theme_scores, topic_scores, thesis)
        young = ctx.young_links.get(pid, [])
        if fit < THESIS_GATE and not young and not team_sigs.get(pid):
            continue
        member = _member_info(p, snap, thesis)
        facts: list[EvidenceFact] = []
        for x in own.get(pid, []):
            facts.append(_signal_fact(x, "affiliation_change" if x.signal_type in CHANGE_TYPES
                                      else "formation_language"))
        for x in team_sigs.get(pid, []):
            facts.append(_signal_fact(x, "young_org"))
        for cid, cname, created in young:
            facts.append(EvidenceFact("young_org", f"Linked to {cname}, an organization created {created.isoformat()} "
                                                   f"({(day - created).days} days ago)",
                                      detail="Listed member or contributor with >= 3 commits",
                                      source_url=None, source_type="github",
                                      event_at=datetime.combine(created, time(12), tzinfo=UTC)))
        if generic_founder_only(facts, own.get(pid, []), phrases):
            emp = _established_employer(s, p, young_ids)
            if emp is not None:
                log.debug("formation: %s left out — generic founder bio at established %s", p.slug, emp.slug)
                continue
        allowed = scope.get(pid, {pid}) | {cid for cid, _, _ in young}
        cited = [x for x in ctx.index.for_entities([pid]) if x.entity_id in allowed
                 and (x.signal_type not in FORMATION_SIGNAL_TYPES or x.entity_id == pid)]
        for x in cited:
            if x.signal_type == "x_launch_post" and x.entity_id == pid:
                facts.append(_signal_fact(x, "launch"))
        tech_raw = (comps.get("technical_output") or {}).get("raw") or {}
        all_sigs = sorted({x.id: x for x in [*cited, *team_sigs.get(pid, [])]}.values(),
                          key=lambda x: x.event_at, reverse=True)
        out.append(CandidateInput(
            kind="person", anchor_type="person", anchor_id=pid, anchor_name=member.name, anchor_slug=p.slug,
            created=None, first_seen_at=p.first_seen_at, members=[member],
            metrics={"commits_30d": tech_raw.get("commits_30d_proxy"), "stars_30d": tech_raw.get("recent_repo_stars_30d")},
            facts=facts, signals=all_sigs,
            attention=_attention_of(snap, "person"), thesis_fit=round(fit, 4), theme=theme, subtopic=sub,
            latest_activity_at=max((x.event_at for x in all_sigs), default=None), is_demo=bool(p.is_demo),
        ))
    return out

def _snapshots(session: Session, day: date) -> dict[uuid.UUID, ScoreSnapshot]:
    """Latest score snapshot on or before ``day`` per entity."""
    out: dict[uuid.UUID, ScoreSnapshot] = {}
    q = (select(ScoreSnapshot).where(ScoreSnapshot.day <= day)
         .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc()).distinct(ScoreSnapshot.entity_id))
    for snap in session.scalars(q):
        out[snap.entity_id] = snap
    return out


def compute(session: Session, day: date | None = None, thesis: Thesis | None = None) -> list[Scored]:
    """Score every formation candidate for ``day`` (pure read; nothing persisted)."""
    day = day or clock.today()
    thesis = thesis or get_thesis()
    ctx = _Ctx(session, day, thesis, load_signal_index(session, day, PERSON_SIGNAL_DAYS), _snapshots(session, day))
    teams = gather_teams(ctx)
    in_team = {m.id for t in teams for m in t.members}
    people = gather_people(ctx, in_team)
    real_teams = [t for t in teams if not t.is_demo] or teams
    real_people = [p for p in people if not p.is_demo] or people
    team_cohort = build_tech_cohort(tech_row(t.metrics, "team") for t in real_teams)
    person_cohort = build_tech_cohort(tech_row(p.metrics, "person") for p in real_people)
    out = [score_candidate(t, team_cohort, day) for t in teams]
    out += [score_candidate(p, person_cohort, day) for p in people]
    out.sort(key=lambda x: (-x.score, x.inp.anchor_slug))
    return out


def _slug(sc: Scored) -> str:
    return f"{sc.inp.kind}-{sc.inp.anchor_slug}"[:220]


def run(session: Session, day: date | None = None, thesis: Thesis | None = None) -> dict[str, Any]:
    """Compute and persist formation candidates (upsert by anchor; delete those no longer qualifying)."""
    day = day or clock.today()
    scored = compute(session, day, thesis)
    keep = [x for x in scored if x.tier is not None]
    existing = {fc.anchor_id: fc for fc in session.scalars(select(FormationCandidate))}
    now = clock.now()
    keep_ids = {x.inp.anchor_id for x in keep}
    stale = [fc.id for aid, fc in existing.items() if aid not in keep_ids]
    if stale:
        session.execute(delete(FormationCandidate).where(FormationCandidate.id.in_(stale)))
        session.flush()
    for x in keep:
        fc = existing.get(x.inp.anchor_id)
        if fc is None:
            fc = FormationCandidate(id=uuid.uuid4(), anchor_id=x.inp.anchor_id, first_detected_at=now)
            session.add(fc)
        fc.slug = _slug(x)
        fc.kind = x.inp.kind
        fc.anchor_type = x.inp.anchor_type
        fc.title = x.title[:400]
        fc.theme = x.inp.theme
        fc.subtopic = x.inp.subtopic
        fc.member_ids = [m.id for m in x.inp.members]
        fc.score = x.score
        fc.tier = x.tier  # type: ignore[assignment]
        fc.label = x.label  # type: ignore[assignment]
        fc.components = x.components
        conv = x.inp.convergence.to_json() if x.inp.convergence else {
            "score": 0.0, "strength": "LOW", "explanation": "Single person — no team linked yet.",
            "relationship_ids": [], "prior_relationships": 0, "families": x.families, "kinds": []}
        fc.convergence = conv
        fc.signal_families = x.families
        fc.independent_signals = x.independent_signals
        fc.latest_activity_at = x.inp.latest_activity_at
        fc.why_now = x.why_now
        fc.is_demo = x.inp.is_demo
        fc.day = day
        fc.updated_at = now
    session.flush()
    counts = {t: sum(1 for x in keep if x.tier == t) for t in ("high", "emerging", "early")}
    summary = {"day": day.isoformat(), "considered": len(scored), "stored": len(keep), **counts,
               "teams": sum(1 for x in keep if x.inp.kind == "team"),
               "people": sum(1 for x in keep if x.inp.kind == "person"), "deleted": len(stale)}
    log.info("formation.run %s", summary)
    return summary


__all__ = ["FORMATION_MAX", "CandidateInput", "EvidenceFact", "MemberInfo", "Scored", "compute", "founder_quality",
           "formation_evidence", "label_for", "novelty_attention", "run", "score_candidate", "team_convergence",
           "technical_activity", "tier_for"]
