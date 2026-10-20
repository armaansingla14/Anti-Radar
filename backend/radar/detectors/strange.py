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
from radar.detectors.hiring import is_open, is_technical_class, job_class, job_start
from radar.models.core import (
    Company,
    CompanyPerson,
    Job,
    PaperAuthor,
    Person,
    Relationship,
    RepoContributor,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
)
from radar.providers.domains import github_repos_in
from radar.scoring.cohort import ordinal
from radar.scoring.collision import number_word
from radar.scoring.extract import (
    SignalIndex,
    build_company_features,
    load_signal_index,
    org_created_date,
    person_lab_contributions,
)
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
# credible_engineers_tiny_repo
TINY_REPO_MAX_STARS = 50
TINY_ORG_DAYS = 90
TALENT_MIN_COMMITS = 3  # commits to the tiny repo for a talent-pool person to count (owner always counts)
TALENT_MIN_PEOPLE = 2
TALENT_RECENT_DAYS = 180  # the repo must have been created, or committed to by them, this recently
# code_and_jobs
CODE_JOBS_WINDOW_DAYS = 30
CODE_SIGNAL_TYPES = ("release_published", "repo_created")
# quiet_specialized_hiring
SPECIALIZED_CLASSES = ("silicon", "robotics_controls", "gpu_compiler", "rf_defense", "energy_plasma")
SPECIALIZED_MIN_ROLES = 2
# former_coworkers_converging
PRIOR_TIE_KINDS = ("COAUTHORED_WITH", "CONTRIBUTED_WITH")
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


# ── Talent / hiring / convergence patterns (M5) ──────────────────────────────


