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
from radar.scoring.features import (
    FORMATION_EVENT_TYPES,
    FORMATION_SIGNAL_TYPES,
    LAB_ALUMNI_CLUSTER,
    TRANSITION_SIGNAL_TYPES,
    EntityFeatures,
    PersonFeatures,
    SignalLite,
    parse_lab_contributions,
)
from radar.scoring.novelty import tokenize
from radar.scoring.velocity import aggregate_velocity, compute_velocity
from radar.thesis.loader import Thesis, get_thesis

KEY_CONTRIBUTOR_SHARE = 0.15  # contributions / top contributor's, to count a repo as 'key'
SIGNAL_WINDOW_DAYS = 120
YOUNG_ORG_DAYS = 180
SERIES_LOOKBACK_DAYS = 70
CURRENT_TOLERANCE_DAYS = 7  # "current" value may be at most this stale
START_TOLERANCE_DAYS = 3  # a window's baseline may be at most this much older than the window start
WEEKLY_TOLERANCE_DAYS = 6
PAPER_WINDOW_DAYS = 730
REPO_METRICS = ("stars", "forks", "commits_week", "releases")
# 'contributors' = cumulative distinct authors by first-commit week (GitHub backfill);
# 'contributors_total' = the /contributors count (includes bots; a different measure).
CONTRIBUTOR_METRICS = ("contributors", "contributors_total")
VELOCITY_METRICS = ("stars", "commits_week", "contributors", "contributors_total", "releases_total", "open_issues")
VELOCITY_LOOKBACK_DAYS = 100  # 90-day release window + endpoint tolerance

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
    points: list[tuple[date, float]],
    day: date,
    end_offset: int,
    start_offset: int,
    created: date | None,
    *,
    zero_at_creation: bool = True,
) -> float | None:
    """Change of a cumulative metric between day-start_offset and day-end_offset.

    The baseline must lie within ``START_TOLERANCE_DAYS`` of the window start, so a "30-day"
    delta never silently spans 45 days. With ``zero_at_creation`` (stars, forks: a new repo
    starts at 0) a repo created inside the window uses 0 as baseline, and a repo created
    shortly before the window start whose first snapshot lands after it uses the value
    interpolated between 0 at creation and that first snapshot (an approximation that
    under- rather than over-counts). Contributor counts must pass ``zero_at_creation=False``:
    a repo created from existing history already has contributors on day one."""
    end, start = day - timedelta(days=end_offset), day - timedelta(days=start_offset)
    if created is not None and created > end:
        return 0.0
    end_tol = CURRENT_TOLERANCE_DAYS if end_offset == 0 else START_TOLERANCE_DAYS
    v_end = value_at(points, end, end_tol)
    if v_end is None:
        return None
    if zero_at_creation and created is not None and created > start:
        return v_end
    v_start = value_at(points, start, START_TOLERANCE_DAYS)
    if v_start is None and zero_at_creation and created is not None and points:
        first_day, first_v = points[0]
        if first_day > start and (first_day - created).days <= START_TOLERANCE_DAYS:
            span = (first_day - created).days
            v_start = first_v * (start - created).days / span if span > 0 else first_v
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
    meta_types = FORMATION_EVENT_TYPES | {LAB_ALUMNI_CLUSTER}
    metas: dict[uuid.UUID, dict] = {}
    if any(r.signal_type in meta_types for r in rows):
        metas = {sid: dict(m or {}) for sid, m in session.execute(
            select(Signal.id, Signal.meta).where(Signal.event_at >= lo, Signal.event_at <= hi,
                                                 Signal.signal_type.in_(sorted(meta_types))))}
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
            r.entity_id,
            metas.get(r.id),
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


@dataclass
class ContributorSeries:
    """Per-repo contributor series, one measure each (never mixed across definitions)."""

    first_commit: list[tuple[date, float]] = field(default_factory=list)  # cumulative, first-commit based
    is_backfill: bool = False  # first_commit is a step series from /stats/contributors (complete when fetched)
    totals: list[tuple[date, float]] = field(default_factory=list)  # /contributors counts


