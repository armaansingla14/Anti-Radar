"""Founder Score (people, /100) and the company-level Founder Signal component (/20)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from radar.scoring.attention import AttentionResult, gap_points, gap_result
from radar.scoring.cohort import Cohort, ordinal
from radar.scoring.collision import CollisionResult, collision
from radar.scoring.features import (
    FORMATION_SIGNAL_TYPES,
    Component,
    EntityFeatures,
    PersonFeatures,
    SignalLite,
    top_signals,
)
from radar.scoring.momentum import lv
from radar.scoring.thesis_fit import thesis_fit
from radar.thesis.loader import Thesis

FOUNDER_MAX = {
    "technical_output": 25,
    "research": 20,
    "pedigree": 15,
    "formation": 20,
    "thesis_fit": 10,
    "attention_gap": 10,
}
TECH_OUTPUT_WEIGHTS = {"owned_stars": 0.40, "recent_stars": 0.35, "commits": 0.25}
PAPER_SATURATION = 3.0  # papers in 24 months at which research credit reaches ~63% of its paper share
TOP_FOUNDER_WEIGHT = 0.6


@dataclass
class FounderScore:
    components: list[Component]
    attention: AttentionResult
    collision: CollisionResult


def _log(v: float | None) -> float | None:
    return None if v is None else math.log1p(max(v, 0.0))


def hn_mentions_90d(signals: list[SignalLite], today: date) -> int:
    cutoff = datetime.combine(today - timedelta(days=90), time.min, tzinfo=UTC)
    return sum(1 for s in signals if s.source_type == "hackernews" and s.event_at >= cutoff)


def person_row(p: PersonFeatures, today: date) -> dict[str, float | None]:
    return {
        "owned_stars": _log(p.owned_repo_stars),
        "recent_stars": lv(p.recent_repo_stars_30d, 30),
        "commits": _log(p.commits_30d),
        "followers": _log(p.followers),
        "hn_mentions": _log(hn_mentions_90d(p.signals, today)),
    }


def technical_output(p: PersonFeatures, cohort: Cohort, today: date) -> tuple[Component, float | None]:
    """Returns the component and the raw weighted percentile blend (None if nothing measured)."""
    row = person_row(p, today)
    pcts = {k: v for k in TECH_OUTPUT_WEIGHTS if (v := cohort.pct(k, row[k])) is not None}
    mx = FOUNDER_MAX["technical_output"]
    raw = {
        "key_repo_stars_weighted": p.owned_repo_stars,
        "key_repos": p.key_repos,
        "recent_repo_stars_30d": p.recent_repo_stars_30d,
        "commits_30d_proxy": p.commits_30d,
        "percentiles": {k: round(v, 4) for k, v in pcts.items()},
    }
    evidence = top_signals(p.signals, lambda s: s.source_type == "github")
    if not pcts:
        return Component(
            "technical_output",
            "Technical Output",
            0.0,
            mx,
            "No owned or core-contributor repositories measured.",
            raw,
            evidence,
        ), None
    blend = sum(TECH_OUTPUT_WEIGHTS[k] * v for k, v in pcts.items()) / sum(
        TECH_OUTPUT_WEIGHTS[k] for k in pcts
    )
    raw["blend"] = round(blend, 4)
    if p.key_repos:
        stars = f"{int(p.owned_repo_stars or 0):,}"
        repos = ", ".join(p.key_repos[:2]) + (f" +{len(p.key_repos) - 2}" if len(p.key_repos) > 2 else "")
        expl = (f"Technical output at the {ordinal(blend * 100)} percentile — core contributor to {repos} "
                f"({stars} share-weighted stars).")
    else:
        expl = f"Technical output at the {ordinal(blend * 100)} percentile (no key repositories identified)."
    return Component("technical_output", "Technical Output", mx * blend, mx, expl, raw, evidence), blend


def research(p: PersonFeatures) -> Component:
    mx = FOUNDER_MAX["research"]
    n = max(p.papers_24mo, 0)
    code_share = min(p.papers_with_code, n) / n if n else 0.0
    r = 0.8 * (1 - math.exp(-n / PAPER_SATURATION)) + 0.2 * code_share
    expl = (
        f"{n} paper{'s' if n != 1 else ''} in 24 months, {p.papers_with_code} with code."
        if n
        else "No papers in the last 24 months."
    )
    raw = {"papers_24mo": n, "papers_with_code": p.papers_with_code}
    evidence = top_signals(p.signals, lambda s: s.source_type == "arxiv")
    return Component("research", "Research", mx * r, mx, expl, raw, evidence)


def pedigree(p: PersonFeatures) -> Component:
    mx = FOUNDER_MAX["pedigree"]
    basis = getattr(p, "tier_basis", None)
    login = getattr(p, "tier_org_login", None)
    public = list(getattr(p, "public_orgs", None) or [])
    if p.tier > 0 and p.tier_match:
        tier = "tier-1" if p.tier >= 1.0 else "tier-2"
        if basis == "public_github_org" and login:
            # Observed membership (GitHub API), not a claim of employment or its dates.
            expl = f"Public GitHub member of {login} ({p.tier_match}, {tier})."
        else:
            expl = f"Affiliation with {p.tier_match} ({tier})."
    elif p.affiliations or public:
        expl = "Known affiliations are not on the thesis tier lists."
    else:
        expl = "No affiliations known."
    raw = {"affiliations": p.affiliations, "tier": p.tier, "matched": p.tier_match,
           "basis": basis, "public_github_orgs": public, "org_login": login}
    return Component("pedigree", "Pedigree", mx * p.tier, mx, expl, raw)


def formation(p: PersonFeatures, today: date, thesis: Thesis) -> tuple[Component, CollisionResult]:
    mx = FOUNDER_MAX["formation"]
    # Only signals about this person: a co-contributor's bio change or a repo-level measurement
    # is not evidence that *this* person is forming something.
    col = collision(p.own_signals, today, thesis.scoring.collision)
    v = 0.5 * float(p.recent_transition) + 0.3 * col.score + 0.2 * float(p.joined_young_org)
    parts = []
    if p.recent_transition:
        parts.append("recent transition signal (affiliation/bio/formation language)")
    if p.joined_young_org:
        parts.append(f"joined a young organization ({', '.join(p.young_org_names[:2])})")
    parts.append(f"signal collision {col.score:.2f} ({col.summary})")
    raw = {
        "recent_transition": p.recent_transition,
        "joined_young_org": p.joined_young_org,
        "young_orgs": p.young_org_names,
        "collision": round(col.score, 4),
        "transition_signal_ids": [str(i) for i in p.transition_evidence],
    }
    evidence = top_signals(p.own_signals, lambda s: s.signal_type in FORMATION_SIGNAL_TYPES)
    expl = "; ".join(parts)
    return Component(
        "formation", "Formation Signal", mx * v, mx, expl[0].upper() + expl[1:] + ".", raw, evidence
    ), col


def person_attention(
    p: PersonFeatures, cohort: Cohort, tech_blend: float | None, today: date, thesis: Thesis
) -> tuple[Component, AttentionResult]:
    cfg = thesis.scoring.attention_gap
    mx = FOUNDER_MAX["attention_gap"]
    technical = 100 * (cohort.pct("tech_output", tech_blend) or 0.0)
    row = person_row(p, today)
    sources = []
    for key, label, val, note in (
        ("followers", "GitHub followers", p.followers, "GitHub followers: not collected"),
        ("hn_mentions", "Hacker News mentions (90d)", hn_mentions_90d(p.signals, today), None),
        ("x_followers", "X followers", None, "X: unavailable — no API token"),
    ):
        pv = cohort.pct(key, row.get(key))
        sources.append(
            {
                "key": key,
                "label": label,
                "available": pv is not None,
                "value": val,
                "percentile": round(100 * pv, 1) if pv is not None else None,
                "note": None if pv is not None else note,
            }
        )
    res = gap_result(technical, sources, cfg)
    value = gap_points(res, cfg, mx)
    tech = "not measured" if tech_blend is None else f"at the {ordinal(technical)} percentile"
    if res.attention is None:
        expl = f"Technical output {tech}; public attention not measured, so no gap is awarded."
    else:
        expl = (f"Technical output {tech} vs public attention at the {ordinal(res.attention)} percentile "
                f"({res.coverage} coverage).")
    raw = {
        "technical": round(technical, 1),
        "attention": None if res.attention is None else round(res.attention, 1),
        "gap": None if res.attention is None else round(res.gap, 1),
        "coverage": res.coverage,
        "flagged": res.flagged,
    }
    return Component("attention_gap", "Attention Gap", value, mx, expl, raw), res


def founder_score(
    p: PersonFeatures, cohort: Cohort, thesis: Thesis, prefs: dict[str, float], today: date
) -> FounderScore:
    """Six components summing to at most 100. ``cohort`` must contain 'tech_output' blends."""
    tech, blend = technical_output(p, cohort, today)
    form, col = formation(p, today, thesis)
    fit = thesis_fit(
        p.theme_scores,
        p.topic_scores,
        p.topic_evidence,
        thesis,
        prefs,
        stage=None,
        max_points=FOUNDER_MAX["thesis_fit"],
    )
    att, att_res = person_attention(p, cohort, blend, today, thesis)
    return FounderScore([tech, research(p), pedigree(p), form, fit, att], att_res, col)


def founder_signal(f: EntityFeatures, max_points: float = 20) -> Component:
    """Company component: weighted mean of linked founders' Founder Scores (top 0.6, rest share 0.4)."""
    scores = sorted(((s, pid) for s, pid in zip(f.founder_scores, f.founder_ids)), key=lambda t: -t[0])
    evidence = top_signals(
        f.signals, lambda s: s.signal_type in FORMATION_SIGNAL_TYPES or s.source_type == "arxiv"
    )
    if not scores:
        linked = (
            f" {f.linked_people} linked {'people are' if f.linked_people != 1 else 'person is'} not marked "
            f"as founder{'s' if f.linked_people != 1 else ''}, so no founder credit is given for them."
            if f.linked_people
            else ""
        )
        return Component(
            "founder_signal",
            "Founder Signal",
            0.0,
            max_points,
            "No founders identified yet — team unknown, so no founder credit is given." + linked,
            {"founders": [], "linked_people": f.linked_people},
            evidence,
        )
    if len(scores) == 1:
        mean = scores[0][0]
    else:
        rest = 1 - TOP_FOUNDER_WEIGHT
        mean = TOP_FOUNDER_WEIGHT * scores[0][0] + sum(s for s, _ in scores[1:]) * rest / (len(scores) - 1)
    value = max_points * min(max(mean / 100, 0.0), 1.0)
    raw = {
        "founders": [{"person_id": str(pid), "founder_score": round(s, 1)} for s, pid in scores],
        "weighted_mean": round(mean, 2),
    }
    expl = (
        f"{len(scores)} linked founder{'s' if len(scores) != 1 else ''}; top Founder Score "
        f"{round(scores[0][0])}, weighted mean {round(mean)}."
    )
    return Component("founder_signal", "Founder Signal", value, max_points, expl, raw, evidence)