def _aware(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def _repo_ev(r: Repository, detail: str) -> dict[str, Any]:
    return {
        "signal_id": None,
        "title": r.full_name,
        "detail": detail,
        "source_type": "github",
        "source_url": f"https://github.com/{r.full_name}",
        "event_at": r.gh_created_at,
    }


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _lab_orgs(people: list[Person]) -> set[str]:
    return {lc.org.lower() for p in people for lc in person_lab_contributions(p)}


def credible_engineers_tiny_repo(ctx: _Ctx) -> list[dict]:
    """>= 2 talent-pool people (verified frontier-lab commit history, ``Person.attrs['lab_contributions']``)
    committing to a repository with < 50 stars or one owned by an organization < 90 days old."""
    from radar.pipeline.relationships import lab_source
    from radar.providers.github import is_bot

    s, day = ctx.session, ctx.day
    end = _end(day)
    talent = {
        p.id: p
        for p in s.scalars(select(Person).where(Person.dismissed.is_(False)))
        if person_lab_contributions(p) and not is_bot(p.github_handle or p.name)
    }
    if len(talent) < TALENT_MIN_PEOPLE:
        return []
    lab_orgs = _lab_orgs(list(talent.values()))
    rows = s.execute(
        select(RepoContributor, Repository)
        .join(Repository, Repository.id == RepoContributor.repository_id)
        .where(RepoContributor.person_id.in_(list(talent)))
    ).all()
    by_repo: dict[uuid.UUID, list[RepoContributor]] = defaultdict(list)
    repos: dict[uuid.UUID, Repository] = {}
    for rc, r in rows:
        repos[r.id] = r
        by_repo[r.id].append(rc)
    cids = {r.company_id for r in repos.values() if r.company_id}
    companies = {c.id: c for c in s.scalars(select(Company).where(Company.id.in_(list(cids))))} if cids else {}
    repo_dates: dict[uuid.UUID, list[datetime | None]] = defaultdict(list)
    if cids:
        for cid, created in s.execute(
            select(Repository.company_id, Repository.gh_created_at).where(Repository.company_id.in_(list(cids)))
        ).all():
            repo_dates[cid].append(created)
    recent = end - timedelta(days=TALENT_RECENT_DAYS)
    out = []
    for rid, rcs in by_repo.items():
        r = repos[rid]
        if lab_source(r) or (r.owner or "").lower() in lab_orgs:
            continue  # the lab repositories themselves are the pedigree, not the anomaly
        c = companies.get(r.company_id) if r.company_id else None
        if c is not None and c.dismissed:
            continue
        org_age = None
        if c is not None:
            created = org_created_date(c.founded_date, repo_dates.get(c.id, []))
            org_age = (day - created).days if created is not None else None
        tiny = (r.stars or 0) < TINY_REPO_MAX_STARS
        young = org_age is not None and 0 <= org_age < TINY_ORG_DAYS
        if not (tiny or young):
            continue
        created_at = _aware(r.gh_created_at)
        if created_at is not None and created_at > end:
            continue
        members = []
        for rc in rcs:
            p = talent[rc.person_id]
            owner = bool(p.github_handle and p.github_handle.lower() == (r.owner or "").lower())
            if (rc.contributions or 0) < TALENT_MIN_COMMITS and not owner:
                continue
            last = _aware(rc.last_commit_at)
            if last is not None and last > end:
                last = None  # commit history recorded after the as-of day is not evidence for it
            is_recent = (last is not None and last >= recent) or (created_at is not None and created_at >= recent)
            if is_recent:
                members.append((p, rc, owner))
        if len(members) < TALENT_MIN_PEOPLE:
            continue
        members.sort(key=lambda m: (-person_lab_contributions(m[0])[0].strength, m[0].name))
        n = len(members)
        labs = []
        for p, _, _ in members:
            fn = person_lab_contributions(p)[0].full_name
            if fn not in labs:
                labs.append(fn)
        where = (
            f"a {age_phrase(org_age)} {_org_noun(c)}'s repository {r.full_name}"
            if young and c is not None
            else f"{r.full_name}, a {int(r.stars or 0):,}-star repository"
        )
        headline = (
            f"{number_word(n, True)} engineers with verified commits to "
            f"{' and '.join(labs[:2])}{' and others' if len(labs) > 2 else ''} are committing to {where}"
        )
        detail = (
            "; ".join(
                f"{p.name}: {person_lab_contributions(p)[0].phrase(verb=False)}; "
                + ("owner of" if owner else f"{rc.contributions} commits to") + f" {r.full_name}"
                for p, rc, owner in members[:5]
            )
            + ". Public commit history — not employment."
        )
        evidence = [_repo_ev(r, f"{int(r.stars or 0):,} stars" + (f"; org ~{org_age} days old" if young else ""))]
        evidence += [
            _person_ev(p, f"{person_lab_contributions(p)[0].phrase(verb=False)}; {rc.contributions} commits here")
            for p, rc, _ in members
        ]
        ids = [r.id] + ([c.id] if c else []) + [p.id for p, _, _ in members]
        evidence += _sig_ev(ctx.index.for_entities(ids), 3)
        avg_lab = sum(person_lab_contributions(p)[0].strength for p, _, _ in members) / n
        strength = 0.5 + 0.1 * (n - TALENT_MIN_PEOPLE) + 0.25 * avg_lab + (0.1 if young else 0.0)
        ents = ([_ent("company", c)] if c else []) + [_ent("repository", r)] + [_ent("person", p) for p, _, _ in members]
        out.append(
            ctx.make(
                "credible_engineers_tiny_repo",
                str(r.id),
                headline,
                detail,
                ents,
                evidence,
                strength,
                bool(r.is_demo or (c is not None and c.is_demo) or any(p.is_demo for p, _, _ in members)),
            )
        )
    return out


def code_and_jobs(ctx: _Ctx) -> list[dict]:
    """An organization released code (new repository / release) and opened technical roles, all within the
    last 30 days."""
    s, day = ctx.session, ctx.day
    end = _end(day)
    lo = end - timedelta(days=CODE_JOBS_WINDOW_DAYS)
    jobs_by: dict[uuid.UUID, list[Job]] = defaultdict(list)
    for j in s.scalars(select(Job).where(Job.first_seen_at <= end)):
        if lo < job_start(j) <= end and is_open(j, day) and is_technical_class(job_class(j, ctx.thesis)):
            jobs_by[j.company_id].append(j)
    if not jobs_by:
        return []
    companies = {c.id: c for c in s.scalars(select(Company).where(Company.id.in_(list(jobs_by))))}
    repos_by: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in s.scalars(select(Repository).where(Repository.company_id.in_(list(jobs_by)))):
        repos_by[r.company_id].append(r)
    out = []
    for cid, jobs in jobs_by.items():
        c = companies.get(cid)
        if c is None or c.dismissed:
            continue
        repos = repos_by.get(cid, [])
        code: list[dict[str, Any]] = []
        seen_repo: set[str] = set()
        for x in ctx.index.for_entities([cid, *(r.id for r in repos)]):
            ev = _aware(x.event_at)
            if x.signal_type in CODE_SIGNAL_TYPES and ev is not None and lo < ev <= end:
                code.append(x.evidence())
                if x.signal_type == "repo_created":
                    seen_repo.add((x.title or "").lower())
        for r in repos:
            ca = _aware(r.gh_created_at)
            if ca is not None and lo < ca <= end and not any(r.full_name.lower() in t for t in seen_repo):
                code.append(_repo_ev(r, "New public repository"))
        if not code:
            continue
        times = [_parse_dt(e["event_at"]) for e in code] + [_aware(job_start(j)) for j in jobs]
        times = [t for t in times if t is not None]
        span = max(1, math.ceil((max(times) - min(times)).total_seconds() / 86400))
        n = len(jobs)
        classes = sorted({ROLE_CLASS_LABELS.get(job_class(j, ctx.thesis) or "", job_class(j, ctx.thesis) or "technical")
                          for j in jobs})
        n_code = len(code)
        headline = (
            f"{c.name} released code and opened {number_word(n)} technical role{'s' if n != 1 else ''} "
            f"within {span} days"
        )
        detail = (
            f"Code: {'; '.join(str(e['title']) for e in code[:3])}. Roles ({', '.join(classes)}): "
            f"{', '.join(j.title for j in jobs[:5])}."
        )
        job_ev = [
            {
                "signal_id": None,
                "title": j.title,
                "detail": j.department,
                "source_type": "jobs",
                "source_url": j.url,
                "event_at": job_start(j),
            }
            for j in sorted(jobs, key=job_start, reverse=True)[:5]
        ]
        strength = 0.45 + 0.05 * min(n, 5) + 0.05 * min(n_code, 3) + 0.15 * (1 - span / CODE_JOBS_WINDOW_DAYS)
        out.append(
            ctx.make(
                "code_and_jobs",
                f"{cid}:{min(times).date().isoformat()}",
                headline,
                detail,
                [_ent("company", c)],
                code[:5] + job_ev,
                strength,
                bool(c.is_demo or any(j.is_demo for j in jobs)),
            )
        )
    return out


def quiet_specialized_hiring(ctx: _Ctx) -> list[dict]:
    """>= 2 open specialized technical roles (silicon, robotics & controls, GPU/compiler, RF/defense,
    energy/plasma) posted in the last 30 days at an organization whose public attention was *measured* low."""
    s, day = ctx.session, ctx.day
    end = _end(day)
    lo = end - timedelta(days=HIRING_WINDOW_DAYS)
    by: dict[uuid.UUID, list[tuple[Job, str]]] = defaultdict(list)
    for j in s.scalars(select(Job).where(Job.first_seen_at <= end)):
        cls = job_class(j, ctx.thesis)
        if cls in SPECIALIZED_CLASSES and lo < job_start(j) <= end and is_open(j, day):
            by[j.company_id].append((j, cls))
    by = {cid: js for cid, js in by.items() if len(js) >= SPECIALIZED_MIN_ROLES}
    if not by:
        return []
    snaps: dict[uuid.UUID, ScoreSnapshot] = {}
    for snap in s.scalars(
        select(ScoreSnapshot)
        .where(ScoreSnapshot.entity_id.in_(list(by)), ScoreSnapshot.day <= day)
        .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc())
        .distinct(ScoreSnapshot.entity_id)
    ):
        snaps[snap.entity_id] = snap
    max_att = ctx.thesis.scoring.attention_gap.flag_attention_max * 100
    companies = {c.id: c for c in s.scalars(select(Company).where(Company.id.in_(list(by))))}
    out = []
    for cid, js in by.items():
        c, snap = companies.get(cid), snaps.get(cid)
        if c is None or c.dismissed or snap is None:
            continue
        att = (snap.attention or {}).get("attention")
        if not isinstance(att, int | float) or att > max_att:
            continue  # unmeasured attention is unknown, not low
        n = len(js)
        labels = sorted({ROLE_CLASS_LABELS.get(cls, cls) for _, cls in js})
        measured = [x.get("label") for x in (snap.attention or {}).get("sources") or [] if x.get("available")]
        headline = (
            f"{c.name} opened {number_word(n)} specialized {' / '.join(labels)} roles in {HIRING_WINDOW_DAYS} days "
            f"while public attention sits at the {ordinal(att)} percentile"
        )
        detail = (
            f"Roles: {', '.join(j.title for j, _ in js[:5])}. Public attention measured from "
            f"{', '.join(m for m in measured if m) or 'available sources'} (as of {snap.day.isoformat()})."
        )
        evidence = [
            {
                "signal_id": None,
                "title": j.title,
                "detail": ROLE_CLASS_LABELS.get(cls, cls),
                "source_type": "jobs",
                "source_url": j.url,
                "event_at": job_start(j),
            }
            for j, cls in sorted(js, key=lambda x: job_start(x[0]), reverse=True)[:6]
        ]
        strength = 0.5 + 0.08 * min(n - SPECIALIZED_MIN_ROLES, 4) + 0.2 * (1 - att / max_att if max_att else 0)
        out.append(
            ctx.make(
                "quiet_specialized_hiring",
                f"{cid}:{'+'.join(sorted({cls for _, cls in js}))}",
                headline,
                detail,
                [_ent("company", c)],
                evidence,
                strength,
                bool(c.is_demo or any(j.is_demo for j, _ in js)),
            )
        )
    return out