def load_contributor_series(
    session: Session, ids: Iterable[uuid.UUID], day: date, lookback_days: int = SERIES_LOOKBACK_DAYS
) -> dict[uuid.UUID, ContributorSeries]:
    """Split stored contributor points by definition.

    'contributors' backfill points count distinct non-bot authors by first-commit week; older
    'observed' points under the same metric are /contributors counts (bots included, a different
    measure), so when backfill exists they are treated as totals, never diffed against backfill."""
    ids = list(set(ids))
    raw: dict[uuid.UUID, dict[str, list[tuple[date, float]]]] = defaultdict(lambda: defaultdict(list))
    if ids:
        for eid, metric, d, v, src in session.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value,
                   MetricSnapshot.source)
            .where(
                MetricSnapshot.entity_id.in_(ids),
                MetricSnapshot.metric.in_(CONTRIBUTOR_METRICS),
                MetricSnapshot.day <= day,
                MetricSnapshot.day >= day - timedelta(days=lookback_days),
            )
            .order_by(MetricSnapshot.day)
        ).all():
            key = "total" if metric == "contributors_total" else "backfill" if src == "backfill" else "other"
            raw[eid][key].append((d, float(v)))
    out: dict[uuid.UUID, ContributorSeries] = {}
    for eid, parts in raw.items():
        if parts["backfill"]:
            out[eid] = ContributorSeries(parts["backfill"], True, parts["total"] or parts["other"])
        else:
            out[eid] = ContributorSeries(parts["other"], False, parts["total"] or parts["other"])
    return out


def new_contributors_delta(
    cs: ContributorSeries | None, day: date, created: date | None = None, days: int = 30
) -> float | None:
    """Distinct first-time contributors in the last ``days``, from one consistent series.

    A backfill step series is complete as of the repo's last fetch, so if that fetch is recent
    (a totals point within the current tolerance) values are read without staleness limits."""
    if created is not None and created > day:
        return 0.0  # the repo did not exist yet
    if cs is None or not cs.first_commit:
        return None
    if cs.is_backfill and value_at(cs.totals, day, CURRENT_TOLERANCE_DAYS) is not None:
        start = day - timedelta(days=days)
        v_end = value_at(cs.first_commit, day, 10**6)
        v_start = value_at(cs.first_commit, start, 10**6)
        return None if v_end is None else v_end - (v_start or 0.0)
    return window_delta(cs.first_commit, day, 0, days, None, zero_at_creation=False)


def latest_new_contributors_signal(signals: Iterable[SignalLite], repo_id: uuid.UUID, day: date) -> float | None:
    """Count from the repo's own recent 'new_contributors' signal (first-commit based, founders excluded)."""
    lo = day_start(day - timedelta(days=CURRENT_TOLERANCE_DAYS))
    best = None
    for s in signals:
        if (s.signal_type == "new_contributors" and s.entity_id == repo_id and s.raw_value is not None
                and s.event_at >= lo and (best is None or s.event_at > best.event_at)):
            best = s
    return None if best is None else float(best.raw_value)


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
    contrib_series = load_contributor_series(session, all_repo_ids, day)
    company_series = load_series(
        session, cids, ("followers", "hn_probe_mentions", "hn_probe_points"), day, lookback_days=30
    )
    hist_cut = day - timedelta(days=7)
    with_history = {eid for (eid, _), pts in series.items() if pts and pts[0][0] <= hist_cut} | {
        eid for eid, cs in contrib_series.items() if cs.first_commit and cs.first_commit[0][0] <= hist_cut
    }
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
        # Only confirmed founders: linked GitHub contributors are not a founding team.
        founders = [cp for cp in links if cp.is_founder]
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
            _current(cs.totals if (cs := contrib_series.get(r.id)) else [], day, r.contributors_count)
            for r in repos
        )

        # The company's own signals (on it, its repos, or explicitly linked to them) plus signals *about*
        # its linked people. A person's 'related' links (e.g. a new contributor to another org's repo)
        # are about that other org and must not leak into this company's evidence.
        pids = {cp.person_id for cp in links}
        signals = index.for_entities([c.id, *ser.keys()])
        if pids:
            own_ids = {s.id for s in signals}
            signals = sorted([*signals, *(s for s in index.for_entities(pids)
                                          if s.entity_id in pids and s.id not in own_ids)],
                             key=lambda s: s.event_at, reverse=True)
        # Per repo: the provider's first-commit-based count if fresh, else a delta on one consistent series.
        new_contributors = sum_known(
            sig if (sig := latest_new_contributors_signal(signals, r.id, day)) is not None
            else new_contributors_delta(contrib_series.get(r.id), day, _repo_created(r))
            for r in repos
        )
        if new_contributors is None and c.id in new_contrib:
            new_contributors = float(new_contrib[c.id])
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
                linked_people=len(links),
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


