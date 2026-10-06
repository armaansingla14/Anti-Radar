"""Deterministic, conservative company stage estimation (epistemic: INFERENCE).

We only claim an early stage when the evidence is strong (young org, tiny observed
team, no funding signal). Everything else is 'unknown' — a wrong 'seed' label is
worse than an honest 'unknown'.
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
    if company.founded_date:
        return company.founded_date, "founded date"
    for k in ("created_at", "org_created_at", "github_created_at"):
        raw = (company.attrs or {}).get(k)
        if raw:
            try:
                return dateparser.isoparse(str(raw)).date(), "GitHub org creation date"
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
    if created is None:
        if company.kind == "stealth" and 0 < n_people <= FORMATION_MAX_PEOPLE:
            return "formation", f"Stealth company with {n_people} known people and no funding signal."
        return "unknown", "Insufficient data: no founding/org creation date and no funding signal."

    months = _months_between(created, clock.today())
    age = f"{created_basis} {created.isoformat()} (~{max(months, 0):.0f} months ago)"
    if months < 0:
        return "unknown", f"Creation date {created.isoformat()} is in the future; not inferring stage."
    if months <= FORMATION_MAX_MONTHS and n_people <= FORMATION_MAX_PEOPLE:
        return "formation", f"{age}; {n_people} known people; no funding signal."
    if months <= PRE_SEED_MAX_MONTHS and n_people <= PRE_SEED_MAX_PEOPLE:
        return "pre_seed", f"{age}; {n_people} known people; no funding signal."
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
    for cp in session.scalars(select(CompanyPerson).where(CompanyPerson.company_id.in_(ids))):
        people[cp.company_id].append(cp)

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
