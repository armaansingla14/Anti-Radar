"""SINCE LAST SNAPSHOT: compare two verified snapshot days (or the last snapshot and a live database).

The demo replays a *chain* of verified snapshots (``demo/seed.py``): the base capture, then dated updates.
Each replayed day records an ``IngestionRun(provider="snapshot", params.as_of)``; those days are the
comparison points. The window between two points is ``(end of base day, end of current day]``.

Rules that keep the comparison honest:

* "New" means *new to Radar* (``first_seen_at`` in the window), never "newly created"; creation dates
  (repository / organization creation, paper publication, job posting) are reported where known.
* A score is compared only when both days have a *live* (not reconstructed) snapshot of the *same score
  version* and the entity was *re-observed* in the window (a new signal, an observed metric, a crawl).
  Otherwise it is counted (``not_reobserved`` / ``not_comparable`` / ``no_baseline``), never diffed.
* Scores are cohort-relative percentiles; a later, larger cohort moves percentiles by itself. The report
  says so (``caveats``).
* Formation status comes from ``FormationHistory`` (one row per qualifying candidate per formation run).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    FormationCandidate,
    FormationHistory,
    IngestionRun,
    Job,
    MeetingRecommendation,
    MetricSnapshot,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
    SignalEntity,
    WebPage,
)
from radar.schemas.api import EntityRef, FormationChangeBadge, SinceSnapshot
from radar.schemas.changes import (
    ChangeItem,
    ChangeSection,
    ChangesReport,
    CollisionChange,
    CollisionChanges,
    FamilySignals,
    FormationChange,
    FormationChanges,
    MeetChanges,
    MeetMove,
    ScoreChange,
    ScoreChangeList,
    ScoreChanges,
    SnapshotPoint,
    SnapshotPoints,
)
from radar.scoring.collision import collision_strength
from radar.scoring.extract import day_end
from radar.services import memo
from radar.services import presenters as P
from radar.services.freshness import FAMILIES, PERSON_GITHUB_CHECKS, SOURCE_FAMILY

MATERIAL = 5.0  # |score delta| at or above this is a material change (Anti Score and Formation Score points)
MOMENTUM_COMPONENT_MIN = 2.5  # technical_momentum points (10% of its 25-point max)
ACCELERATION_RAW_MIN = 0.15  # acceleration percentile (0..1) increase
ITEM_LIMIT = 25
SIGNAL_ITEM_LIMIT = 10  # per family
TIER_RANK = {"early": 1, "emerging": 2, "high": 3}
STRONG_COLLISION = ("HIGH", "MEDIUM")
FAMILY_TITLE = {"github": "GitHub", "research": "Research", "hiring": "Hiring", "web": "Web",
                "community": "Community", "social": "Social", "other": "Other"}


class InvalidRange(ValueError):
    pass


# ── pure helpers ─────────────────────────────────────────────────────────────


def material_delta(delta: float | None, threshold: float = MATERIAL) -> str | None:
    """'up' / 'down' when ``|delta|`` (rounded to 0.1) reaches ``threshold``; None otherwise."""
    if delta is None:
        return None
    d = round(float(delta), 1)
    if d >= threshold:
        return "up"
    if d <= -threshold:
        return "down"
    return None


def _field(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _attach_anchor_slugs(session: Session, items: list[FormationChange]) -> None:
    """Entity slugs for formation anchors, so links never depend on the candidate-slug format."""
    from radar.models import Company, Person

    for model, kind in ((Company, "company"), (Person, "person")):
        ids = [uuid.UUID(x.anchor_id) for x in items if x.anchor_type == kind]
        if ids:
            slugs = dict(session.execute(select(model.id, model.slug).where(model.id.in_(ids))).all())
            for x in items:
                if x.anchor_type == kind:
                    x.anchor_slug = slugs.get(uuid.UUID(x.anchor_id))


def formation_status(base_row: Any, current_row: Any, threshold: float = MATERIAL) -> str | None:
    """NEW / DROPPED / STRENGTHENED / WEAKENED / UNCHANGED for one anchor's formation-history rows
    (objects or dicts with ``tier`` and ``score``); None when the anchor is on neither day.

    A tier change decides; within the same tier a score move of at least ``threshold`` points does."""
    if base_row is None and current_row is None:
        return None
    if base_row is None:
        return "NEW"
    if current_row is None:
        return "DROPPED"
    tb, tc = TIER_RANK.get(_field(base_row, "tier"), 0), TIER_RANK.get(_field(current_row, "tier"), 0)
    if tc != tb:
        return "STRENGTHENED" if tc > tb else "WEAKENED"
    move = material_delta(float(_field(current_row, "score") or 0) - float(_field(base_row, "score") or 0), threshold)
    return {"up": "STRENGTHENED", "down": "WEAKENED"}.get(move or "", "UNCHANGED")


def _d(day: date | None, fallback: str) -> str:
    return day.isoformat() if day else fallback


def score_comparability(base: ScoreSnapshot | None, current: ScoreSnapshot | None, reobserved: bool, *,
                        base_day: date | None = None, current_day: date | None = None) -> tuple[bool, str | None]:
    """Whether two snapshots of one entity may be diffed, and why not."""
    if current is None:
        return False, f"No score on {_d(current_day, 'the current day')}."
    if base is None:
        return False, f"No score on {_d(base_day, 'the base day')}: new to Radar since then."
    if P.is_reconstructed(base) or P.is_reconstructed(current):
        which = base_day if P.is_reconstructed(base) else current_day
        return False, f"The {_d(which, 'baseline')} score is reconstructed from history, not live; not compared."
    if (base.version or "") != (current.version or ""):
        return False, f"Score version changed ({base.version} → {current.version}); not compared."
    if not reobserved:
        return False, f"Not re-observed since {_d(base_day, 'the base day')}: nothing new to compare."
    return True, None


def dominant_version(versions: Iterable[str | None]) -> str | None:
    c = Counter(v for v in versions if v)
    if not c:
        return None
    return max(c.items(), key=lambda kv: (kv[1], kv[0]))[0]


def versions_comparable(base_versions: Iterable[str | None], current_versions: Iterable[str | None]
                        ) -> tuple[bool, str | None, str | None, str | None]:
    """(comparable, reason, base_version, current_version) for two days' score populations."""
    b, c = dominant_version(base_versions), dominant_version(current_versions)
    if b is None or c is None:
        return False, "No live scores on " + ("the base day." if b is None else "the current day."), b, c
    if b != c:
        return False, f"Score version changed ({b} → {c}); scores from different versions are not compared.", b, c
    return True, None, b, c