@dataclass
class AffiliationTier:
    """A person's affiliations (self-reported/listed strings + public GitHub orgs) and the best tier match.

    ``basis`` is "public_github_org" when the best match comes from a public GitHub organization membership
    (observed via GitHub's API — a fact about membership, not employment dates), else "affiliation"."""

    affiliations: list[str]
    tier: float = 0.0
    tier_match: str | None = None
    basis: str | None = None
    org_login: str | None = None
    public_orgs: list[str] = field(default_factory=list)  # canonical names of public org memberships


def public_org_canonical(o: dict) -> str | None:
    """Canonical organization for a public GitHub org, only on a whole-login alias match (see
    ``pipeline.relationships.public_org_alias``)."""
    from radar.pipeline.relationships import public_org_alias

    return public_org_alias(o)


def person_affiliation_tier(p: Person, thesis: Thesis) -> AffiliationTier:
    """Affiliation strings plus public GitHub org memberships (``Person.attrs['github_orgs']``), and the best
    thesis tier. On equal tiers a public membership wins (observed, not typed by the person)."""
    affs = list(dict.fromkeys(a.strip() for a in [p.current_company, *(p.prior_affiliations or [])]
                              if a and a.strip()))
    out = AffiliationTier(affiliations=affs)
    for a in affs:
        t = thesis.affiliation_tier(a)
        if t > out.tier:
            out.tier, out.tier_match, out.basis = t, thesis.matched_affiliation(a), "affiliation"
    for o in (p.attrs or {}).get("github_orgs") or []:
        if not isinstance(o, dict) or not o.get("login"):
            continue
        canon = public_org_canonical(o)
        if canon is None:
            continue
        out.public_orgs.append(canon)
        t = thesis.affiliation_tier(canon)
        if t > out.tier or (t > 0 and t == out.tier and out.basis != "public_github_org"):
            out.tier, out.tier_match = t, thesis.matched_affiliation(canon)
            out.basis, out.org_login = "public_github_org", str(o["login"])
    out.public_orgs = list(dict.fromkeys(out.public_orgs))
    return out


def talent_sources_path():
    """config/talent_sources.yaml (next to the thesis file, else the repository's config/)."""
    from pathlib import Path

    from radar.settings import get_settings

    near = Path(get_settings().thesis_path).parent / "talent_sources.yaml"
    return near if near.exists() else Path(__file__).resolve().parents[3] / "config" / "talent_sources.yaml"


_TALENT_TIERS: dict[str, int] | None = None


def talent_tiers() -> dict[str, int]:
    """GitHub org login (lowercase) -> tier (1|2) from config/talent_sources.yaml; {} when absent/invalid.
    Only used to fill a lab contribution recorded without a tier."""
    global _TALENT_TIERS
    if _TALENT_TIERS is None:
        import yaml

        out: dict[str, int] = {}
        try:
            with open(talent_sources_path()) as f:
                data = yaml.safe_load(f) or {}
            lists = [data] if isinstance(data, list) else [v for v in data.values() if isinstance(v, list)]
            rows = [row for lst in lists for row in lst]
            for row in rows:
                if isinstance(row, dict) and row.get("org") and row.get("tier") in (1, 2):
                    out[str(row["org"]).lower()] = int(row["tier"])
        except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError):
            out = {}
        _TALENT_TIERS = out
    return _TALENT_TIERS


_TALENT_HINTS: dict[str, list[str]] | None = None


def talent_theme_hints() -> dict[str, list[str]]:
    """GitHub org login (lowercase) -> thesis themes (``theme_hint``) from config/talent_sources.yaml."""
    global _TALENT_HINTS
    if _TALENT_HINTS is None:
        import yaml

        out: dict[str, list[str]] = {}
        try:
            with open(talent_sources_path()) as f:
                data = yaml.safe_load(f) or {}
            lists = [data] if isinstance(data, list) else [v for v in data.values() if isinstance(v, list)]
            for row in (row for lst in lists for row in lst):
                if isinstance(row, dict) and row.get("org") and isinstance(row.get("theme_hint"), list):
                    out[str(row["org"]).lower()] = [str(t) for t in row["theme_hint"] if t]
        except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError):
            out = {}
        _TALENT_HINTS = out
    return _TALENT_HINTS


