"""Stage 4: deterministic materiality rules M1–M11. No LLM ever sets materiality.

``classify(event, ctx, history)`` returns ``(level, rule_id, reason)``. Rules are evaluated in ``ORDER``; the first
rule whose condition holds decides the level, then M9 may cap MATERIAL to WATCH. M8 (competitor events) is applied
per affected portfolio company in ``impact.py``. Thresholds are module constants so the methodology document and
the code cannot disagree.

=====  ========================================================================================================
M1     RISK events (security incident, outage, lawsuit, recall) from the company or a registry → MATERIAL
       ("risk signal"; a third-party-only story is capped by M9).
M2     FINANCING (new round, acquisition, strategic investment) announced by the company or a primary source
       → MATERIAL.
M3     COMMERCIAL: a government contract of >= $1M → MATERIAL; a smaller or unpriced award → WATCH. A partnership
       or customer announced by the company naming a tracked company or a government agency → MATERIAL; other
       partnerships / customers / deployments → WATCH.
M4     PRODUCT launch or major release announced by the company → MATERIAL when novel (no launch of the company
       in the previous 90 days), else WATCH. New features and pricing changes → WATCH.
M5     TALENT: a founder / executive joining or leaving, from a verified (FACT) source → MATERIAL; otherwise WATCH.
M6     TALENT hiring: >= 10 new postings of one specialised role class in 14 days, or a specialised class never
       posted before → MATERIAL; >= 5 → WATCH; hiring acceleration or role closures → WATCH. Always INFERENCE.
M7     TECHNICAL (paper, open-source release, benchmark claim, repository release, repo traction) → WATCH;
       MATERIAL when it bears on a claim in the company's V3 diligence (shared key terms with a recorded claim)
       or repository traction is high (a major release or new repository of a repo with >= 1,000 stars).
M8     Competitor events (``impact.py``): a MATERIAL event of a DIRECT comp is MATERIAL for the portfolio company;
       of an ADJACENT / INFRASTRUCTURE / INCUMBENT / OPEN_SOURCE comp → WATCH. A comp's WATCH event is WATCH for a
       DIRECT comp, else not recorded.
M9     Confidence < 0.5, or only third-party sources from one source family (uncorroborated) → at most WATCH.
M10    NOISE: generic posts, copy edits, docs, routine (patch / pre-) releases, small new repositories, hiring
       state snapshots, activity metrics, low-engagement discussion → NOISE (counted in the funnel, not hidden).
M11    Other typed events (REGULATORY milestones, STRATEGIC repositioning / market entry / public attention)
       → WATCH.
=====  ========================================================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from radar.portfolio.dedup import tokens
from radar.portfolio.normalize import AGENCY_RE
from radar.portfolio.types import NOISE_TYPE, Candidate, CompanyContext

CONTRACT_MATERIAL_USD = 1_000_000
NOVELTY_DAYS = 90
LOW_CONFIDENCE = 0.5
HIGH_TRACTION_STARS = 1_000
DILIGENCE_OVERLAP = 2  # distinctive shared tokens between an event title and a diligence claim
LAUNCH_SUBTYPES = frozenset({"product_launch", "major_release"})


@dataclass(frozen=True)
class Rule:
    id: str
    description: str
    condition: str
    outcome: str


RULES: tuple[Rule, ...] = (
    Rule("M1", "Risk signal", "RISK event from the company or a registry", "MATERIAL"),
    Rule("M2", "Financing", "FINANCING event announced by the company or a primary source", "MATERIAL"),
    Rule("M3", "Commercial", "Government contract >= $1M, or a company-announced partner / customer that is a tracked "
                             "company or a government agency", "MATERIAL; smaller or unnamed → WATCH"),
    Rule("M4", "Product launch", "Launch / major release announced by the company, first in 90 days",
         "MATERIAL; repeat launch, new feature or pricing → WATCH"),
    Rule("M5", "Leadership change", "Founder / executive join or departure from a verified source",
         "MATERIAL; unverified → WATCH"),
    Rule("M6", "Specialised hiring", ">= 10 new roles of one specialised class in 14 days or a new class",
         "MATERIAL; >= 5 roles, acceleration, closures → WATCH (INFERENCE)"),
    Rule("M7", "Technical", "Paper, open-source release, benchmark, release, repo traction",
         "WATCH; MATERIAL if it bears on a diligence claim or traction is high"),
    Rule("M8", "Competitor impact", "MATERIAL event of a DIRECT comp", "MATERIAL for the portfolio company; "
                                                                        "other comp types → WATCH"),
    Rule("M9", "Low confidence / uncorroborated", "confidence < 0.5 or third-party-only from one family",
         "capped at WATCH"),
    Rule("M10", "Noise", "Generic posts, copy edits, docs, routine releases, low-engagement social", "NOISE"),
    Rule("M11", "Other typed events", "REGULATORY or STRATEGIC events", "WATCH"),
)
RULES_BY_ID = {r.id: r for r in RULES}


def _official(ev: Candidate) -> bool:
    return any(s.publisher in ("company", "registry") for s in ev.sources)


def _company_announced(ev: Candidate) -> bool:
    return any(s.publisher == "company" for s in ev.sources)


def uncorroborated(ev: Candidate) -> bool:
    """Only third-party sources, all from one source family."""
    return not _official(ev) and len({s.family for s in ev.sources}) <= 1


def novelty(ev: Candidate, history: list[Candidate]) -> str | None:
    """'first <subtype> in 90 days' or None when an earlier one exists for the company."""
    since = ev.event_at - timedelta(days=NOVELTY_DAYS)
    group = LAUNCH_SUBTYPES if ev.subtype in LAUNCH_SUBTYPES else {ev.subtype}
    for h in history:
        if h is ev or h.company_id != ev.company_id or h.subtype not in group:
            continue
        if since <= h.event_at < ev.event_at and h.title != ev.title:
            return None
    return f"first {ev.subtype.replace('_', ' ')} for the company in {NOVELTY_DAYS} days"


_STOP_EXTRA = frozenset("model models ai launch launches launched introducing announcing release released new "
                        "faster performance company product platform system data open source".split())


def diligence_link(ev: Candidate, ctx: CompanyContext) -> dict | None:
    """The diligence claim an event bears on (>= 2 distinctive shared tokens), or None."""
    et = tokens(f"{ev.title} {ev.description or ''}") - _STOP_EXTRA
    best = None
    for cl in ctx.diligence_claims:
        shared = sorted(et & (tokens(str(cl.get("text") or "")) - _STOP_EXTRA))
        shared = [t for t in shared if len(t) > 2 and not t.isdigit()]
        if len(shared) >= DILIGENCE_OVERLAP and (best is None or len(shared) > len(best["shared"])):
            best = {**cl, "shared": shared}
    return best


def _names_major_counterparty(ev: Candidate, ctx: CompanyContext) -> str | None:
    m = AGENCY_RE.search(ev.title)
    if m:
        return m.group(0)
    low = ev.title.lower()
    for name in ctx.tracked_names:
        n = name.strip().lower()
        if len(n) >= 4 and n != ctx.name.lower() and re.search(rf"\b{re.escape(n)}\b", low):
            return name
    return None


def _max_stars(ev: Candidate, ctx: CompanyContext) -> int:
    stars = int(ev.meta.get("repo_stars") or ev.meta.get("stars") or 0)
    repo = str(ev.meta.get("repo") or "").lower()
    return max(stars, ctx.repo_stars.get(repo, 0))


def _base(ev: Candidate, ctx: CompanyContext, history: list[Candidate]) -> tuple[str, str, str]:
    t, st = ev.event_type, ev.subtype
    if t == NOISE_TYPE:
        return "NOISE", "M10", f"routine change ({st.replace('_', ' ')}): counted, not surfaced"
    if t == "RISK":
        if _official(ev):
            return "MATERIAL", "M1", f"risk signal ({st.replace('_', ' ')}) from a primary source"
        return "MATERIAL", "M1", f"risk signal ({st.replace('_', ' ')}) reported by a third party"
    if t == "FINANCING":
        who = "announced by the company" if _company_announced(ev) else "reported by a primary source" \
            if _official(ev) else "reported by a third party"
        return "MATERIAL", "M2", f"{st.replace('_', ' ')} {who}"
    if t == "COMMERCIAL":
        if st == "government_contract":
            amt = ev.meta.get("amount")
            if isinstance(amt, int | float) and amt >= CONTRACT_MATERIAL_USD:
                return "MATERIAL", "M3", f"government contract of ${amt:,.0f} (>= ${CONTRACT_MATERIAL_USD:,})"
            if isinstance(amt, int | float):
                return "WATCH", "M3", f"government award of ${amt:,.0f} (< ${CONTRACT_MATERIAL_USD:,})"
            return "WATCH", "M3", "government contract without a stated amount"
        who = _names_major_counterparty(ev, ctx)
        if who and _company_announced(ev):
            return "MATERIAL", "M3", f"{st.replace('_', ' ')} announced by the company naming {who}"
        return "WATCH", "M3", f"{st.replace('_', ' ')} without a named major counterparty"
    if t == "PRODUCT":
        if st in LAUNCH_SUBTYPES:
            nov = novelty(ev, history)
            if _company_announced(ev) and nov:
                return "MATERIAL", "M4", f"product launch announced by the company ({nov})"
            if not _company_announced(ev):
                return "WATCH", "M4", "launch seen only in a third-party story"
            return "WATCH", "M4", "repeat launch (another launch in the previous 90 days)"
        return "WATCH", "M4", f"{st.replace('_', ' ')}"
    if t == "TALENT":
        if st in ("executive_join", "executive_departure"):
            if ev.epistemic == "FACT" and (_official(ev) or ev.meta.get("signal_type") == "affiliation_changed"):
                return "MATERIAL", "M5", f"leadership change ({st.replace('_', ' ')}) from a verified source"
            return "WATCH", "M5", "leadership change from an unverified source"
        n = int(ev.meta.get("count") or 0)
        if st == "specialized_hiring_cluster":
            if n >= 10:
                return "MATERIAL", "M6", f"{n} new roles of one specialised class in 14 days (>= 10)"
            return "WATCH", "M6", f"{n} new roles of one specialised class in 14 days (>= 5)"
        if st == "new_role_class":
            return "MATERIAL", "M6", "a specialised role class the company never posted before"
        return "WATCH", "M6", st.replace("_", " ")
    if t == "TECHNICAL":
        link = diligence_link(ev, ctx)
        if link:
            return "MATERIAL", "M7", (f"bears on diligence claim “{str(link.get('text'))[:120]}” "
                                      f"(shared terms: {', '.join(link['shared'][:4])})")
        stars = _max_stars(ev, ctx)
        if st in ("open_source_release", "repository_release") and stars >= HIGH_TRACTION_STARS and (
                st == "open_source_release" or ev.meta.get("semver") in ("major", "first")):
            return "MATERIAL", "M7", f"{st.replace('_', ' ')} of a repository with {stars:,} stars"
        return "WATCH", "M7", st.replace("_", " ")
    return "WATCH", "M11", f"{t.lower()} {st.replace('_', ' ')}"


def classify(ev: Candidate, ctx: CompanyContext, history: list[Candidate] | None = None) -> tuple[str, str, str]:
    """(MATERIAL | WATCH | NOISE, rule id, reason)."""
    level, rule, reason = _base(ev, ctx, history or [])
    if level == "MATERIAL":
        if ev.confidence < LOW_CONFIDENCE:
            return "WATCH", "M9", f"{rule}: {reason}; capped at WATCH (confidence {ev.confidence:.2f} < 0.5)"
        if uncorroborated(ev):
            return "WATCH", "M9", f"{rule}: {reason}; capped at WATCH (third-party source only, not corroborated)"
    return level, rule, reason