def strength_of(collision: dict[str, Any] | None) -> str | None:
    if not collision:
        return None
    s = collision.get("strength")
    if s in ("HIGH", "MEDIUM", "LOW"):
        return s
    return collision_strength(len(collision.get("families") or []), float(collision.get("score") or 0.0))


# ── points ───────────────────────────────────────────────────────────────────


def _point_rows(session: Session) -> list[tuple[date, dict[str, Any]]]:
    out: dict[date, dict[str, Any]] = {}
    for params, started in session.execute(
            select(IngestionRun.params, IngestionRun.started_at).where(IngestionRun.provider == "snapshot")
            .order_by(IngestionRun.started_at)):
        try:
            d = date.fromisoformat((params or {}).get("as_of") or "")
        except ValueError:
            continue
        out[d] = dict(params or {})
    return sorted(out.items())


def snapshot_points(session: Session) -> list[date]:
    """Every snapshot day replayed into this database, oldest first."""
    return [d for d, _ in _point_rows(session)]


def is_live_mode() -> bool:
    return clock.as_of() is None and not clock.snapshot_pinned()


def default_pair(session: Session, points: Sequence[date] | None = None) -> tuple[date, date] | None:
    """(previous point, latest point); on a live database whose clock is past the last snapshot,
    (latest point, today). None when there is nothing to compare."""
    pts = list(points if points is not None else snapshot_points(session))
    if pts and is_live_mode() and clock.today() > pts[-1]:
        return pts[-1], clock.today()
    if len(pts) >= 2:
        return pts[-2], pts[-1]
    return None


def points_out(session: Session) -> SnapshotPoints:
    rows = _point_rows(session)
    pair = default_pair(session, [d for d, _ in rows])
    pts = []
    for d, params in rows:
        cap = params.get("captured_at")
        try:
            captured = datetime.fromisoformat(cap) if isinstance(cap, str) else None
        except ValueError:
            captured = None
        pts.append(SnapshotPoint(as_of=d, kind=params.get("kind"), link=params.get("link"),
                                 files=params.get("files"), captured_at=captured))
    return SnapshotPoints(points=pts, latest=rows[-1][0] if rows else None,
                          default_base=pair[0] if pair else None, default_current=pair[1] if pair else None,
                          is_snapshot=not (pair and pair[1] not in {d for d, _ in rows}))


# ── re-observation ───────────────────────────────────────────────────────────


def _in_window(col, lo: datetime, hi: datetime):
    return and_(col > lo, col <= hi)