def person_lab_contributions(p: Person | None):
    """Verified lab contributions of ``p`` (``Person.attrs['lab_contributions']``), strongest first."""
    attrs = getattr(p, "attrs", None) or {}
    return parse_lab_contributions(attrs.get("lab_contributions") if isinstance(attrs, dict) else None,
                                   talent_tiers())


# ── New-project quality (is a new repository formation evidence?) ────────────
# A ``new_project`` signal (a person created a public repository) counts as formation evidence only when
# the repository shows *traction* (stars / forks / other contributors) or, for a non-trivial repository by
# someone who is not creating repositories in series, thesis-keyword *depth* (>= 2 thesis subcategories).
# Trivial utility repositories ('*-stats', dotfiles, MCP wrappers, experiments ...) and serial hobby repos
# (>= SERIAL_NEW_PROJECTS new repositories by one person in the window) need traction. Counts are the
# latest observed (max of the signal's metadata at detection and the repositories row).
NEW_PROJECT_TRACTION_STARS = 25
NEW_PROJECT_TRACTION_FORKS = 5
NEW_PROJECT_TRACTION_CONTRIBUTORS = 2  # contributors other than the owner (automation excluded)
NEW_PROJECT_STRONG_STARS = 500  # stars at which traction strength saturates
# "Notable" traction — enough to be formation evidence of its own (``project_traction``), beyond the repository
# merely counting: >= 50 stars, >= 10 forks, or >= 2 other contributors.
NEW_PROJECT_NOTABLE_STARS = 50
NEW_PROJECT_NOTABLE_FORKS = 10
NEW_PROJECT_DEPTH_MATCHES = 2
NEW_PROJECT_DEPTH_STRENGTH = 0.35
SERIAL_NEW_PROJECTS = 7
TRIVIAL_REPO_TOKENS = frozenset({
    "stats", "dotfiles", "config", "configs", "notes", "scripts", "experiment", "experiments", "playground",
    "sandbox", "test", "tests", "demo", "demos", "template", "templates", "starter", "homework", "tutorial",
    "practice", "mcp", "blog", "homepage", "resume", "cv", "snippets", "utils", "scratch", "exercises",
})


@dataclass(frozen=True)
class NewProjectQuality:
    repo: str
    stars: int
    forks: int
    contributors: int  # other than the owner
    thesis_matches: tuple[str, ...]
    serial: int  # new repositories by the same person in the window
    age_days: int | None
    counts: bool
    traction: bool
    strength: float  # 0..1 (0 when it does not count)
    reason: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    @property
    def notable(self) -> bool:
        """Traction strong enough to be evidence of its own (see ``NEW_PROJECT_NOTABLE_*``)."""
        return self.counts and (self.stars >= NEW_PROJECT_NOTABLE_STARS or self.forks >= NEW_PROJECT_NOTABLE_FORKS
                                or self.contributors >= NEW_PROJECT_TRACTION_CONTRIBUTORS)

    def traction_text(self) -> str:
        bits = [f"{self.stars:,} star{'s' if self.stars != 1 else ''}"]
        if self.forks:
            bits.append(f"{self.forks:,} fork{'s' if self.forks != 1 else ''}")
        if self.contributors:
            bits.append(f"{self.contributors} other contributor{'s' if self.contributors != 1 else ''}")
        age = f" within {self.age_days} days of creation" if self.age_days is not None else ""
        return ", ".join(bits) + age


def trivial_repo_name(repo: str) -> str | None:
    """The token that makes a repository name a trivial utility ('vllm-force-merge-stats' -> 'stats'), else None."""
    import re

    name = repo.rsplit("/", 1)[-1].lower()
    for tok in re.split(r"[-_.\s]+", name):
        if tok in TRIVIAL_REPO_TOKENS:
            return tok
    return None


