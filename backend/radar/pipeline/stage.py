"""Deterministic, conservative company stage estimation (epistemic: INFERENCE).

We only claim an early stage when the evidence is strong: a young org, a tiny
observed team, no funding signal AND positive early-stage evidence (a founder on
record, a formation signal, or a declared stealth company). A young GitHub org on
its own is not enough — large companies and labs open new orgs all the time — and
projects, labs and orgs named after a known large organization are never staged.
Everything else is 'unknown' — a wrong 'seed' label is worse than an honest 'unknown'.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from dateutil import parser as dateparser
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import Company, CompanyPerson, Epistemic, Repository, Signal
from radar.pipeline.resolve import record_provenance

FORMATION_MAX_MONTHS = 6
PRE_SEED_MAX_MONTHS = 18
FORMATION_MAX_PEOPLE = 3
PRE_SEED_MAX_PEOPLE = 10
FUNDING_SIGNAL_TYPES = {"funding", "funding_round", "funding_announced"}
EARLY_STAGE_SIGNAL_TYPES = {"formation_language", "x_formation_post", "affiliation_changed"}
NON_COMPANY_KINDS = {"project", "lab"}
# Large organizations beyond the thesis affiliation lists whose teams publish under their own GitHub orgs.
LARGE_ORGS = (
    "Alibaba", "Ant Group", "Baidu", "ByteDance", "Tencent", "Huawei", "Xiaomi", "RedNote", "Xiaohongshu",
    "Meituan", "NetEase", "Samsung", "LG", "Sony", "Toyota", "Honda", "Hyundai", "Bosch", "Siemens",
    "IBM", "Oracle", "Salesforce", "Adobe", "Uber", "Netflix", "Alphabet", "Cisco", "Intel Labs",
    "Microsoft Research", "Amazon Science", "Apple Machine Learning", "Shanghai AI Laboratory",
)

_ROUND_STAGE = [
    (re.compile(r"pre[\s\-_]?seed"), "pre_seed"),
    (re.compile(r"\bseed\b"), "seed"),
    (re.compile(r"series[\s\-_]?a\b"), "series_a"),
    (re.compile(r"series[\s\-_]?[b-z]\b|growth|late[\s\-_]stage"), "growth"),
    (re.compile(r"\bipo\b|public"), "public"),
]


def _months_between(start: date, end: date) -> float:
    return (end - start).days / 30.44


def _declared_created(company: Company) -> tuple[date | None, str]:
    attrs = company.attrs or {}
    if company.founded_date:
        # The GitHub provider fills founded_date from the org's created_at: say so, it is not a founding date.
        if attrs.get("founded_date_basis") == "github_org_created_at":
            return company.founded_date, "GitHub org created"
        return company.founded_date, "founded date"
    for k in ("created_at", "org_created_at", "github_created_at"):
        raw = attrs.get(k)
        if raw:
            try:
                return dateparser.isoparse(str(raw)).date(), "GitHub org created"
            except (ValueError, OverflowError):
                continue
    return None, ""


def _company_created(company: Company, repos: Sequence[Repository]) -> tuple[date | None, str]:
    """Creation date; an older repo of the company overrides it (conservative: older = less early)."""
    created, basis = _declared_created(company)
    if created is None:
        return None, ""
    oldest = min((r.gh_created_at.date() for r in repos if r.gh_created_at), default=None)
    if oldest and oldest < created:
        return oldest, "oldest company repository created"
    return created, basis


def _funding_round(signals: Sequence[Signal]) -> tuple[str | None, Signal | None]:
    """Latest stage implied by a funding signal, if any."""
    funding = [s for s in signals if s.signal_type in FUNDING_SIGNAL_TYPES]
    for sig in sorted(funding, key=lambda s: s.event_at, reverse=True):
        meta = sig.meta or {}
        text = str(meta.get("round") or meta.get("funding_round") or sig.title or "").lower()
        for pat, stage in _ROUND_STAGE:
            if pat.search(text):
                return stage, sig
    return None, funding[0] if funding else None


ESTABLISHED_ORG_FOLLOWERS = 2000
ESTABLISHED_ORG_REPOS = 100
ESTABLISHED_REPO_STARS = 20000
ESTABLISHED_REPO_MONTHS = 24


def _established(company: Company, repos: Sequence[Repository], created: date | None) -> str | None:
    """Evidence that this is an established organization, well past formation (INFERENCE)."""
    if company.is_demo:
        return None
    a = company.attrs or {}
    followers, public_repos = a.get("followers") or 0, a.get("public_repos") or 0
    if followers >= ESTABLISHED_ORG_FOLLOWERS or public_repos >= ESTABLISHED_ORG_REPOS:
        return (f"Established organization: {followers:,} GitHub followers, {public_repos} public repositories "
                "— well past formation stage.")
    for r in repos:
        if (r.stars or 0) >= ESTABLISHED_REPO_STARS and r.gh_created_at is not None:
            months = _months_between(r.gh_created_at.date(), clock.today())
            if months >= ESTABLISHED_REPO_MONTHS:
                return (f"Mature flagship project: {r.full_name} has {r.stars:,} stars and is {months:.0f} months "
                        "old — widely known, past formation stage.")
    return None


def _large_org(company: Company) -> str | None:
    """Known large organization named by the company / GitHub org (likely a team of it, not a startup)."""
    if company.is_demo:
        return None
    from radar.thesis.loader import get_thesis

    names = [n for tier in get_thesis().affiliation_tiers.values() for n in tier] + list(LARGE_ORGS)
    text = " ".join(x for x in (company.name, (company.github_org or "").replace("-", " ")) if x).lower()
    for n in names:
        if re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", text):
            return n
    return None


def _early_evidence(company: Company, signals: Sequence[Signal], people: Sequence[object]) -> str | None:
    """Positive evidence of an early-stage *company* (not just a young GitHub org)."""
    if company.kind == "stealth":
        return "declared stealth company"
    founders = sum(1 for p in people if getattr(p, "is_founder", False))
    if founders:
        return f"{founders} founder{'s' if founders != 1 else ''} on record"
    kinds = sorted({s.signal_type for s in signals if s.signal_type in EARLY_STAGE_SIGNAL_TYPES})
    if kinds:
        return f"formation signal ({', '.join(kinds)})"
    return None


def estimate_stage(
    company: Company,
    signals: Sequence[Signal],
    repos: Sequence[Repository],
    people: Sequence[object],
) -> tuple[str, str]:
    """(stage, basis). Stages: formation | pre_seed | seed | series_a | growth | public | unknown."""
    stage, sig = _funding_round(signals)
    if stage:
        return stage, f"Funding signal ({sig.source_type}, {sig.event_at.date().isoformat()}) indicates {stage}."
    if sig is not None:
        return "unknown", "A funding signal exists but does not state the round."

    created, created_basis = _company_created(company, repos)
    n_people = len(people)
    established = _established(company, repos, created)
    if established:
        return "growth", established
    if company.kind in NON_COMPANY_KINDS:
        return "unknown", f"Listed as a {company.kind}, not a company; stage not inferred."
    big = _large_org(company)
    if big:
        return "unknown", (f"Name matches established organization '{big}' — likely a team or project of it; "
                           "stage not inferred.")
    if created is None:
        if company.kind == "stealth" and 0 < n_people <= FORMATION_MAX_PEOPLE:
            return "formation", f"Stealth company with {n_people} known people and no funding signal."
        return "unknown", "Insufficient data: no founding/org creation date and no funding signal."

    months = _months_between(created, clock.today())
    age = f"{created_basis} {created.isoformat()} (~{max(months, 0):.0f} months ago)"
    if months < 0:
        return "unknown", f"Creation date {created.isoformat()} is in the future; not inferring stage."
    young = months <= PRE_SEED_MAX_MONTHS and n_people <= PRE_SEED_MAX_PEOPLE
    evidence = _early_evidence(company, signals, people) if young else None
    if young and evidence is None:
        return "unknown", (f"{age}; {n_people} known people; no founder, formation signal or stealth declaration "
                           "— a young org alone does not establish an early-stage company.")
    if months <= FORMATION_MAX_MONTHS and n_people <= FORMATION_MAX_PEOPLE:
        return "formation", f"{age}; {n_people} known people; {evidence}; no funding signal."
    if young:
        return "pre_seed", f"{age}; {n_people} known people; {evidence}; no funding signal."
    return "unknown", f"{age}; {n_people} known people; too old or too large to infer stage without funding data."


def apply_stage_estimates(session: Session, *, only_ids: Sequence | None = None) -> int:
    """Estimate stage for companies (skips manual overrides). Returns number of companies changed."""
    q = select(Company)
    if only_ids is not None:
        q = q.where(Company.id.in_(list(only_ids)))
    companies = session.scalars(q).all()
    if not companies:
        return 0
    ids = [c.id for c in companies]
    sigs: dict = defaultdict(list)
    for s in session.scalars(select(Signal).where(Signal.entity_id.in_(ids), Signal.entity_type == "company")):
        sigs[s.entity_id].append(s)
    repos: dict = defaultdict(list)
    for r in session.scalars(select(Repository).where(Repository.company_id.in_(ids))):
        repos[r.company_id].append(r)
    people: dict = defaultdict(list)
    members: dict = defaultdict(set)
    for cp in session.scalars(select(CompanyPerson).where(CompanyPerson.company_id.in_(ids))):
        people[cp.company_id].append(cp)
        members[cp.person_id].add(cp.company_id)
    if members:  # formation signals on the company's people count as early-stage evidence too
        for s in session.scalars(select(Signal).where(
                Signal.entity_type == "person", Signal.entity_id.in_(list(members)),
                Signal.signal_type.in_(EARLY_STAGE_SIGNAL_TYPES))):
            for cid in members[s.entity_id]:
                sigs[cid].append(s)

    changed = 0
    for c in companies:
        if (c.attrs or {}).get("stage_source") == "manual":
            continue
        stage, basis = estimate_stage(c, sigs[c.id], repos[c.id], people[c.id])
        if (c.stage, c.stage_basis) == (stage, basis):
            continue
        c.stage, c.stage_basis = stage, basis
        record_provenance(
            session, "company", c.id, "stage", {"stage": stage, "basis": basis},
            source_type="system", source_url="", confidence=0.6, epistemic=Epistemic.INFERENCE.value,
        )
        changed += 1
    return changed
