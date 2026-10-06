"""Build scoring features from the database.

Time windows are read from ``MetricSnapshot`` series using the value at the
nearest day <= target, within a staleness tolerance (a baseline weeks older than
the window would silently inflate a "30-day" delta). If a repository was created
after a window's start, its baseline is 0 — new repos are measurable on day one.
Missing data stays ``None``; it is never coerced to 0.
"""

from __future__ import annotations

import uuid
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session

from radar.detectors.hiring import is_open, is_technical_class, job_class, job_start
from radar.models.core import (
    Company,
    CompanyPerson,
    EntityTopic,
    IngestionRun,
    Job,
    MetricSnapshot,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
    SignalEntity,
    Topic,
)
from radar.scoring.features import FORMATION_SIGNAL_TYPES, EntityFeatures, PersonFeatures, SignalLite
from radar.scoring.novelty import tokenize
from radar.thesis.loader import Thesis, get_thesis

KEY_CONTRIBUTOR_SHARE = 0.15  # contributions / top contributor's, to count a repo as 'key'
SIGNAL_WINDOW_DAYS = 120
YOUNG_ORG_DAYS = 180
SERIES_LOOKBACK_DAYS = 70
CURRENT_TOLERANCE_DAYS = 7  # "current" value may be at most this stale
WEEKLY_TOLERANCE_DAYS = 6
PAPER_WINDOW_DAYS = 730
REPO_METRICS = ("stars", "forks", "contributors", "commits_week", "releases")

Series = dict[tuple[uuid.UUID, str], list[tuple[date, float]]]


def day_end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


# ── Series helpers (pure) ────────────────────────────────────────────────────


def value_at(points: list[tuple[date, float]], target: date, tolerance_days: int) -> float | None:
    """Value at the nearest day <= target, if no older than ``tolerance_days``."""
    i = bisect_right(points, (target, float("inf")))
    if i == 0:
        return None
    d, v = points[i - 1]
    return v if (target - d).days <= tolerance_days else None