def assess_new_project(repo: str, *, stars: int = 0, forks: int = 0, contributors: int = 0,
                       thesis_matches: Iterable[str] = (), serial: int = 1, age_days: int | None = None
                       ) -> NewProjectQuality:
    """Pure: does a new repository count as formation evidence (see the constants above)?"""
    import math

    matches = tuple(dict.fromkeys(str(m) for m in thesis_matches if m))
    traction_bits = []
    if stars >= NEW_PROJECT_TRACTION_STARS:
        traction_bits.append(f"{stars} stars")
    if forks >= NEW_PROJECT_TRACTION_FORKS:
        traction_bits.append(f"{forks} forks")
    if contributors >= NEW_PROJECT_TRACTION_CONTRIBUTORS:
        traction_bits.append(f"{contributors} other contributors")
    traction = bool(traction_bits)
    trivial = trivial_repo_name(repo)
    is_serial = serial >= SERIAL_NEW_PROJECTS

    def q(counts: bool, strength: float, reason: str) -> NewProjectQuality:
        return NewProjectQuality(repo, stars, forks, contributors, matches, serial, age_days, counts, traction,
                                 round(strength if counts else 0.0, 4), reason)

    if traction:
        strength = 0.5 + 0.5 * min(1.0, math.log1p(max(stars, 0)) / math.log1p(NEW_PROJECT_STRONG_STARS))
        return q(True, strength, "traction: " + ", ".join(traction_bits))
    if trivial:
        return q(False, 0.0, f"trivial utility repository ('{trivial}') without traction "
                             f"(< {NEW_PROJECT_TRACTION_STARS} stars)")
    if is_serial:
        return q(False, 0.0, f"one of {serial} new repositories by the same person in the window, without "
                             f"traction (< {NEW_PROJECT_TRACTION_STARS} stars)")
    if len(matches) >= NEW_PROJECT_DEPTH_MATCHES:
        return q(True, NEW_PROJECT_DEPTH_STRENGTH, f"thesis depth ({', '.join(matches)}), no traction yet")
    return q(False, 0.0, f"no traction (< {NEW_PROJECT_TRACTION_STARS} stars) and thin thesis match "
                         f"({', '.join(matches) or 'none'})")


def assess_new_project_signals(session: Session, signals: Iterable[object], day: date | None = None
                               ) -> dict[uuid.UUID, NewProjectQuality]:
    """{signal id: quality} for ``new_project`` signals (``Signal`` or ``SignalLite`` with ``meta``); batched.

    ``serial`` is the number of distinct new repositories the same person has among ``signals`` (or the
    provider's ``new_repos_in_window`` when larger)."""
    from radar.providers.github import is_bot

    sigs = [s for s in signals if getattr(s, "signal_type", None) == "new_project"]
    if not sigs:
        return {}

    def repo_of(s: object) -> str:
        meta = getattr(s, "meta", None) or {}
        r = str(meta.get("repo") or "")
        if not r:
            title = str(getattr(s, "title", "") or "")
            r = title.rsplit(": ", 1)[-1].strip() if ": " in title else title
        return r

    by_person: dict[object, set[str]] = defaultdict(set)
    for s in sigs:
        by_person[getattr(s, "entity_id", None)].add(repo_of(s).lower())
    names = sorted({repo_of(s).lower() for s in sigs if repo_of(s)})
    stats: dict[str, tuple[int, int, int, datetime | None]] = {}
    if names:
        rows = list(session.scalars(select(Repository).where(func.lower(Repository.full_name).in_(names))))
        others: dict[uuid.UUID, int] = defaultdict(int)
        if rows:
            owners = {r.id: (r.owner or "").lower() for r in rows}
            for rid, login in session.execute(
                select(RepoContributor.repository_id, Person.github_handle)
                .join(Person, Person.id == RepoContributor.person_id)
                .where(RepoContributor.repository_id.in_(list(owners)))
            ).all():
                if login and login.lower() != owners[rid] and not is_bot(login):
                    others[rid] += 1
        for r in rows:
            stats[r.full_name.lower()] = (int(r.stars or 0), int(r.forks or 0), others.get(r.id, 0), r.gh_created_at)
    out: dict[uuid.UUID, NewProjectQuality] = {}
    for s in sigs:
        meta = getattr(s, "meta", None) or {}
        repo = repo_of(s)
        st, fk, co, created = stats.get(repo.lower(), (0, 0, 0, None))
        stars = max(st, int(meta.get("stars") or 0))
        serial = max(len(by_person.get(getattr(s, "entity_id", None), ())), int(meta.get("new_repos_in_window") or 0))
        created = created or getattr(s, "event_at", None)
        from radar import clock

        ref = day or clock.today()
        age = (ref - created.date()).days if isinstance(created, datetime) else None
        out[s.id] = assess_new_project(repo, stars=stars, forks=fk, contributors=co,  # type: ignore[attr-defined]
                                       thesis_matches=meta.get("thesis_matches") or (), serial=serial,
                                       age_days=age if age is None or age >= 0 else None)
    return out


