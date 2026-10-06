"""Strange Signals: deterministic, evidence-backed patterns a human would find odd.

Every detector fires only when the stored data supports the claim, cites the
entities and signals involved, and words its headline from facts we hold
(e.g. "people who previously worked at X", never an invented job title).
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import is_technical_class, job_class, job_start
from radar.models.core import (
    Company,
    CompanyPerson,
    Job,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
)
from radar.providers.domains import github_repos_in
from radar.scoring.collision import number_word
from radar.scoring.extract import SignalIndex, build_company_features, load_signal_index, org_created_date
from radar.scoring.features import SignalLite
from radar.thesis.loader import Thesis, get_thesis

YOUNG_ORG_DAYS = 180
MIN_CLUSTER = 3
LAB_PAPER_DAYS = 365
LAUNCH_SPAN_DAYS = 30
LAUNCH_PAPER_MAX_AGE_DAYS = 90
EXODUS_WINDOW_DAYS = 30
QUIET_MIN_STARS_30D = 50.0  # top-decile velocity must also clear this absolute floor
QUIET_TOP_FRACTION = 0.20
QUIET_MIN_THESIS = 0.4  # theme weight × theme score
QUIET_MAX_RESULTS = 5
HIRING_WINDOW_DAYS = 30
SIGNAL_LOOKBACK_DAYS = 180
PRIOR_ORG_META_KEYS = ("from", "previous", "prior", "prior_affiliation", "old_affiliation", "from_org")
ROLE_CLASS_LABELS = {
    "ml_research": "ML research",
    "robotics_controls": "robotics & controls",
    "embedded_firmware": "embedded/firmware",
    "silicon": "silicon",
    "gpu_compiler": "GPU/compiler",
    "rf_defense": "RF/defense",
    "energy_plasma": "energy/plasma",
    "manufacturing": "manufacturing",
    "infra": "infrastructure",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def age_phrase(days: int) -> str:
    if days < 14:
        return f"{max(days, 1)}-day-old"
    if days < 60:
        return f"{round(days / 7)}-week-old"
    return f"{round(days / 30)}-month-old"


def _ent(kind: str, obj: Any) -> dict[str, Any]:
    name = getattr(obj, "name", None) or getattr(obj, "title", None) or getattr(obj, "full_name", "")
    return {
        "type": kind,
        "id": str(obj.id),
        "slug": getattr(obj, "slug", None)
        or getattr(obj, "full_name", None)
        or getattr(obj, "arxiv_id", None),
        "name": name,
    }


def _sig_ev(signals: list[SignalLite], limit: int = 5) -> list[dict[str, Any]]:
    return [s.evidence() for s in sorted(signals, key=lambda s: s.event_at, reverse=True)[:limit]]


def _person_ev(p: Person, detail: str) -> dict[str, Any]:
    url = f"https://github.com/{p.github_handle}" if p.github_handle else p.personal_site
    return {
        "signal_id": None,
        "title": p.name,
        "detail": detail,
        "source_type": "github" if p.github_handle else None,
        "source_url": url,
        "event_at": None,
    }


def _org_noun(c: Company) -> str:
    return "GitHub organization" if c.github_org and not c.website else "company"


def _canon_org(name: str, thesis: Thesis) -> tuple[str, str]:
    """(grouping key, display name) — thesis tier names unify aliases, else exact string."""
    m = thesis.matched_affiliation(name)
    disp = m or name.strip()
    return disp.lower(), disp


@dataclass
class _Ctx:
    session: Session
    day: date
    thesis: Thesis
    index: SignalIndex
    detected_at: datetime

    def make(
        self,
        pattern: str,
        key: str,
        headline: str,
        detail: str,
        entities: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        strength: float,
        is_demo: bool,
    ) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"anti-radar:strange:{pattern}:{key}")),
            "pattern": pattern,
            "headline": headline,
            "detail": detail,
            "entities": entities,
            "evidence": evidence,
            "detected_at": self.detected_at,
            "strength": round(min(max(strength, 0.0), 1.0), 3),
            "is_demo": is_demo,
        }


@dataclass
class _YoungOrg:
    company: Company
    age_days: int
    people: dict[uuid.UUID, str]  # person id -> relation (contributor | member)


def _young_orgs(ctx: _Ctx) -> tuple[list[_YoungOrg], dict[uuid.UUID, Person]]:
    s, day = ctx.session, ctx.day
    companies = {c.id: c for c in s.scalars(select(Company).where(Company.dismissed.is_(False)))}
    repo_dates: dict[uuid.UUID, list[datetime | None]] = defaultdict(list)
    for cid, created in s.execute(
        select(Repository.company_id, Repository.gh_created_at).where(Repository.company_id.is_not(None))
    ).all():
        repo_dates[cid].append(created)
    young: dict[uuid.UUID, _YoungOrg] = {}
    for c in companies.values():
        created = org_created_date(c.founded_date, repo_dates.get(c.id, []))
        if created is not None and 0 <= (day - created).days <= YOUNG_ORG_DAYS:
            young[c.id] = _YoungOrg(c, (day - created).days, {})
    if not young:
        return [], {}
    for cid, pid in s.execute(
        select(CompanyPerson.company_id, CompanyPerson.person_id).where(
            CompanyPerson.company_id.in_(list(young))
        )
    ).all():
        young[cid].people[pid] = "member"
    for cid, pid in s.execute(
        select(Repository.company_id, RepoContributor.person_id)
        .join(Repository, Repository.id == RepoContributor.repository_id)
        .where(Repository.company_id.in_(list(young)))
    ).all():
        young[cid].people[pid] = "contributor"
    pids = {p for y in young.values() for p in y.people}
    people = {p.id: p for p in s.scalars(select(Person).where(Person.id.in_(list(pids))))} if pids else {}
    return list(young.values()), people


def _relation_verb(rels: list[str]) -> str:
    return "are contributing to" if all(r == "contributor" for r in rels) else "are linked to"


# ── Patterns ─────────────────────────────────────────────────────────────────


def shared_prior_employer_new_org(
    ctx: _Ctx, young: list[_YoungOrg], people: dict[uuid.UUID, Person]
) -> list[dict]:
    out = []
    for y in young:
        org_lower = y.company.name.lower()
        groups: dict[str, dict[uuid.UUID, bool]] = defaultdict(dict)  # key -> person -> is_prior
        display: dict[str, str] = {}
        for pid in y.people:
            p = people.get(pid)
            if p is None:
                continue
            affs = [(a, True) for a in (p.prior_affiliations or [])]
            if p.current_company:
                affs.append((p.current_company, False))
            for a, prior in affs:
                if not a or not a.strip():
                    continue
                key, disp = _canon_org(a, ctx.thesis)
                if key == org_lower or org_lower in key:
                    continue  # the new org itself is not a "shared prior employer"
                display[key] = disp
                groups[key][pid] = groups[key].get(pid, False) or prior
        best = max(
            (k for k in groups if len(groups[k]) >= MIN_CLUSTER),
            key=lambda k: (len(groups[k]), ctx.thesis.affiliation_tier(display[k])),
            default=None,
        )
        if best is None:
            continue
        members = sorted(groups[best], key=lambda pid: people[pid].name)
        n, org_name, tier = len(members), display[best], ctx.thesis.affiliation_tier(display[best])
        all_prior = all(groups[best][pid] for pid in members)
        who = (
            f"people who previously worked at {org_name}"
            if all_prior
            else f"people with {org_name} affiliations"
        )
        verb = _relation_verb([y.people[pid] for pid in members])
        headline = (
            f"{number_word(n, True)} {who} {verb} the same {age_phrase(y.age_days)} {_org_noun(y.company)}"
        )
        detail = (
            f"{', '.join(people[pid].name for pid in members)} all list {org_name} as a current or prior "
            f"affiliation and are linked to {y.company.name} (created ~{y.age_days} days ago)."
        )
        sigs = ctx.index.for_entities([y.company.id, *members])
        evidence = [
            _person_ev(people[pid], f"Affiliation: {org_name}; {y.people[pid]} of {y.company.name}")
            for pid in members
        ] + _sig_ev(sigs)
        strength = 0.5 + 0.1 * (n - MIN_CLUSTER) + 0.2 * tier + 0.2 * (1 - y.age_days / YOUNG_ORG_DAYS)
        out.append(
            ctx.make(
                "shared_prior_employer_new_org",
                f"{y.company.id}:{best}",
                headline,
                detail,
                [_ent("company", y.company)] + [_ent("person", people[pid]) for pid in members],
                evidence,
                strength,
                bool(y.company.is_demo or any(people[p].is_demo for p in members)),
            )
        )
    return out


def lab_cluster(ctx: _Ctx, young: list[_YoungOrg], people: dict[uuid.UUID, Person]) -> list[dict]:
    if not young:
        return []
    lo = datetime.combine(ctx.day - timedelta(days=LAB_PAPER_DAYS), time.min, tzinfo=UTC)
    authors: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    papers: dict[uuid.UUID, ResearchPaper] = {}
    for paper, pid in ctx.session.execute(
        select(ResearchPaper, PaperAuthor.person_id)
        .join(PaperAuthor, PaperAuthor.paper_id == ResearchPaper.id)
        .where(
            ResearchPaper.published_at >= lo,
            ResearchPaper.published_at <= datetime.combine(ctx.day, time.max, tzinfo=UTC),
        )
    ).all():
        papers[paper.id] = paper
        authors[paper.id].add(pid)
    out = []
    for y in young:
        best = None
        for paper_id, auth in authors.items():
            overlap = auth & set(y.people)
            if len(overlap) >= MIN_CLUSTER and (best is None or len(overlap) > len(best[1])):
                best = (papers[paper_id], overlap)
        if best is None:
            continue
        paper, overlap = best
        members = sorted(overlap, key=lambda pid: people[pid].name)
        n = len(members)
        verb = _relation_verb([y.people[pid] for pid in members])
        headline = (
            f"{number_word(n, True)} co-authors of “{paper.title}” {verb} the same "
            f"{age_phrase(y.age_days)} {_org_noun(y.company)}"
        )
        detail = (
            f"{', '.join(people[pid].name for pid in members)} co-authored arXiv:{paper.arxiv_id} and are "
            f"linked to {y.company.name}."
        )
        paper_ev = {
            "signal_id": None,
            "title": paper.title,
            "detail": f"arXiv:{paper.arxiv_id}",
            "source_type": "arxiv",
            "source_url": paper.url or f"https://arxiv.org/abs/{paper.arxiv_id}",
            "event_at": paper.published_at,
        }
        sigs = ctx.index.for_entities([y.company.id, paper.id, *members])
        strength = (
            0.55
            + 0.1 * (n - MIN_CLUSTER)
            + 0.2 * (1 - y.age_days / YOUNG_ORG_DAYS)
            + (0.1 if paper.code_urls else 0.0)
        )
        out.append(
            ctx.make(
                "lab_cluster",
                f"{y.company.id}:{paper.id}",
                headline,
                detail,
                [_ent("company", y.company), _ent("paper", paper)]
                + [_ent("person", people[pid]) for pid in members],
                [paper_ev] + _sig_ev(sigs),
                strength,
                bool(y.company.is_demo or paper.is_demo),
            )
        )
    return out


def paper_code_launch(ctx: _Ctx) -> list[dict]:
    s, day = ctx.session, ctx.day
    lo = datetime.combine(day - timedelta(days=LAUNCH_PAPER_MAX_AGE_DAYS), time.min, tzinfo=UTC)
    papers = [
        p
        for p in s.scalars(
            select(ResearchPaper).where(
                ResearchPaper.published_at >= lo,
                ResearchPaper.published_at <= datetime.combine(day, time.max, tzinfo=UTC),
            )
        )
        if p.code_urls
    ]
    slugs = {slug.lower(): p for p in papers for u in p.code_urls for slug in github_repos_in(u)}
    if not slugs:
        return []
    repos = {
        r.full_name.lower(): r
        for r in s.scalars(select(Repository).where(func.lower(Repository.full_name).in_(list(slugs))))
    }
    out = []
    for slug, paper in slugs.items():
        repo = repos.get(slug)
        if repo is None:
            continue
        ids = [paper.id, repo.id] + ([repo.company_id] if repo.company_id else [])
        hn = [x for x in ctx.index.for_entities(ids) if x.source_type == "hackernews"]
        best = None
        for h in hn:
            times = [paper.published_at, h.event_at] + ([repo.gh_created_at] if repo.gh_created_at else [])
            span = (max(times) - min(times)).total_seconds() / 86400
            if span <= LAUNCH_SPAN_DAYS and (best is None or span < best[1]):
                best = (h, span)
        if best is None:
            continue
        h, span = best
        points = h.raw_value or 0.0
        headline = (
            f"Paper, code and a Hacker News launch landed within {max(1, round(span))} days: “{paper.title}”"
        )
        detail = (
            f"arXiv:{paper.arxiv_id} links {repo.full_name}"
            + (f" ({int(repo.stars):,} stars)" if repo.stars else "")
            + f"; discussed on Hacker News{f' ({int(points)} points)' if points else ''}."
        )
        evidence = [
            {
                "signal_id": None,
                "title": paper.title,
                "detail": f"arXiv:{paper.arxiv_id}",
                "source_type": "arxiv",
                "source_url": paper.url or f"https://arxiv.org/abs/{paper.arxiv_id}",
                "event_at": paper.published_at,
            },
            {
                "signal_id": None,
                "title": repo.full_name,
                "detail": "Code linked from the paper",
                "source_type": "github",
                "source_url": f"https://github.com/{repo.full_name}",
                "event_at": repo.gh_created_at,
            },
            h.evidence(),
        ]
        strength = 0.5 + 0.3 * (1 - span / LAUNCH_SPAN_DAYS) + 0.2 * min(1.0, points / 200)
        ents = [_ent("paper", paper), _ent("repository", repo)]
        out.append(
            ctx.make(
                "paper_code_launch",
                f"{paper.id}:{repo.id}",
                headline,
                detail,
                ents,
                evidence,
                strength,
                bool(paper.is_demo or repo.is_demo or h.is_demo),
            )
        )
    return out


def affiliation_exodus(ctx: _Ctx) -> list[dict]:
    end = datetime.combine(ctx.day, time.max, tzinfo=UTC)
    rows = ctx.session.scalars(
        select(Signal).where(
            Signal.signal_type == "affiliation_changed",
            Signal.entity_type == "person",
            Signal.event_at >= end - timedelta(days=EXODUS_WINDOW_DAYS),
            Signal.event_at <= end,
        )
    ).all()
    groups: dict[str, dict[uuid.UUID, Signal]] = defaultdict(dict)
    display: dict[str, str] = {}
    for sig in rows:
        meta = sig.meta or {}
        prior = next(
            (meta[k] for k in PRIOR_ORG_META_KEYS if isinstance(meta.get(k), str) and meta[k].strip()), None
        )
        if prior is None:
            continue
        key, disp = _canon_org(prior, ctx.thesis)
        display[key] = disp
        groups[key].setdefault(sig.entity_id, sig)
    out = []
    for key, by_person in groups.items():
        if len(by_person) < MIN_CLUSTER:
            continue
        people = {p.id: p for p in ctx.session.scalars(select(Person).where(Person.id.in_(list(by_person))))}
        sigs = sorted(by_person.values(), key=lambda x: x.event_at)
        span = max(1, math.ceil((sigs[-1].event_at - sigs[0].event_at).total_seconds() / 86400))
        n, org = len(by_person), display[key]
        headline = f"{number_word(n, True)} people left {org} within {span} days"
        detail = (
            f"Affiliation changes away from {org}: "
            + ", ".join(people[pid].name for pid in by_person if pid in people)
            + "."
        )
        evidence = [
            {
                "signal_id": str(x.id),
                "title": x.title,
                "source_type": x.source_type,
                "source_url": x.source_url,
                "event_at": x.event_at,
            }
            for x in sigs
        ]
        strength = (
            0.4 + 0.1 * (n - MIN_CLUSTER) + 0.3 * ctx.thesis.affiliation_tier(org) + 0.2 * (1 - span / 30)
        )
        out.append(
            ctx.make(
                "affiliation_exodus",
                f"{key}:{sigs[0].event_at.date().isoformat()}",
                headline,
                detail,
                [_ent("person", people[pid]) for pid in by_person if pid in people],
                evidence,
                strength,
                any(x.is_demo for x in sigs),
            )
        )
    return out


def quiet_velocity(ctx: _Ctx) -> list[dict]:
    feats = [
        f
        for f in build_company_features(ctx.session, ctx.day, ctx.index, ctx.thesis)
        if f.stars_30d is not None
    ]
    real = sorted(f.stars_30d for f in feats if not f.is_demo)
    threshold = QUIET_MIN_STARS_30D
    if real:
        threshold = max(
            threshold, real[min(len(real) - 1, math.ceil((1 - QUIET_TOP_FRACTION) * len(real)) - 1)]
        )
    # hn_mentions_90d is None unless HN attention was actually measured (signals or an explicit probe),
    # so "zero discussion" is only claimed when it was checked. Off-thesis repos are not intelligence.
    hits = [
        f for f in feats
        if f.stars_30d >= threshold and f.hn_mentions_90d == 0 and f.x_followers is None
        and max((ctx.thesis.themes[t].weight * v for t, v in f.theme_scores.items() if t in ctx.thesis.themes),
                default=0.0) >= QUIET_MIN_THESIS
    ]
    hits.sort(key=lambda f: -f.stars_30d)
    out = []
    companies = (
        {
            c.id: c
            for c in ctx.session.scalars(
                select(Company).where(Company.id.in_([f.entity_id for f in hits[:QUIET_MAX_RESULTS]]))
            )
        }
        if hits
        else {}
    )
    for f in hits[:QUIET_MAX_RESULTS]:
        c = companies[f.entity_id]
        stars = int(f.stars_30d)
        headline = f"{c.name} gained {stars:,} GitHub stars in 30 days without a single Hacker News thread"
        detail = (
            f"Star velocity is in the top {round(QUIET_TOP_FRACTION * 100)}% of tracked entities "
            f"(threshold {int(threshold):,}/30d). Hacker News was searched for its repository and domain: "
            "no stories in the last 90 days. X data unavailable."
        )
        rank = sum(1 for v in real if v <= f.stars_30d) / len(real) if real else 1.0
        sigs = [x for x in f.signals if x.source_type == "github"]
        out.append(
            ctx.make(
                "quiet_velocity",
                str(c.id),
                headline,
                detail,
                [_ent("company", c)],
                _sig_ev(sigs),
                0.5 + 0.5 * rank,
                bool(c.is_demo),
            )
        )
    return out


def hiring_pivot(ctx: _Ctx) -> list[dict]:
    end = datetime.combine(ctx.day, time.max, tzinfo=UTC)
    lo = end - timedelta(days=HIRING_WINDOW_DAYS)
    by_company: dict[uuid.UUID, list[Job]] = defaultdict(list)
    for j in ctx.session.scalars(select(Job).where(Job.first_seen_at <= end)):
        by_company[j.company_id].append(j)
    out = []
    for cid, jobs in by_company.items():
        prior = [j for j in jobs if job_start(j) <= lo]
        if not prior:
            continue  # first-ever hiring is not a pivot
        prior_classes = {job_class(j, ctx.thesis) for j in prior}
        new_by: dict[str, list[Job]] = defaultdict(list)
        for j in jobs:
            cls = job_class(j, ctx.thesis)
            if lo < job_start(j) <= end and is_technical_class(cls) and cls not in prior_classes:
                new_by[cls].append(j)
        for cls, new in new_by.items():
            if len(new) < MIN_CLUSTER:
                continue
            c = ctx.session.get(Company, cid)
            if c is None or c.dismissed:
                continue
            n, label = len(new), ROLE_CLASS_LABELS.get(cls, cls)
            headline = (
                f"{c.name} opened {number_word(n)} {label} roles in {HIRING_WINDOW_DAYS} days "
                f"— a role class it had never posted before"
            )
            detail = (
                f"New roles: {', '.join(j.title for j in new[:5])}. Previously posted classes: "
                f"{', '.join(sorted(ROLE_CLASS_LABELS.get(x, x or 'unclassified') for x in prior_classes))}."
            )
            evidence = [
                {
                    "signal_id": None,
                    "title": j.title,
                    "detail": j.department,
                    "source_type": "jobs",
                    "source_url": j.url,
                    "event_at": job_start(j),
                }
                for j in new
            ]
            sigs = [x for x in ctx.index.for_entities([cid]) if x.source_type == "jobs"]
            out.append(
                ctx.make(
                    "hiring_pivot",
                    f"{cid}:{cls}",
                    headline,
                    detail,
                    [_ent("company", c)],
                    evidence + _sig_ev(sigs, 3),
                    0.55 + 0.1 * (n - MIN_CLUSTER),
                    bool(c.is_demo or any(j.is_demo for j in new)),
                )
            )
    return out


def detect_strange_signals(
    session: Session, day: date | None = None, thesis: Thesis | None = None
) -> list[dict]:
    """All Strange Signals for ``day`` (StrangeSignal-shaped dicts), strongest first."""
    day = day or clock.today()
    detected_at = clock.now() if day == clock.today() else datetime.combine(day, time(12), tzinfo=UTC)
    ctx = _Ctx(
        session,
        day,
        thesis or get_thesis(),
        load_signal_index(session, day, SIGNAL_LOOKBACK_DAYS),
        detected_at,
    )
    young, people = _young_orgs(ctx)
    found = (
        shared_prior_employer_new_org(ctx, young, people)
        + lab_cluster(ctx, young, people)
        + paper_code_launch(ctx)
        + affiliation_exodus(ctx)
        + quiet_velocity(ctx)
        + hiring_pivot(ctx)
    )
    return sorted(found, key=lambda x: -x["strength"])