def _parse_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return _aware(v)
    if isinstance(v, str) and v:
        try:
            return _aware(datetime.fromisoformat(v))
        except ValueError:
            return None
    return None


def _tie_phrase(kind: str, ev: dict[str, Any]) -> str:
    t = str(ev.get("title") or "").strip()
    if kind == "COAUTHORED_WITH":
        return f"co-authored “{t}”" if t else "co-authored a paper"
    return f"contributed to {t} together" if t else "contributed to the same repository"


def former_coworkers_converging(
    ctx: _Ctx, young: list[_YoungOrg], people: dict[uuid.UUID, Person]
) -> list[dict]:
    """>= 2 people with a VERIFIED prior tie (COAUTHORED_WITH / CONTRIBUTED_WITH evidence dated before the
    unit existed, about something other than the unit) now contributing to the same young org or repository."""
    s, day = ctx.session, ctx.day
    units: list[tuple[str, Any, int, datetime, dict[uuid.UUID, str], set[str]]] = []
    for y in young:
        repo_ids = {str(r) for r in s.scalars(select(Repository.id).where(Repository.company_id == y.company.id))}
        created = datetime.combine(day - timedelta(days=y.age_days), time.min, tzinfo=UTC)
        units.append(("company", y.company, y.age_days, created, dict(y.people), repo_ids | {str(y.company.id)}))
    lo = datetime.combine(day - timedelta(days=YOUNG_ORG_DAYS), time.min, tzinfo=UTC)
    for r in s.scalars(
        select(Repository).where(
            Repository.company_id.is_(None), Repository.gh_created_at >= lo, Repository.gh_created_at <= _end(day)
        )
    ):
        pids = {pid: "contributor" for pid in s.scalars(
            select(RepoContributor.person_id).where(RepoContributor.repository_id == r.id))}
        if len(pids) >= 2:
            ca = _aware(r.gh_created_at)
            units.append(("repository", r, (day - ca.date()).days, ca, pids, {str(r.id)}))
    if not units:
        return []
    all_pids = {pid for u in units for pid in u[4]}
    rels = list(
        s.scalars(
            select(Relationship).where(
                Relationship.kind.in_(PRIOR_TIE_KINDS),
                Relationship.epistemic == "VERIFIED",
                Relationship.src_id.in_(list(all_pids)),
                Relationship.dst_id.in_(list(all_pids)),
            )
        )
    ) if len(all_pids) >= 2 else []
    if not rels:
        return []
    missing = all_pids - set(people)
    if missing:
        people = {**people, **{p.id: p for p in s.scalars(select(Person).where(Person.id.in_(list(missing))))}}
    out = []
    for kind, obj, age, created, members, own_ids in units:
        ties: list[tuple[Relationship, dict[str, Any], datetime]] = []
        for rel in rels:
            if rel.src_id not in members or rel.dst_id not in members:
                continue
            prior = []
            for ev in rel.evidence or []:
                at = _parse_dt(ev.get("event_at"))
                about_unit = str(ev.get("repo_id") or "") in own_ids or str(ev.get("company_id") or "") in own_ids
                if at is not None and at < created and not about_unit:
                    prior.append((ev, at))
            if prior:
                ev, at = min(prior, key=lambda x: x[1])
                ties.append((rel, ev, at))
        if not ties:
            continue
        pids = sorted({p for rel, _, _ in ties for p in (rel.src_id, rel.dst_id) if p in people},
                      key=lambda pid: people[pid].name)
        if len(pids) < 2:
            continue
        n = len(pids)
        noun = _org_noun(obj) if kind == "company" else "repository"
        verb = _relation_verb([members[pid] for pid in pids])
        if len(ties) == 1:
            rel, ev, _ = ties[0]
            who = f"people who previously {_tie_phrase(rel.kind, ev)}"
        else:
            who = "people with verified prior collaborations"
        headline = f"{number_word(n, True)} {who} {verb} the same {age_phrase(max(age, 1))} {noun}"
        detail = "; ".join(
            f"{people[rel.src_id].name} & {people[rel.dst_id].name} {_tie_phrase(rel.kind, ev)} ({at.date().isoformat()})"
            for rel, ev, at in sorted(ties, key=lambda t: t[2])[:5]
            if rel.src_id in people and rel.dst_id in people
        ) + f". {obj.name if kind == 'company' else obj.full_name} was created ~{age} days ago."
        evidence = [
            {
                "signal_id": None,
                "title": str(ev.get("title") or rel.kind),
                "detail": ev.get("detail"),
                "source_type": ev.get("source_type") or ("arxiv" if rel.kind == "COAUTHORED_WITH" else "github"),
                "source_url": ev.get("source_url"),
                "event_at": at,
            }
            for rel, ev, at in ties[:5]
        ]
        ent_kind = "company" if kind == "company" else "repository"
        evidence += _sig_ev(ctx.index.for_entities([obj.id, *pids]), 3)
        strength = 0.5 + 0.1 * min(n - 2, 3) + 0.05 * min(len(ties), 4) + 0.15 * (1 - min(age, YOUNG_ORG_DAYS) / YOUNG_ORG_DAYS)
        out.append(
            ctx.make(
                "former_coworkers_converging",
                f"{obj.id}:{'+'.join(sorted(str(p) for p in pids))}",
                headline,
                detail,
                [_ent(ent_kind, obj)] + [_ent("person", people[pid]) for pid in pids],
                evidence,
                strength,
                bool(obj.is_demo or any(people[pid].is_demo for pid in pids) or any(r.is_demo for r, _, _ in ties)),
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
    labs = lab_cluster(ctx, young, people)
    # A lab cluster (>= 3 co-authors) already tells the convergence story for that org.
    lab_orgs = {e["id"] for f in labs for e in f["entities"] if e["type"] == "company"}
    converging = [
        f for f in former_coworkers_converging(ctx, young, people)
        if not any(e["type"] == "company" and e["id"] in lab_orgs for e in f["entities"])
    ]
    found = (
        shared_prior_employer_new_org(ctx, young, people)
        + labs
        + paper_code_launch(ctx)
        + affiliation_exodus(ctx)
        + quiet_velocity(ctx)
        + hiring_pivot(ctx)
        + credible_engineers_tiny_repo(ctx)
        + code_and_jobs(ctx)
        + quiet_specialized_hiring(ctx)
        + converging
    )
    return sorted(found, key=lambda x: -x["strength"])