def tier_phrase(t: AffiliationTier) -> str | None:
    """'public GitHub member of openai (OpenAI)' / None for non-public-org matches."""
    if t.basis != "public_github_org" or not t.org_login:
        return None
    return f"public GitHub member of {t.org_login}" + (f" ({t.tier_match})" if t.tier_match else "")


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
    from radar.providers.github import is_bot

    people = [
        p for p in session.scalars(
            select(Person).where(
                Person.dismissed.is_(False), true() if reconstruct else Person.first_seen_at <= day_end(day)
            )
        )
        if not (p.github_handle and is_bot(p.github_handle))  # automation / AI coding agents are not people
    ]
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
    labs = {p.id: person_lab_contributions(p) for p in people}
    key_repos: dict[uuid.UUID, list[tuple[Repository, float]]] = defaultdict(list)
    for pid in pids:
        seen: set[uuid.UUID] = set()
        lab_names = {c.full_name.lower() for c in labs.get(pid, [])}
        for r in owned.get(pid, []):
            key_repos[pid].append((r, 1.0))
            seen.add(r.id)
        for rc, r in contribs.get(pid, []):
            if r.id in seen:
                continue
            top = top_contrib.get(r.id) or 0
            share = (rc.contributions or 0) / top if top else 0.0
            # Verified lab-repository history counts at its share even below the core-contributor threshold.
            if share >= KEY_CONTRIBUTOR_SHARE or (share > 0 and (r.full_name or "").lower() in lab_names):
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
    # New repositories count as formation evidence only with traction or thesis depth (assess_new_project).
    projects = assess_new_project_signals(
        session, {x.id: x for sigs in index.by_entity.values() for x in sigs.values()
                  if x.signal_type == "new_project"}.values(), day)
    out: list[PersonFeatures] = []
    for p in people:
        repos = owned.get(p.id, [])
        keyed = key_repos.get(p.id, [])
        signals = index.for_entities([p.id, *(r.id for r, _ in keyed)])
        # Formation evidence must be about this person: never a co-contributor's bio/affiliation
        # change that merely shares a repo link.
        own = [
            s for s in index.for_entities([p.id])
            if s.signal_type not in FORMATION_SIGNAL_TYPES or s.entity_id == p.id
        ]
        transition = [s.id for s in own if s.signal_type in TRANSITION_SIGNAL_TYPES]
        events = [s for s in own if s.signal_type in FORMATION_EVENT_TYPES and s.entity_id == p.id]
        new_projects = list(dict.fromkeys(
            str((s.meta or {}).get("repo") or s.title) for s in events if s.signal_type == "new_project"
            and (q := projects.get(s.id)) is not None and q.counts))
        joined_orgs = [str((s.meta or {}).get("org") or s.title) for s in events
                       if s.signal_type == "joined_young_org"]
        clusters = list(dict.fromkeys(s.title for s in own if s.signal_type == LAB_ALUMNI_CLUSTER))
        aff = person_affiliation_tier(p, thesis)
        affiliations, best_tier, best_match = aff.affiliations, aff.tier, aff.tier_match
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
        young += [o for o in joined_orgs if o.lower() not in {y.lower() for y in young}]
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
                own_signals=own,
                is_demo=bool(p.is_demo),
                tier_basis=aff.basis,
                tier_org_login=aff.org_login,
                public_orgs=aff.public_orgs,
                affiliation_self_reported=bool((p.attrs or {}).get("self_reported")),
                lab_contributions=labs.get(p.id, []),
                new_projects=new_projects,
                lab_clusters=clusters,
            )
        )
    return out