def _parse_dt(v: Any) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def reobserved_ids(session: Session, entity_type: str, ids: Iterable[uuid.UUID], base_day: date,
                   current_day: date) -> set[uuid.UUID]:
    """Entities with any observation recorded in ``(base_day, current_day]``: a new signal on the entity (or
    linked to it, or on its repositories), an observed metric snapshot, a job / web crawl, a GitHub check."""
    ids = list(set(ids))
    if not ids:
        return set()
    lo, hi = day_end(base_day), day_end(current_day)
    sig_win = _in_window(Signal.detected_at, lo, hi)
    metric_win = and_(MetricSnapshot.source == "observed", MetricSnapshot.day > base_day,
                      MetricSnapshot.day <= current_day)
    out: set[uuid.UUID] = set()
    queries = [
        select(Signal.entity_id).where(Signal.entity_id.in_(ids), sig_win),
        select(SignalEntity.entity_id).join(Signal, Signal.id == SignalEntity.signal_id)
        .where(SignalEntity.entity_id.in_(ids), sig_win),
        select(MetricSnapshot.entity_id).where(MetricSnapshot.entity_id.in_(ids), metric_win),
    ]
    if entity_type == "company":
        queries += [
            select(Repository.company_id).join(Signal, Signal.entity_id == Repository.id)
            .where(Repository.company_id.in_(ids), sig_win),
            select(Repository.company_id).join(MetricSnapshot, MetricSnapshot.entity_id == Repository.id)
            .where(Repository.company_id.in_(ids), metric_win),
            select(Job.company_id).where(Job.company_id.in_(ids), _in_window(Job.first_seen_at, lo, hi)),
            select(WebPage.company_id).where(WebPage.company_id.in_(ids), _in_window(WebPage.last_fetched_at, lo, hi)),
        ]
    else:
        queries += [
            select(RepoContributor.person_id).join(MetricSnapshot, MetricSnapshot.entity_id == RepoContributor.repository_id)
            .where(RepoContributor.person_id.in_(ids), metric_win),
            select(RepoContributor.person_id).join(Signal, Signal.entity_id == RepoContributor.repository_id)
            .where(RepoContributor.person_id.in_(ids), sig_win),
            select(PaperAuthor.person_id).join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
            .where(PaperAuthor.person_id.in_(ids), _in_window(ResearchPaper.first_seen_at, lo, hi)),
        ]
    for q in queries:
        out.update(x for x in session.scalars(q.distinct()) if x is not None)
    rest = [i for i in ids if i not in out]
    if rest:
        model = Company if entity_type == "company" else Person
        keys = ("jobs_last_crawled_at",) if entity_type == "company" else PERSON_GITHUB_CHECKS
        for eid, attrs in session.execute(select(model.id, model.attrs).where(model.id.in_(rest))):
            for k in keys:
                at = _parse_dt((attrs or {}).get(k))
                if at is not None and lo < at <= hi:
                    out.add(eid)
                    break
    return out


# ── per-entity ───────────────────────────────────────────────────────────────


def entity_since(session: Session, entity_type: str, entity_id: uuid.UUID) -> SinceSnapshot | None:
    """Score change since the previous snapshot for one company / person (None without two points)."""
    pair = default_pair(session)
    if pair is None:
        return None
    b, c = pair
    snaps = {s.day: s for s in session.scalars(
        select(ScoreSnapshot).where(ScoreSnapshot.entity_id == entity_id, ScoreSnapshot.day.in_([b, c])))}
    base, cur = snaps.get(b), snaps.get(c)
    ok, reason = score_comparability(base, cur, True, base_day=b, current_day=c)
    if ok:
        ok, reason = score_comparability(
            base, cur, entity_id in reobserved_ids(session, entity_type, [entity_id], b, c), base_day=b, current_day=c)
    return SinceSnapshot(
        base_day=b, current_day=c,
        base_score=round(base.score, 1) if base is not None else None,
        current_score=round(cur.score, 1) if cur is not None else None,
        delta=round(cur.score - base.score, 1) if ok and base is not None and cur is not None else None,
        comparable=ok, reason=reason)


def company_since(session: Session, company_id: uuid.UUID) -> SinceSnapshot | None:
    return entity_since(session, "company", company_id)


def person_since(session: Session, person_id: uuid.UUID) -> SinceSnapshot | None:
    return entity_since(session, "person", person_id)


# ── formation ────────────────────────────────────────────────────────────────


def history_day_on_or_before(session: Session, day: date) -> date | None:
    return session.scalar(select(func.max(FormationHistory.day)).where(FormationHistory.day <= day))


def _history(session: Session, day: date, include_demo: bool) -> dict[uuid.UUID, FormationHistory]:
    q = select(FormationHistory).where(FormationHistory.day == day)
    if not include_demo:
        q = q.where(FormationHistory.is_demo.is_(False))
    return {r.anchor_id: r for r in session.scalars(q)}


def _history_pair(session: Session, base_day: date, current_day: date
                  ) -> tuple[date | None, date | None, str | None]:
    hb, hc = history_day_on_or_before(session, base_day), history_day_on_or_before(session, current_day)
    if hb is None:
        return hb, hc, f"No formation run recorded on or before {base_day}."
    if hc is None or hc == hb:
        return hb, hc, f"No formation run recorded between {base_day} and {current_day}."
    return hb, hc, None


def formation_changes(session: Session, anchor_ids: Iterable[uuid.UUID] | None = None
                      ) -> dict[uuid.UUID, FormationChangeBadge]:
    """anchor_id -> change badge (NEW / STRENGTHENED / WEAKENED / UNCHANGED) since the previous snapshot,
    for the candidates qualifying on the current side. Empty when there is no comparison."""

    def compute() -> dict[uuid.UUID, FormationChangeBadge]:
        pair = default_pair(session)
        if pair is None:
            return {}
        hb, hc, reason = _history_pair(session, *pair)
        if reason or hb is None or hc is None:
            return {}
        base, cur = _history(session, hb, True), _history(session, hc, True)
        out = {}
        for aid, row in cur.items():
            st = formation_status(base.get(aid), row)
            b = base.get(aid)
            out[aid] = FormationChangeBadge(status=st, base_day=pair[0], base_score=round(b.score, 1) if b else None,
                                            base_tier=b.tier if b and b.tier in TIER_RANK else None,
                                            current_score=round(row.score, 1), threshold=MATERIAL)
        return out

    allb = memo.memoized_view(session, ("formation_changes",), compute)
    if anchor_ids is None:
        return dict(allb)
    return {a: allb[a] for a in anchor_ids if a in allb}