def window_delta(
    points: list[tuple[date, float]], day: date, end_offset: int, start_offset: int, created: date | None
) -> float | None:
    """Change of a cumulative metric between day-start_offset and day-end_offset."""
    end, start = day - timedelta(days=end_offset), day - timedelta(days=start_offset)
    if created is not None and created > end:
        return 0.0
    end_tol = CURRENT_TOLERANCE_DAYS if end_offset == 0 else 3
    v_end = value_at(points, end, end_tol)
    if v_end is None:
        return None
    if created is not None and created > start:
        return v_end
    v_start = value_at(points, start, max(3, (start_offset - end_offset) // 2))
    return None if v_start is None else v_end - v_start


def weekly_sum(
    points: list[tuple[date, float]], day: date, weeks: range, created: date | None
) -> float | None:
    """Sum of weekly samples (``commits_week``) at day-7k for k in ``weeks``; scaled if partially missing."""
    total, present = 0.0, 0
    for k in weeks:
        target = day - timedelta(days=7 * k)
        if created is not None and created > target:
            present += 1  # the repo did not exist yet: a true zero
            continue
        v = value_at(points, target, WEEKLY_TOLERANCE_DAYS)
        if v is not None:
            total += v
            present += 1
    if present < 2:
        return None
    return total * len(weeks) / present


def sum_known(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def org_created_date(founded: date | None, repo_created: Iterable[datetime | None]) -> date | None:
    """Best estimate of when an organization was created (never our first-seen date)."""
    dates = [d.date() for d in repo_created if d is not None]
    if founded is not None:
        dates.append(founded)
    return min(dates) if dates else None


# ── Shared loaders ───────────────────────────────────────────────────────────


@dataclass
class SignalIndex:
    """Signals in the scoring window keyed by every entity they concern."""

    by_entity: dict[uuid.UUID, dict[uuid.UUID, SignalLite]] = field(default_factory=lambda: defaultdict(dict))
    hn_available: bool = False
    press_available: bool = False

    def for_entities(self, ids: Iterable[uuid.UUID]) -> list[SignalLite]:
        merged: dict[uuid.UUID, SignalLite] = {}
        for i in ids:
            merged.update(self.by_entity.get(i, {}))
        return sorted(merged.values(), key=lambda s: s.event_at, reverse=True)


def load_signal_index(session: Session, day: date, window_days: int = SIGNAL_WINDOW_DAYS) -> SignalIndex:
    lo, hi = day_start(day - timedelta(days=window_days)), day_end(day)
    rows = session.execute(
        select(
            Signal.id,
            Signal.entity_id,
            Signal.signal_type,
            Signal.source_type,
            Signal.title,
            Signal.event_at,
            Signal.confidence,
            Signal.normalized_value,
            Signal.source_url,
            Signal.is_demo,
            Signal.raw_value,
        ).where(Signal.event_at >= lo, Signal.event_at <= hi)
    ).all()
    idx = SignalIndex()
    lites: dict[uuid.UUID, SignalLite] = {}
    for r in rows:
        s = SignalLite(
            r.id,
            r.signal_type,
            r.source_type,
            r.title,
            r.event_at,
            r.confidence if r.confidence is not None else 1.0,
            r.normalized_value,
            r.source_url,
            bool(r.is_demo),
            r.raw_value,
        )
        lites[r.id] = s
        idx.by_entity[r.entity_id][r.id] = s
    if lites:
        for sid, eid in session.execute(
            select(SignalEntity.signal_id, SignalEntity.entity_id)
            .join(Signal, Signal.id == SignalEntity.signal_id)
            .where(Signal.event_at >= lo, Signal.event_at <= hi)
        ).all():
            idx.by_entity[eid][sid] = lites[sid]
    idx.hn_available = bool(
        session.scalar(
            select(func.count())
            .select_from(IngestionRun)
            .where(
                IngestionRun.provider.ilike("%hackernews%") | IngestionRun.provider.ilike("hn%"),
                IngestionRun.status.in_(("ok", "partial")),
            )
        )
        or session.scalar(select(func.count()).select_from(Signal).where(Signal.source_type == "hackernews"))
    )
    idx.press_available = bool(
        session.scalar(select(func.count()).select_from(Signal).where(Signal.signal_type == "press_mention"))
    )
    return idx


def load_series(
    session: Session,
    ids: Iterable[uuid.UUID],
    metrics: Iterable[str],
    day: date,
    lookback_days: int = SERIES_LOOKBACK_DAYS,
) -> Series:
    ids = list(set(ids))
    out: Series = defaultdict(list)
    if not ids:
        return out
    rows = session.execute(
        select(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value)
        .where(
            MetricSnapshot.entity_id.in_(ids),
            MetricSnapshot.metric.in_(list(metrics)),
            MetricSnapshot.day <= day,
            MetricSnapshot.day >= day - timedelta(days=lookback_days),
        )
        .order_by(MetricSnapshot.day)
    ).all()
    for eid, metric, d, v in rows:
        out[(eid, metric)].append((d, float(v)))
    return out


def _topics(
    session: Session, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[str, str, float, list[str]]]]:
    by: dict[uuid.UUID, list[tuple[str, str, float, list[str]]]] = defaultdict(list)
    ids = list(set(ids))
    if ids:
        for eid, key, theme, score, ev in session.execute(
            select(EntityTopic.entity_id, Topic.key, Topic.theme, EntityTopic.score, EntityTopic.evidence)
            .join(Topic, Topic.id == EntityTopic.topic_id)
            .where(EntityTopic.entity_id.in_(ids))
        ).all():
            by[eid].append((key, theme, float(score), list(ev or [])))
    return by


def _merge_topics(rows: Iterable[tuple[str, str, float, list[str]]]):
    topic_scores: dict[str, float] = {}
    theme_scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    for key, theme, score, ev in rows:
        topic_scores[key] = max(topic_scores.get(key, 0.0), score)
        theme_scores[theme] = max(theme_scores.get(theme, 0.0), score)
        merged = evidence.setdefault(key, [])
        merged.extend(k for k in ev if k not in merged)
    return topic_scores, theme_scores, evidence


def _repo_created(r: Repository) -> date | None:
    return r.gh_created_at.date() if r.gh_created_at else None


# ── Companies ────────────────────────────────────────────────────────────────


def build_company_features(
    session: Session,
    day: date,
    index: SignalIndex | None = None,
    thesis: Thesis | None = None,
    *,
    reconstruct: bool = False,
) -> list[EntityFeatures]:
    """Features for every non-dismissed company (incl. 'project' orgs) as of ``day``.

    ``reconstruct`` includes entities first seen after ``day`` — used to rebuild score
    history from backfilled time series (only data dated <= day is used either way)."""
    thesis = thesis or get_thesis()
    index = index or load_signal_index(session, day)
    companies = list(
        session.scalars(
            select(Company).where(
                Company.dismissed.is_(False), true() if reconstruct else Company.first_seen_at <= day_end(day)
            )
        )
    )
    if not companies:
        return []
    cids = [c.id for c in companies]
    repos_by: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in session.scalars(select(Repository).where(Repository.company_id.in_(cids))):
        repos_by[r.company_id].append(r)
    people_by: dict[uuid.UUID, list[CompanyPerson]] = defaultdict(list)
    for cp in session.scalars(
        select(CompanyPerson).where(
            CompanyPerson.company_id.in_(cids),
            or_(CompanyPerson.end_at.is_(None), CompanyPerson.end_at >= day),
        )
    ):
        people_by[cp.company_id].append(cp)
    all_repo_ids = [r.id for rs in repos_by.values() for r in rs]
    series = load_series(session, all_repo_ids, REPO_METRICS, day)
    company_series = load_series(
        session, cids, ("followers", "hn_probe_mentions", "hn_probe_points"), day, lookback_days=30
    )
    hist_cut = day - timedelta(days=7)
    with_history = {eid for (eid, _), pts in series.items() if pts and pts[0][0] <= hist_cut}
    topics = _topics(session, cids + all_repo_ids)
    jobs_by: dict[uuid.UUID, list[Job]] = defaultdict(list)
    for j in session.scalars(select(Job).where(Job.company_id.in_(cids))):
        jobs_by[j.company_id].append(j)
    new_contrib = dict(
        session.execute(
            select(Repository.company_id, func.count(func.distinct(RepoContributor.person_id)))
            .join(Repository, Repository.id == RepoContributor.repository_id)
            .where(
                Repository.company_id.in_(cids),
                RepoContributor.first_commit_at >= day_start(day - timedelta(days=30)),
                RepoContributor.first_commit_at <= day_end(day),
            )
            .group_by(Repository.company_id)
        ).all()
    )

    lo90 = day_start(day - timedelta(days=90))
    out: list[EntityFeatures] = []
    for c in companies:
        repos = repos_by.get(c.id, [])
        links = people_by.get(c.id, [])
        founders = [cp for cp in links if cp.is_founder] or links
        ser = {r.id: (r, {m: series.get((r.id, m), []) for m in REPO_METRICS}) for r in repos}

        def agg(metric: str, end: int, start: int, ser=ser) -> float | None:
            return sum_known(
                window_delta(s[metric], day, end, start, _repo_created(r)) for r, s in ser.values()
            )

        stars_total = (
            sum_known(_current(s["stars"], day, float(r.stars or 0)) for r, s in ser.values())
            if repos
            else None
        )
        contributors_total = sum_known(
            _current(s["contributors"], day, r.contributors_count) for r, s in ser.values()
        )
        new_contributors = agg("contributors", 0, 30)
        if new_contributors is None and c.id in new_contrib:
            new_contributors = float(new_contrib[c.id])

        ids = [c.id, *ser.keys(), *(cp.person_id for cp in links)]
        signals = index.for_entities(ids)
        hn = [s for s in signals if s.source_type == "hackernews" and s.event_at >= lo90]
        # HN attention is "available" for an entity only if it was measured: HN signals in our corpus,
        # or an explicit attention probe (ops.probe_attention). No data is not the same as zero.
        hn_points = None
        hn_mentions = None
        probe_m = company_series.get((c.id, "hn_probe_mentions"), [])
        probe_p = company_series.get((c.id, "hn_probe_points"), [])
        if hn or probe_m:
            hn_points = float(sum(s.raw_value or 0.0 for s in hn))  # HN signals carry points as raw_value
            hn_mentions = len(hn)
            if probe_m:
                hn_mentions = max(hn_mentions, int(value_at(probe_m, day, 30) or 0))
                hn_points = max(hn_points, float(value_at(probe_p, day, 30) or 0.0)) if probe_p else hn_points
        releases = sum(1 for s in signals if "release" in s.signal_type and s.event_at >= lo90)
        release_metric = agg("releases", 0, 90)

        jobs = jobs_by.get(c.id, [])
        open_roles = technical_30d = None
        if jobs:
            open_roles = sum(1 for j in jobs if is_open(j, day))
            lo30 = day_start(day - timedelta(days=30))
            technical_30d = sum(
                1
                for j in jobs
                if lo30 <= job_start(j) <= day_end(day) and is_technical_class(job_class(j, thesis))
            )
        topic_scores, theme_scores, topic_ev = _merge_topics(
            row for eid in (c.id, *ser.keys()) for row in topics.get(eid, [])
        )
        x_pts = company_series.get((c.id, "followers"), [])
        out.append(
            EntityFeatures(
                entity_id=c.id,
                name=c.name,
                first_seen_at=c.first_seen_at,
                stage=c.stage or "unknown",
                kind=c.kind or "company",
                stars_total=stars_total,
                stars_7d=agg("stars", 0, 7),
                stars_30d=agg("stars", 0, 30),
                stars_prev_28d=agg("stars", 7, 35),
                forks_30d=agg("forks", 0, 30),
                commits_4w=sum_known(
                    weekly_sum(s["commits_week"], day, range(4), _repo_created(r)) for r, s in ser.values()
                ),
                commits_prev_4w=sum_known(
                    weekly_sum(s["commits_week"], day, range(4, 8), _repo_created(r)) for r, s in ser.values()
                ),
                new_contributors_30d=new_contributors,
                contributors_total=contributors_total,
                releases_90d=release_metric
                if release_metric is not None
                else (float(releases) if repos else None),
                open_roles=open_roles,
                technical_roles_30d=technical_30d,
                hn_points_90d=hn_points,
                hn_mentions_90d=hn_mentions,
                x_followers=value_at(x_pts, day, 30) if x_pts else None,
                press_mentions=sum(1 for s in signals if s.signal_type == "press_mention")
                if index.press_available
                else None,
                topic_scores=topic_scores,
                theme_scores=theme_scores,
                topic_evidence=topic_ev,
                founder_ids=[cp.person_id for cp in founders],
                signals=signals,
                tokens=tokenize(
                    c.description, *(r.description for r in repos), *(" ".join(r.topics or []) for r in repos)
                ),
                is_demo=bool(c.is_demo),
                has_history=any(r.id in with_history for r in repos),
            )
        )
    return out


def _current(points: list[tuple[date, float]], day: date, fallback: float | None) -> float | None:
    """Latest snapshot value, else the entity row's current column."""
    v = value_at(points, day, CURRENT_TOLERANCE_DAYS)
    return v if v is not None else (float(fallback) if fallback is not None else None)


# ── People ───────────────────────────────────────────────────────────────────


def build_person_features(
    session: Session,
    day: date,
    index: SignalIndex | None = None,
    thesis: Thesis | None = None,
    *,
    reconstruct: bool = False,
) -> list[PersonFeatures]:
    thesis = thesis or get_thesis()
    index = index or load_signal_index(session, day)
    people = list(
        session.scalars(
            select(Person).where(
                Person.dismissed.is_(False), true() if reconstruct else Person.first_seen_at <= day_end(day)
            )
        )
    )
    if not people:
        return []
    pids = [p.id for p in people]
    handles = {p.github_handle.lower(): p.id for p in people if p.github_handle}
    owned: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    if handles:
        for r in session.scalars(select(Repository).where(func.lower(Repository.owner).in_(list(handles)))):
            owned[handles[r.owner.lower()]].append(r)
    person_series = load_series(session, pids, ("followers", "commits_week"), day)

    contribs: dict[uuid.UUID, list[tuple[RepoContributor, Repository]]] = defaultdict(list)
    for rc, r in session.execute(
        select(RepoContributor, Repository)
        .join(Repository, Repository.id == RepoContributor.repository_id)
        .where(RepoContributor.person_id.in_(pids))
    ).all():
        contribs[rc.person_id].append((rc, r))

    # Key repositories: owned (share 1.0) + repos where the person is a core contributor,
    # weighted by contributions relative to the repo's top recorded contributor.
    top_contrib: dict[uuid.UUID, int] = {}
    repo_ids_all = {r.id for rows in contribs.values() for _, r in rows}
    if repo_ids_all:
        for rid, mx in session.execute(
            select(RepoContributor.repository_id, func.max(RepoContributor.contributions))
            .where(RepoContributor.repository_id.in_(list(repo_ids_all)))
            .group_by(RepoContributor.repository_id)
        ):
            top_contrib[rid] = int(mx or 0)
    key_repos: dict[uuid.UUID, list[tuple[Repository, float]]] = defaultdict(list)
    for pid in pids:
        seen: set[uuid.UUID] = set()
        for r in owned.get(pid, []):
            key_repos[pid].append((r, 1.0))
            seen.add(r.id)
        for rc, r in contribs.get(pid, []):
            if r.id in seen:
                continue
            top = top_contrib.get(r.id) or 0
            share = (rc.contributions or 0) / top if top else 0.0
            if share >= KEY_CONTRIBUTOR_SHARE:
                key_repos[pid].append((r, min(1.0, share)))
                seen.add(r.id)
    key_ids = list({r.id for rows in key_repos.values() for r, _ in rows})
    series = load_series(session, key_ids, ("stars", "commits_week"), day)

    # Organization creation dates, for "joined a young org".
    org_ids = {r.company_id for rows in contribs.values() for _, r in rows if r.company_id}
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for cp in session.scalars(select(CompanyPerson).where(CompanyPerson.person_id.in_(pids))):
        links[cp.person_id].append(cp.company_id)
        org_ids.add(cp.company_id)
    org_info = _org_created(session, org_ids)

    lo24 = day_start(day - timedelta(days=PAPER_WINDOW_DAYS))
    papers: dict[uuid.UUID, list[ResearchPaper]] = defaultdict(list)
    for pid, paper in session.execute(
        select(PaperAuthor.person_id, ResearchPaper)
        .join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
        .where(
            PaperAuthor.person_id.in_(pids),
            ResearchPaper.published_at >= lo24,
            ResearchPaper.published_at <= day_end(day),
        )
    ).all():
        papers[pid].append(paper)
    topics = _topics(session, pids + key_ids + [p.id for ps in papers.values() for p in ps])

    young_cut = day - timedelta(days=YOUNG_ORG_DAYS)
    out: list[PersonFeatures] = []
    for p in people:
        repos = owned.get(p.id, [])
        keyed = key_repos.get(p.id, [])
        signals = index.for_entities([p.id, *(r.id for r, _ in keyed)])
        transition = [s.id for s in signals if s.signal_type in FORMATION_SIGNAL_TYPES]
        affiliations = list(
            dict.fromkeys(
                a.strip() for a in [p.current_company, *(p.prior_affiliations or [])] if a and a.strip()
            )
        )
        best_tier, best_match = 0.0, None
        for a in affiliations:
            t = thesis.affiliation_tier(a)
            if t > best_tier:
                best_tier, best_match = t, thesis.matched_affiliation(a)
        young = sorted(
            {
                org_info[o][1]
                for o in {
                    *links.get(p.id, []),
                    *(r.company_id for _, r in contribs.get(p.id, []) if r.company_id),
                }
                if o in org_info and org_info[o][0] is not None and org_info[o][0] >= young_cut
            }
        )
        commits = weekly_sum(person_series.get((p.id, "commits_week"), []), day, range(4), None)
        if commits is None and keyed:
            # Proxy: each key repo's last-4-week commits, weighted by the person's contribution share.
            commits = sum_known(
                (c * w if (c := weekly_sum(series.get((r.id, "commits_week"), []), day, range(4),
                                           _repo_created(r))) is not None else None)
                for r, w in keyed
            )
        fol_pts = person_series.get((p.id, "followers"), [])
        followers = value_at(fol_pts, day, 30) if fol_pts else None
        if followers is None and p.followers is not None:
            followers = float(p.followers)
        pp = papers.get(p.id, [])
        topic_scores, theme_scores, topic_ev = _merge_topics(
            row for eid in (p.id, *(r.id for r in repos), *(x.id for x in pp)) for row in topics.get(eid, [])
        )
        out.append(
            PersonFeatures(
                person_id=p.id,
                name=p.name,
                first_seen_at=p.first_seen_at,
                followers=followers,
                owned_repo_stars=float(sum((r.stars or 0) * w for r, w in keyed)) if keyed else None,
                recent_repo_stars_30d=sum_known(
                    (d * w if (d := window_delta(series.get((r.id, "stars"), []), day, 0, 30, _repo_created(r)))
                     is not None else None)
                    for r, w in keyed
                ),
                key_repos=[r.full_name for r, _ in keyed][:5],
                commits_30d=commits,
                papers_24mo=len(pp),
                papers_with_code=sum(1 for x in pp if x.code_urls),
                affiliations=affiliations,
                tier=best_tier,
                tier_match=best_match,
                recent_transition=bool(transition),
                transition_evidence=transition,
                joined_young_org=bool(young),
                young_org_names=young,
                topic_scores=topic_scores,
                theme_scores=theme_scores,
                topic_evidence=topic_ev,
                signals=signals,
                is_demo=bool(p.is_demo),
            )
        )
    return out


def _org_created(session: Session, org_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[date | None, str]]:
    if not org_ids:
        return {}
    repo_dates: dict[uuid.UUID, list[datetime | None]] = defaultdict(list)
    for cid, created in session.execute(
        select(Repository.company_id, Repository.gh_created_at).where(
            Repository.company_id.in_(list(org_ids))
        )
    ).all():
        repo_dates[cid].append(created)
    return {
        c.id: (org_created_date(c.founded_date, repo_dates.get(c.id, [])), c.name)
        for c in session.scalars(select(Company).where(Company.id.in_(list(org_ids))))
    }