def _org_created(session: Session, org_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[date | None, str]]:
    if not org_ids:
        return {}
    from radar.scoring.formation import curated_list_anchor

    repos: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in session.scalars(select(Repository).where(Repository.company_id.in_(list(org_ids)))):
        repos[r.company_id].append(r)
    # A link collection (awesome-list org) is not a young organization anyone "joined": no creation date.
    return {
        c.id: (None if curated_list_anchor(c, repos.get(c.id, [])) is not None
               else org_created_date(c.founded_date, [r.gh_created_at for r in repos.get(c.id, [])]), c.name)
        for c in session.scalars(select(Company).where(Company.id.in_(list(org_ids))))
    }


# ── Velocity ─────────────────────────────────────────────────────────────────


@dataclass
class RepoSeries:
    """One repository's velocity inputs: metric -> sorted points, and the observed (non-backfill) days."""

    points: dict[str, list[tuple[date, float]]] = field(default_factory=lambda: defaultdict(list))
    observed: dict[str, set[date]] = field(default_factory=lambda: defaultdict(set))


def load_velocity_series(
    session: Session, ids: Iterable[uuid.UUID], day: date, lookback_days: int = VELOCITY_LOOKBACK_DAYS
) -> dict[uuid.UUID, RepoSeries]:
    """Series for ``VELOCITY_METRICS`` within the lookback, plus the last point before it (a step
    series' value at a window start can come from an older change point)."""
    ids = list(set(ids))
    out: dict[uuid.UUID, RepoSeries] = defaultdict(RepoSeries)
    if not ids:
        return out
    lo = day - timedelta(days=lookback_days)
    cols = (MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value,
            MetricSnapshot.source)
    base = (MetricSnapshot.entity_id.in_(ids), MetricSnapshot.metric.in_(VELOCITY_METRICS))
    rows = list(session.execute(
        select(*cols)
        .where(*base, MetricSnapshot.day < lo)
        .distinct(MetricSnapshot.entity_id, MetricSnapshot.metric)
        .order_by(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day.desc())
    ).all())
    rows += session.execute(
        select(*cols).where(*base, MetricSnapshot.day >= lo, MetricSnapshot.day <= day).order_by(MetricSnapshot.day)
    ).all()
    for eid, metric, d, v, src in sorted(rows, key=lambda r: r[2]):
        rs = out[eid]
        rs.points[metric].append((d, float(v)))
        if src == "observed":
            rs.observed[metric].add(d)
    for rs in out.values():
        # 'contributors' observed points written before the backfill era were /contributors counts (bots
        # included). A backfilled repo keeps only observed points from deep fetches, which also write
        # 'contributors_total' on the same day and use the backfill's definition.
        total_days = {d for d, _ in rs.points.get("contributors_total", [])}
        pts = rs.points.get("contributors", [])
        obs = rs.observed["contributors"]
        if any(d not in obs for d, _ in pts):
            kept: list[tuple[date, float]] = []
            last_backfill = 0.0
            for d, v in pts:
                if d not in obs:
                    last_backfill = v
                    kept.append((d, v))
                elif d in total_days and v >= last_backfill:  # a cumulative count cannot be below its past
                    kept.append((d, v))
            rs.points["contributors"] = kept
    return out


def build_company_velocity(
    session: Session, day: date, company_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, dict]:
    """Per-company velocity (sum over its repositories) as of ``day``; see ``scoring.velocity``."""
    q = select(Repository.id, Repository.company_id, Repository.gh_created_at).where(
        Repository.company_id.is_not(None)
    )
    cids: list[uuid.UUID] = []
    if company_ids is not None:
        cids = list(set(company_ids))
        if not cids:
            return {}
        q = q.where(Repository.company_id.in_(cids))
    repos = session.execute(q).all()
    series = load_velocity_series(session, [r.id for r in repos], day)
    per_company: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for rid, cid, created in repos:
        rs = series.get(rid) or RepoSeries()
        per_company[cid].append(
            compute_velocity(rs.points, day, observed_days=rs.observed, created=created.date() if created else None)
        )
    for cid in cids if company_ids is not None else ():
        per_company.setdefault(cid, [])  # no repositories: every value is insufficient_history
    return {cid: {"computed_on": day.isoformat(), **aggregate_velocity(rows)} for cid, rows in per_company.items()}