def _drop_reasons(session: Session, day: date, anchors: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Why dropped anchors no longer qualify — only when ``day`` is the current formation state (one
    formation ``compute`` explains them all); otherwise nothing (callers say "no longer qualifies")."""
    if not anchors:
        return {}
    latest = session.scalar(select(func.max(FormationCandidate.day)))
    if latest != day:
        return {}
    from radar.scoring import formation as F

    rejected: list[Any] = []
    try:
        F.compute(session, day, rejected=rejected)
    except Exception:  # noqa: BLE001 — reasons are a nicety; never fail the report over them
        return {}
    wanted = set(anchors)
    out: dict[uuid.UUID, str] = {}
    for r in rejected:
        if r.anchor_id in wanted and r.anchor_id not in out:
            out[r.anchor_id] = r.detail or r.reason
    return out


def _formation_section(session: Session, base_day: date, current_day: date, include_demo: bool,
                       explain: bool = True) -> FormationChanges:
    hb, hc, reason = _history_pair(session, base_day, current_day)
    if reason or hb is None or hc is None:
        return FormationChanges(available=False, reason=reason, base_day=hb, current_day=hc,
                                counts=dict.fromkeys(("NEW", "DROPPED", "STRENGTHENED", "WEAKENED", "UNCHANGED"), 0))
    base, cur = _history(session, hb, include_demo), _history(session, hc, include_demo)
    by: dict[str, list[FormationChange]] = defaultdict(list)
    for aid in sorted(set(base) | set(cur), key=str):
        b, c = base.get(aid), cur.get(aid)
        st = formation_status(b, c)
        row = c or b
        by[st].append(FormationChange(
            anchor_id=str(aid), anchor_type=row.anchor_type, slug=row.slug, title=row.title, kind=row.kind, status=st,
            base_score=round(b.score, 1) if b else None, current_score=round(c.score, 1) if c else None,
            base_tier=b.tier if b else None, current_tier=c.tier if c else None))
    _attach_anchor_slugs(session, [x for v in by.values() for x in v])
    dropped = by["DROPPED"]
    if dropped:
        reasons = _drop_reasons(session, hc, [uuid.UUID(x.anchor_id) for x in dropped]) if explain else {}
        for x in dropped:
            x.reason = reasons.get(uuid.UUID(x.anchor_id)) or "No longer qualifies as a formation candidate."

    def delta(x: FormationChange) -> float:
        return (x.current_score or 0) - (x.base_score or 0)

    return FormationChanges(
        available=True, base_day=hb, current_day=hc, threshold=MATERIAL,
        counts={k: len(by[k]) for k in ("NEW", "DROPPED", "STRENGTHENED", "WEAKENED", "UNCHANGED")},
        new=sorted(by["NEW"], key=lambda x: (-(x.current_score or 0), x.slug))[:ITEM_LIMIT],
        dropped=sorted(dropped, key=lambda x: (-(x.base_score or 0), x.slug))[:ITEM_LIMIT],
        strengthened=sorted(by["STRENGTHENED"], key=lambda x: (-delta(x), x.slug))[:ITEM_LIMIT],
        weakened=sorted(by["WEAKENED"], key=lambda x: (delta(x), x.slug))[:ITEM_LIMIT],
    )


# ── new to Radar ─────────────────────────────────────────────────────────────


def _company_created(c: Company) -> datetime | None:
    if c.founded_date:
        return datetime.combine(c.founded_date, time(0), tzinfo=UTC)
    return _parse_dt((c.attrs or {}).get("gh_org_created_at"))


def _company_ref(c: Company) -> EntityRef:
    return EntityRef(type="company", id=str(c.id), slug=c.slug, name=P.display_name(c).name)


def _count(session: Session, q) -> int:
    return int(session.scalar(select(func.count()).select_from(q.subquery())) or 0)


def _new_companies(session: Session, lo: datetime, hi: datetime, current_day: date, demo: bool) -> ChangeSection:
    cond = [_in_window(Company.first_seen_at, lo, hi), Company.dismissed.is_(False)]
    if not demo:
        cond.append(Company.is_demo.is_(False))
    rows = session.execute(
        select(Company, ScoreSnapshot.score).outerjoin(ScoreSnapshot, and_(
            ScoreSnapshot.entity_id == Company.id, ScoreSnapshot.day == current_day)).where(*cond)
        .order_by(ScoreSnapshot.score.desc().nulls_last(), Company.name, Company.id).limit(ITEM_LIMIT)).all()
    items = [ChangeItem(entity_type="company", id=str(c.id), slug=c.slug, name=P.display_name(c).name,
                        url=c.website, created_at=_company_created(c), first_seen_at=c.first_seen_at,
                        score=round(sc, 1) if sc is not None else None, detail=P.one_liner(c)) for c, sc in rows]
    return ChangeSection(count=_count(session, select(Company.id).where(*cond)), items=items)


def _new_people(session: Session, lo: datetime, hi: datetime, current_day: date, demo: bool) -> ChangeSection:
    cond = [_in_window(Person.first_seen_at, lo, hi), Person.dismissed.is_(False)]
    if not demo:
        cond.append(Person.is_demo.is_(False))
    rows = session.execute(
        select(Person, ScoreSnapshot.score).outerjoin(ScoreSnapshot, and_(
            ScoreSnapshot.entity_id == Person.id, ScoreSnapshot.day == current_day)).where(*cond)
        .order_by(ScoreSnapshot.score.desc().nulls_last(), Person.name, Person.id).limit(ITEM_LIMIT)).all()
    items = [ChangeItem(entity_type="person", id=str(p.id), slug=p.slug, name=P.display_name(p).name,
                        url=f"https://github.com/{p.github_handle}" if p.github_handle and not p.is_demo else None,
                        first_seen_at=p.first_seen_at, score=round(sc, 1) if sc is not None else None,
                        detail=P.headline(p)) for p, sc in rows]
    return ChangeSection(count=_count(session, select(Person.id).where(*cond)), items=items)


def _companies_by_id(session: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, Company]:
    ids = {i for i in ids if i is not None}
    return {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(ids)))} if ids else {}


def _new_repositories(session: Session, lo: datetime, hi: datetime, demo: bool) -> ChangeSection:
    cond = [_in_window(Repository.first_seen_at, lo, hi)]
    if not demo:
        cond.append(Repository.is_demo.is_(False))
    rows = list(session.scalars(select(Repository).where(*cond)
                                .order_by(Repository.stars.desc(), Repository.full_name).limit(ITEM_LIMIT)))
    owners = _companies_by_id(session, (r.company_id for r in rows))
    items = [ChangeItem(entity_type="repository", id=str(r.id), slug=r.full_name, name=r.full_name,
                        url=None if r.is_demo else f"https://github.com/{r.full_name}", created_at=r.gh_created_at,
                        first_seen_at=r.first_seen_at, detail=f"{r.stars:,} stars" + (
                            f" · {r.language}" if r.language else ""),
                        parent=_company_ref(owners[r.company_id]) if r.company_id in owners else None)
             for r in rows]
    return ChangeSection(count=_count(session, select(Repository.id).where(*cond)), items=items)


def _paper_item(p: ResearchPaper, detail: str | None = None) -> ChangeItem:
    url = p.url or (None if p.is_demo else f"https://arxiv.org/abs/{p.arxiv_id}")
    return ChangeItem(entity_type="paper", id=str(p.id), slug=p.arxiv_id, name=p.title, url=url,
                      created_at=p.published_at, first_seen_at=p.first_seen_at,
                      detail=detail or (p.primary_category or None))


def _new_papers(session: Session, lo: datetime, hi: datetime, demo: bool) -> ChangeSection:
    cond = [_in_window(ResearchPaper.first_seen_at, lo, hi)]
    if not demo:
        cond.append(ResearchPaper.is_demo.is_(False))
    rows = session.scalars(select(ResearchPaper).where(*cond).order_by(
        ResearchPaper.published_at.desc().nulls_last(), ResearchPaper.arxiv_id).limit(ITEM_LIMIT))
    return ChangeSection(count=_count(session, select(ResearchPaper.id).where(*cond)),
                         items=[_paper_item(p) for p in rows])


def _new_research(session: Session, lo: datetime, hi: datetime, demo: bool) -> ChangeSection:
    """New papers with at least one author already on Radar before the window (new work by known people)."""
    known = exists(select(PaperAuthor.id).join(Person, Person.id == PaperAuthor.person_id).where(
        PaperAuthor.paper_id == ResearchPaper.id, Person.first_seen_at <= lo, Person.dismissed.is_(False)))
    cond = [_in_window(ResearchPaper.first_seen_at, lo, hi), known]
    if not demo:
        cond.append(ResearchPaper.is_demo.is_(False))
    rows = list(session.scalars(select(ResearchPaper).where(*cond).order_by(
        ResearchPaper.published_at.desc().nulls_last(), ResearchPaper.arxiv_id).limit(ITEM_LIMIT)))
    authors: dict[uuid.UUID, list[str]] = defaultdict(list)
    if rows:
        for pid, name in session.execute(
                select(PaperAuthor.paper_id, Person.name).join(Person, Person.id == PaperAuthor.person_id)
                .where(PaperAuthor.paper_id.in_([r.id for r in rows]), Person.first_seen_at <= lo)
                .order_by(PaperAuthor.paper_id, PaperAuthor.position, Person.name)):
            authors[pid].append(name)
    items = [_paper_item(p, "By " + ", ".join(authors[p.id][:3]) + " (on Radar before)" if authors[p.id] else None)
             for p in rows]
    return ChangeSection(count=_count(session, select(ResearchPaper.id).where(*cond)), items=items)


def _new_jobs(session: Session, lo: datetime, hi: datetime, demo: bool) -> ChangeSection:
    cond = [_in_window(Job.first_seen_at, lo, hi), Company.dismissed.is_(False)]
    if not demo:
        cond.append(Job.is_demo.is_(False))
    q = select(Job, Company).join(Company, Company.id == Job.company_id).where(*cond)
    rows = session.execute(q.order_by(Job.posted_at.desc().nulls_last(), Company.name, Job.title, Job.id)
                           .limit(ITEM_LIMIT)).all()
    items = [ChangeItem(entity_type="job", id=str(j.id), slug=c.slug, name=j.title, url=j.url, created_at=j.posted_at,
                        first_seen_at=j.first_seen_at,
                        detail=", ".join(x for x in (j.role_class, j.location) if x) or None, parent=_company_ref(c))
             for j, c in rows]
    return ChangeSection(count=_count(session, select(Job.id).join(Company, Company.id == Job.company_id).where(*cond)),
                         items=items)


def _new_signals(session: Session, lo: datetime, hi: datetime, demo: bool) -> tuple[int, list[FamilySignals]]:
    cond = [_in_window(Signal.detected_at, lo, hi)]
    if not demo:
        cond.append(Signal.is_demo.is_(False))
    by_source = dict(session.execute(select(Signal.source_type, func.count()).where(*cond)
                                     .group_by(Signal.source_type)).tuples().all())
    fam_sources: dict[str, list[str]] = defaultdict(list)
    for src in by_source:
        fam_sources[SOURCE_FAMILY.get(src, "other")].append(src)
    out = []
    for fam in (*FAMILIES, "other"):
        srcs = sorted(fam_sources.get(fam, []))
        if not srcs:
            continue
        sigs = list(session.scalars(select(Signal).where(*cond, Signal.source_type.in_(srcs))
                                    .order_by(Signal.event_at.desc(), Signal.id).limit(SIGNAL_ITEM_LIMIT)))
        ids: dict[str, set[uuid.UUID]] = defaultdict(set)
        for s in sigs:
            ids[s.entity_type].add(s.entity_id)
        from radar.services.entities import entity_refs

        refs = entity_refs(session, ids)
        items = []
        for s in sigs:
            r = refs.get(s.entity_id)
            items.append(ChangeItem(
                entity_type="signal", id=str(s.id), slug=r["slug"] if r else None, name=s.title[:300],
                url=s.source_url, created_at=s.event_at, first_seen_at=s.detected_at, detail=s.signal_type,
                parent=EntityRef(type=r["type"], id=r["id"], slug=r["slug"], name=r["name"]) if r else None))
        out.append(FamilySignals(family=fam, label=FAMILY_TITLE.get(fam, fam.title()),
                                 count=sum(by_source[s] for s in srcs), items=items))
    return sum(by_source.values()), out


# ── scores & collisions ──────────────────────────────────────────────────────


def _visible_ids(session: Session, model, ids: Iterable[uuid.UUID], demo: bool) -> set[uuid.UUID]:
    ids = list(set(ids))
    if not ids:
        return set()
    q = select(model.id).where(model.id.in_(ids), model.dismissed.is_(False))
    if not demo:
        q = q.where(model.is_demo.is_(False))
    return set(session.scalars(q))


def _component_values(snap: ScoreSnapshot) -> dict[str, float]:
    out = {}
    for k, v in (snap.components or {}).items():
        if isinstance(v, dict) and isinstance(v.get("value"), int | float):
            out[k] = float(v["value"])
    return out


def _acc_raw(snap: ScoreSnapshot) -> float | None:
    raw = ((snap.components or {}).get("acceleration") or {}).get("raw") or {}
    v = raw.get("acc")
    return float(v) if isinstance(v, int | float) else None


def _score_change(c: Company, b: ScoreSnapshot, s: ScoreSnapshot) -> ScoreChange:
    bv, cv = _component_values(b), _component_values(s)
    return ScoreChange(entity_type="company", id=str(c.id), slug=c.slug, name=P.display_name(c).name,
                       base_score=round(b.score, 1), current_score=round(s.score, 1), delta=round(s.score - b.score, 1),
                       components_delta={k: round(cv[k] - bv[k], 1) for k in sorted(set(bv) & set(cv))})


def _score_sections(session: Session, base_day: date, current_day: date, demo: bool
                    ) -> tuple[ScoreChanges, ScoreChangeList, dict[str, int]]:
    base = P.snapshots_on_day(session, base_day, "company")
    cur = P.snapshots_on_day(session, current_day, "company")
    visible = _visible_ids(session, Company, set(base) | set(cur), demo)
    base = {k: v for k, v in base.items() if k in visible}
    cur = {k: v for k, v in cur.items() if k in visible}
    cohort = {"base": sum(1 for s in base.values() if not s.is_demo),
              "current": sum(1 for s in cur.values() if not s.is_demo)}
    ok, reason, bv, cv = versions_comparable(
        [s.version for s in base.values() if not P.is_reconstructed(s)],
        [s.version for s in cur.values() if not P.is_reconstructed(s)])
    out = ScoreChanges(comparable=ok, reason=reason, base_version=bv, current_version=cv, threshold=MATERIAL,
                       no_baseline=sum(1 for k in cur if k not in base))
    momentum = ScoreChangeList()
    if not ok:
        out.not_comparable = sum(1 for k in cur if k in base)
        return out, momentum, cohort
    structural = []
    for eid, s in cur.items():
        b = base.get(eid)
        if b is None:
            continue
        good, _ = score_comparability(b, s, True)
        if good:
            structural.append(eid)
        else:
            out.not_comparable += 1
    seen = reobserved_ids(session, "company", structural, base_day, current_day)
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(structural)))} if structural else {}
    up, down, mom = [], [], []
    for eid in structural:
        if eid not in seen:
            out.not_reobserved += 1
            continue
        out.compared += 1
        b, s, c = base[eid], cur[eid], companies[eid]
        ch = _score_change(c, b, s)
        move = material_delta(ch.delta)
        (up if move == "up" else down if move == "down" else []).append(ch)
        if move is None:
            out.unchanged += 1
        tm = ch.components_delta.get("technical_momentum")
        ab, ac = _acc_raw(b), _acc_raw(s)
        if (tm is not None and tm >= MOMENTUM_COMPONENT_MIN) or (
                ab is not None and ac is not None and ac - ab >= ACCELERATION_RAW_MIN):
            mom.append(ch)
    up.sort(key=lambda x: (-x.delta, x.name, x.id))
    down.sort(key=lambda x: (x.delta, x.name, x.id))
    mom.sort(key=lambda x: (-x.components_delta.get("technical_momentum", 0.0), -x.delta, x.name, x.id))
    out.accelerated = ScoreChangeList(count=len(up), items=up[:ITEM_LIMIT])
    out.weakened = ScoreChangeList(count=len(down), items=down[:ITEM_LIMIT])
    momentum = ScoreChangeList(count=len(mom), items=mom[:ITEM_LIMIT])
    return out, momentum, cohort


def _collision_rows(session: Session, day: date) -> dict[uuid.UUID, tuple[str, str | None, list[str]]]:
    """entity_id -> (entity_type, strength, families) for every snapshot on ``day`` (light columns)."""
    rows = session.execute(select(ScoreSnapshot.entity_id, ScoreSnapshot.entity_type, ScoreSnapshot.collision)
                           .where(ScoreSnapshot.day == day))
    return {eid: (et, strength_of(col), list((col or {}).get("families") or [])) for eid, et, col in rows}


def _collision_sections(session: Session, base_day: date, current_day: date, demo: bool
                        ) -> tuple[CollisionChanges, CollisionChanges]:
    base, cur = _collision_rows(session, base_day), _collision_rows(session, current_day)
    started = [eid for eid, (_, st, _) in cur.items()
               if st in STRONG_COLLISION and (base.get(eid) or (None, None, []))[1] not in STRONG_COLLISION]
    ended = [eid for eid, (_, st, _) in base.items()
             if st in STRONG_COLLISION and (cur.get(eid) or (None, None, []))[1] not in STRONG_COLLISION]
    ids = set(started) | set(ended)
    visible = (_visible_ids(session, Company, ids, demo) | _visible_ids(session, Person, ids, demo))
    from radar.services.entities import entity_refs

    def section(eids: list[uuid.UUID], side: dict, other: dict) -> CollisionChanges:
        eids = [e for e in eids if e in visible]
        strength_rank = {"HIGH": 0, "MEDIUM": 1}
        eids.sort(key=lambda e: (strength_rank.get(side[e][1], 2), -len(side[e][2]), str(e)))
        top = eids[:ITEM_LIMIT]
        by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
        for e in top:
            by_type[side[e][0]].add(e)
        refs = entity_refs(session, by_type)
        items = []
        for e in top:
            r = refs.get(e) or {"slug": None, "name": str(e)}
            b, c = base.get(e), cur.get(e)
            items.append(CollisionChange(
                entity_type=side[e][0], id=str(e), slug=r["slug"], name=r["name"],
                base_strength=b[1] if b else None, current_strength=c[1] if c else None,
                families=side[e][2], new_entity=b is None))
        items.sort(key=lambda x: (strength_rank.get((side[uuid.UUID(x.id)][1]), 2), -len(x.families), x.name, x.id))
        return CollisionChanges(count=len(eids), items=items)

    return section(started, cur, base), section(ended, base, cur)


# ── Who Should We Meet ───────────────────────────────────────────────────────


def _meet_section(session: Session, base_day: date, current_day: date, demo: bool) -> MeetChanges:
    from radar.services import meet

    wb = meet.iso_week(datetime.combine(base_day, time(18, 0), tzinfo=UTC))
    wc = meet.iso_week(datetime.combine(current_day, time(18, 0), tzinfo=UTC))
    if wb == wc:
        return MeetChanges(available=False, reason=f"Both days fall in ISO week {wc}.", base_week=wb, current_week=wc)

    def ranks(week: str) -> dict[uuid.UUID, int]:
        q = select(MeetingRecommendation.person_id, MeetingRecommendation.rank).where(MeetingRecommendation.week == week)
        if not demo:
            q = q.where(MeetingRecommendation.is_demo.is_(False))
        return dict(session.execute(q).tuples().all())

    rb, rc = ranks(wb), ranks(wc)
    if not rb or not rc:
        missing = wb if not rb else wc
        return MeetChanges(available=False, reason=f"No Who Should We Meet list stored for {missing}.",
                           base_week=wb, current_week=wc)
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(set(rb) | set(rc))))}

    def move(pid: uuid.UUID) -> MeetMove:
        p = people.get(pid)
        ref = EntityRef(type="person", id=str(pid), slug=p.slug if p else None,
                        name=P.display_name(p).name if p else str(pid))
        b, c = rb.get(pid), rc.get(pid)
        return MeetMove(person=ref, base_rank=b, current_rank=c, change=(b - c) if b is not None and c is not None else None)

    entered = sorted((move(p) for p in rc if p not in rb), key=lambda m: (m.current_rank or 0, m.person.id))
    left = sorted((move(p) for p in rb if p not in rc), key=lambda m: (m.base_rank or 0, m.person.id))
    moved = sorted((move(p) for p in rc if p in rb and rb[p] != rc[p]), key=lambda m: (m.current_rank or 0, m.person.id))
    return MeetChanges(available=True, base_week=wb, current_week=wc, entered=entered, left=left, moved=moved)


# ── report ───────────────────────────────────────────────────────────────────


def _caveats(session: Session, base_day: date, current_day: date, scores: ScoreChanges, cohort: dict[str, int],
             points: dict[date, dict[str, Any]]) -> list[str]:
    out = []
    if cohort["base"] and cohort["current"]:
        out.append(
            f"Scores are cohort-relative percentiles: {cohort['current']:,} companies were scored on {current_day} vs "
            f"{cohort['base']:,} on {base_day}, so part of any score change reflects who else is in the cohort, "
            "not only the company itself.")
    else:
        out.append("Scores are cohort-relative percentiles; a change in cohort moves them by itself.")
    breadth = [f"{d} ({p.get('files')} provider batches)" for d, p in sorted(points.items())
               if d in (base_day, current_day) and p.get("files") is not None]
    out.append(
        "\"New\" means new to Radar — first observed in the later capture — not newly created; creation and "
        "publication dates are shown where the source records them. A capture covers only what its searches reached"
        + (f" (captures: {', '.join(breadth)})." if breadth else "."))
    if scores.comparable:
        out.append(f"Scores on both days use version {scores.current_version}; only live scores are compared, "
                   "never reconstructed history.")
    elif scores.reason:
        out.append(scores.reason)
    if scores.not_reobserved:
        out.append(f"{scores.not_reobserved:,} companies scored on both days were not re-observed in between; their "
                   "scores are not compared.")
    if current_day not in points:
        out.append(f"{current_day} is the live database, not a verified snapshot.")
    return out


def _resolve_days(session: Session, base_day: date | None, current_day: date | None, points: list[date]
                  ) -> tuple[date, date] | None:
    if base_day is None and current_day is None:
        return default_pair(session, points)
    if current_day is None:
        later = [p for p in points if p > base_day]  # type: ignore[operator]
        current_day = later[-1] if later else (clock.today() if is_live_mode() else None)
        if current_day is None:
            raise InvalidRange(f"No snapshot after {base_day} to compare with.")
    if base_day is None:
        earlier = [p for p in points if p < current_day]
        if not earlier:
            raise InvalidRange(f"No snapshot before {current_day} to compare with.")
        base_day = earlier[-1]
    if base_day >= current_day:
        raise InvalidRange("base must be earlier than current.")
    return base_day, current_day


def compare(session: Session, base_day: date | None = None, current_day: date | None = None, *,
            include_demo: bool | None = None, explain_dropped: bool = True) -> ChangesReport:
    """What changed between ``base_day`` and ``current_day`` (defaults: previous vs latest snapshot)."""
    from radar.services.formation import include_demo_default

    demo = include_demo_default(include_demo)
    rows = dict(_point_rows(session))
    points = sorted(rows)
    pair = _resolve_days(session, base_day, current_day, points)
    if pair is None:
        return ChangesReport(available=False, points=points, reason=(
            "Only one snapshot has been loaded; there is nothing earlier to compare with." if points
            else "No snapshot has been loaded."))
    b, c = pair

    def build() -> ChangesReport:
        lo, hi = day_end(b), day_end(c)
        scores, momentum, cohort = _score_sections(session, b, c, demo)
        new_col, ended_col = _collision_sections(session, b, c, demo)
        total, by_family = _new_signals(session, lo, hi, demo)
        return ChangesReport(
            available=True, base_day=b, current_day=c, points=points, is_snapshot=c in rows,
            new_companies=_new_companies(session, lo, hi, c, demo), new_people=_new_people(session, lo, hi, c, demo),
            new_repositories=_new_repositories(session, lo, hi, demo), new_papers=_new_papers(session, lo, hi, demo),
            new_jobs=_new_jobs(session, lo, hi, demo), new_research=_new_research(session, lo, hi, demo),
            new_signals_total=total, new_signals_by_family=by_family,
            score_changes=scores, momentum_acceleration=momentum,
            new_collisions=new_col, collisions_ended=ended_col,
            formation=_formation_section(session, b, c, demo, explain_dropped),
            meet=_meet_section(session, b, c, demo),
            caveats=_caveats(session, b, c, scores, cohort, rows),
        )

    return memo.memoized_view(session, ("changes", b, c, demo, explain_dropped), build)


__all__ = ["ACCELERATION_RAW_MIN", "MATERIAL", "MOMENTUM_COMPONENT_MIN", "InvalidRange", "company_since", "compare",
           "default_pair", "dominant_version", "entity_since", "formation_changes", "formation_status",
           "material_delta", "person_since", "points_out", "reobserved_ids", "score_comparability",
           "snapshot_points", "strength_of", "versions_comparable"]
