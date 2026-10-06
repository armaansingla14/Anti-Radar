"""Radar feed ("Morning Intelligence") and Daily Radar ("What changed since yesterday?").

Ranking (documented, deterministic)
-----------------------------------
Feeds rank by today's ANTI SCORE with a small *change* boost so that entities
whose signals moved recently float above equally-scored static ones::

    priority = score
             + (2 + 0.3 × min(Δ7d, 10))      if Δ7d > 0            (max +5)
             + 4 × max(0, 1 − age_days / 7)  age of newest signal  (max +4)

The boost is bounded (≤ 9 points) so it re-orders near-ties but never lets a weak
entity leapfrog a much stronger one.

Thesis shifts compare, per thesis topic, the tagged companies' scores now vs
seven days ago plus signal volume this week vs last::

    momentum = mean(company Δ7d) + 0.5 × (signals_7d − signals_prev_7d)
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import zip_longest
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import is_technical_class, job_class
from radar.detectors.strange import detect_strange_signals
from radar.detectors.unknown_yesterday import new_entities
from radar.llm import narratives
from radar.models import (
    Company,
    EntityTopic,
    IngestionRun,
    Job,
    Person,
    ResearchPaper,
    Signal,
    Topic,
    User,
    Watchlist,
    WatchlistItem,
)
from radar.schemas.api import (
    CompanySummary,
    DailyRadar,
    IntelCard,
    Narrative,
    PaperCard,
    PersonCard,
    RadarCounts,
    RadarFeed,
    StrangeSignal,
    ThesisShift,
    WatchChange,
)
from radar.services import presenters as P
from radar.services.entities import (
    latest_score_subq,
    paper_cards,
    rank_papers,
    signal_activity,
    theme_entity_ids,
    validate_theme,
)
from radar.settings import get_settings

CARD_LIMIT = 12
EMERGING_MIN_SCORE = 45  # "emerging companies" headline count: early-stage and scoring at least this
NEW_MIN_SCORE = 30  # "entered the radar" count ignores unscored noise
# Measurements are dated when observed (not when the underlying change happened), so they
# can't be compared week-over-week; topic momentum counts discrete events only.
MEASUREMENT_SIGNALS = frozenset({"star_velocity", "star_acceleration", "commit_acceleration", "hiring_active",
                                 "new_contributors"})
FOUNDER_LIMIT = 8
FOUNDER_FLOOR = 30  # founder cards need at least this founder score
STRANGE_LIMIT = 5
SHIFT_LIMIT = 4
POOL = 80  # candidates considered before re-ranking
NEW_DAYS = 7
DEFAULT_USER_NAME = "Anti Fund"
MAX_SINCE_DAYS = 90


# ── user / visit state ───────────────────────────────────────────────────────


def default_user(session: Session) -> User:
    """The single internal user (oldest row); created on first use."""
    u = session.scalar(select(User).order_by(User.created_at, User.id).limit(1))
    if u is None:
        u = User(name=DEFAULT_USER_NAME)
        session.add(u)
        session.flush()
    return u


def touch_last_visit(session: Session) -> datetime | None:
    """Record a visit; returns the *previous* last_visit_at (read before updating)."""
    u = default_user(session)
    prev = u.last_visit_at
    u.last_visit_at = clock.now()
    session.flush()
    return prev


def resolve_since(session: Session, since: datetime | None) -> datetime:
    """Explicit ?since wins; otherwise the earlier of (now − 24h, last visit) — a visit a few minutes
    ago never shrinks the window below a day. Clamped to MAX_SINCE_DAYS."""
    now = clock.now()
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)
        if since > now:
            raise ValueError("since must not be in the future")
    else:
        since = now - timedelta(hours=24)
        u = session.scalar(select(User).order_by(User.created_at, User.id).limit(1))
        if u is not None and u.last_visit_at is not None and u.last_visit_at < since:
            since = u.last_visit_at
    return max(since, now - timedelta(days=MAX_SINCE_DAYS))


# ── ranking ──────────────────────────────────────────────────────────────────


def change_boost(delta_7d: float | None, newest_signal: datetime | None, now: datetime) -> float:
    boost = 0.0
    if delta_7d is not None and delta_7d > 0:
        boost += 2 + 0.3 * min(delta_7d, 10)
    if newest_signal is not None:
        age = max((now - newest_signal).total_seconds() / 86400, 0.0)
        boost += 4 * max(0.0, 1 - age / 7)
    return boost


@dataclass
class Ranked:
    entity: Any
    score: float
    delta_7d: float | None
    newest: datetime | None
    activity: dict[str, Any] = field(default_factory=dict)
    priority: float = 0.0


def _rank(session: Session, entities: Sequence[Any]) -> list[Ranked]:
    ids = [e.id for e in entities]
    snaps = P.latest_snapshots(session, ids)
    d7 = P.score_deltas(session, snaps, 7)
    now = clock.now()
    act = signal_activity(session, ids, now - timedelta(days=7))
    out = []
    for e in entities:
        snap = snaps.get(e.id)
        if snap is None:
            continue
        a = act.get(e.id, {})
        r = Ranked(e, snap.score, d7.get(e.id), a.get("latest"), a)
        r.priority = r.score + change_boost(r.delta_7d, r.newest, now)
        out.append(r)
    out.sort(key=lambda r: (-r.priority, -r.score, getattr(r.entity, "name", "")))
    return out


def candidate_companies(session: Session, *, include_demo: bool, theme: str | None = None,
                        new_only: bool = False, pool: int = POOL) -> list[Company]:
    today = clock.today()
    cur = latest_score_subq("company", today, min_day=today - timedelta(days=14))
    q = (select(Company).join(cur, cur.c.entity_id == Company.id).where(Company.dismissed.is_(False))
         .order_by(cur.c.score.desc()).limit(pool))
    if not include_demo:
        q = q.where(Company.is_demo.is_(False))
    if theme:
        q = q.where(Company.id.in_(theme_entity_ids(theme)))
    if new_only:
        q = q.where(Company.first_seen_at >= clock.now() - timedelta(days=NEW_DAYS))
    return list(session.scalars(q))


def candidate_people(session: Session, *, include_demo: bool, theme: str | None = None, new_only: bool = False,
                     floor: float = FOUNDER_FLOOR, pool: int = POOL) -> list[Person]:
    today = clock.today()
    cur = latest_score_subq("person", today, min_day=today - timedelta(days=14))
    q = (select(Person).join(cur, cur.c.entity_id == Person.id)
         .where(Person.dismissed.is_(False), cur.c.score >= floor).order_by(cur.c.score.desc()).limit(pool))
    if not include_demo:
        q = q.where(Person.is_demo.is_(False))
    if theme:
        q = q.where(or_(Person.id.in_(theme_entity_ids(theme)), Person.domains.any(theme.partition(".")[0])))
    if new_only:
        q = q.where(Person.first_seen_at >= clock.now() - timedelta(days=NEW_DAYS))
    return list(session.scalars(q))


def _cap_demo(ranked: list[Ranked], limit: int) -> list[Ranked]:
    """Fictional examples are there to demonstrate behaviour; they never take more than half of a
    list when real entities are available. Order (by priority) is preserved."""
    max_demo = (limit + 1) // 2
    out, n_demo = [], 0
    for r in ranked:
        if getattr(r.entity, "is_demo", False):
            if n_demo >= max_demo:
                continue
            n_demo += 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def top_company_cards(session: Session, *, include_demo: bool, theme: str | None = None, new_only: bool = False,
                      limit: int = CARD_LIMIT) -> list[IntelCard]:
    ranked = _rank(session, candidate_companies(session, include_demo=include_demo, theme=theme, new_only=new_only))
    return P.company_cards(session, [r.entity for r in _cap_demo(ranked, limit)])


def top_founder_cards(session: Session, *, include_demo: bool, theme: str | None = None, new_only: bool = False,
                      limit: int = FOUNDER_LIMIT) -> list[IntelCard]:
    ranked = _rank(session, candidate_people(session, include_demo=include_demo, theme=theme, new_only=new_only))
    return P.founder_intel_cards(session, [r.entity for r in _cap_demo(ranked, limit)])


def strange_signals(session: Session, *, include_demo: bool, limit: int | None = STRANGE_LIMIT
                    ) -> list[StrangeSignal]:
    rows = [StrangeSignal.model_validate(s) for s in detect_strange_signals(session, clock.today())]
    if not include_demo:
        rows = [s for s in rows if not s.is_demo]
    return rows[:limit] if limit else rows


# ── thesis shifts ────────────────────────────────────────────────────────────


@dataclass
class TopicMove:
    key: str
    label: str
    theme: str
    company_ids: list[uuid.UUID]
    deltas: dict[uuid.UUID, float]
    signals_7d: int
    signals_prev_7d: int
    signal_ids_7d: list[str]
    new_papers: int
    momentum: float


def topic_moves(session: Session, *, include_demo: bool) -> list[TopicMove]:
    """Per-topic aggregate movement (see module docstring for the formula)."""
    now = clock.now()
    rows = session.execute(
        select(Topic.key, Topic.label, Topic.theme, EntityTopic.entity_id, EntityTopic.entity_type)
        .join(EntityTopic, EntityTopic.topic_id == Topic.id)
    ).all()
    if not rows:
        return []
    demo_ids: set[uuid.UUID] = set()
    if not include_demo:
        for model in (Company, Person, ResearchPaper):
            demo_ids |= set(session.scalars(select(model.id).where(model.is_demo.is_(True))))
    by_topic: dict[str, dict[str, Any]] = {}
    for key, label, theme, eid, et in rows:
        if eid in demo_ids:
            continue
        t = by_topic.setdefault(key, {"label": label, "theme": theme, "company": [], "paper": [], "all": []})
        t["all"].append(eid)
        if et in ("company", "paper"):
            t[et].append(eid)
    company_ids = {cid for t in by_topic.values() for cid in t["company"]}
    dismissed = set(session.scalars(select(Company.id).where(Company.id.in_(company_ids),
                                                             Company.dismissed.is_(True)))) if company_ids else set()
    snaps = P.latest_snapshots(session, company_ids - dismissed)
    d7 = P.score_deltas(session, snaps, 7)
    all_ids = {e for t in by_topic.values() for e in t["all"]}
    act_now = signal_activity(session, all_ids, now - timedelta(days=7))
    act_prev = signal_activity(session, all_ids, now - timedelta(days=14), now - timedelta(days=7))
    paper_ids = {e for t in by_topic.values() for e in t["paper"]}
    new_papers = set(session.scalars(select(ResearchPaper.id).where(
        ResearchPaper.id.in_(paper_ids), ResearchPaper.published_at >= now - timedelta(days=7)))) if paper_ids else set()
    paper_signal_ids = {str(sid) for sid in session.scalars(
        select(Signal.id).where(Signal.source_type == "arxiv"))}
    out = []
    for key, t in by_topic.items():
        cids = [c for c in dict.fromkeys(t["company"]) if c in snaps]
        deltas: dict[uuid.UUID, float] = {c: v for c in cids if (v := d7.get(c)) is not None}
        sids_now: dict[str, None] = {}
        sids_prev: set[str] = set()
        for e in dict.fromkeys(t["all"]):
            for s in act_now.get(e, {}).get("signals", []):
                if s.signal_type not in MEASUREMENT_SIGNALS:
                    sids_now[str(s.id)] = None
            for s in act_prev.get(e, {}).get("signals", []):
                if s.signal_type not in MEASUREMENT_SIGNALS:
                    sids_prev.add(str(s.id))
        mean = sum(deltas.values()) / len(deltas) if deltas else 0.0
        # Paper volume depends on how deep each arXiv category was fetched, so it is reported
        # but not used for week-over-week momentum.
        ev_now = [x for x in sids_now if x not in paper_signal_ids]
        ev_prev = [x for x in sids_prev if x not in paper_signal_ids]
        momentum = round(mean + 0.5 * (len(ev_now) - len(ev_prev)), 1)
        out.append(TopicMove(key, t["label"], t["theme"], cids, deltas, len(sids_now), len(sids_prev),
                             list(sids_now), sum(1 for p in dict.fromkeys(t["paper"]) if p in new_papers), momentum))
    return out


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def thesis_shifts(session: Session, *, include_demo: bool, moves: list[TopicMove] | None = None) -> list[ThesisShift]:
    moves = moves if moves is not None else topic_moves(session, include_demo=include_demo)
    out = []
    for m in moves:
        if m.momentum == 0:
            continue
        heating = m.momentum > 0
        parts = []
        moved = [d for d in m.deltas.values() if (d > 0 if heating else d < 0)]
        if not _shift_supported(m, moved, heating):
            continue
        if moved:
            mean = sum(moved) / len(moved)
            parts.append(f"{_plural(len(moved), 'tracked company', 'tracked companies')} "
                         f"{'accelerated' if heating else 'cooled'} this week (mean {mean:+.1f} score points)")
        if m.new_papers:
            parts.append(f"{_plural(m.new_papers, 'new paper')} in the last 7 days")
        out.append(ThesisShift(topic_key=m.key, label=m.label, theme=m.theme,
                               direction="heating" if heating else "cooling", detail="; ".join(parts),
                               momentum=m.momentum))
    return _select_shifts(out, moves)


def _shift_supported(m: TopicMove, moved: list[float], heating: bool) -> bool:
    """A thesis shift needs real breadth of evidence, not one entity or one week of noise."""
    if heating:
        return len(moved) >= 2 and m.momentum >= 2
    return len(moved) >= 2 and sum(moved) / len(moved) <= -3


def _select_shifts(shifts: list[ThesisShift], moves: list[TopicMove]) -> list[ThesisShift]:
    """Largest moves first, alternating heating/cooling so both directions show, and skipping topics
    whose evidence (same companies, same signals) duplicates an already-listed topic."""
    sig = {m.key: (frozenset(m.company_ids), frozenset(m.signal_ids_7d)) for m in moves}
    heat = sorted((s for s in shifts if s.direction == "heating"), key=lambda s: (-s.momentum, s.topic_key))
    cool = sorted((s for s in shifts if s.direction == "cooling"), key=lambda s: (s.momentum, s.topic_key))
    seen: set[tuple[frozenset, frozenset]] = set()
    out: list[ThesisShift] = []
    for pair in zip_longest(heat, cool):
        for s in pair:
            if s is None or sig[s.topic_key] in seen:
                continue
            seen.add(sig[s.topic_key])
            out.append(s)
    return out


# ── counts / freshness ───────────────────────────────────────────────────────


def _counts(session: Session, *, include_demo: bool, n_strange: int, n_shifts: int) -> RadarCounts:
    today, now = clock.today(), clock.now()
    cur = latest_score_subq("company", today, min_day=today - timedelta(days=14))
    early = Company.stage.not_in(("growth", "public", "series_a"))
    q = (select(func.count()).select_from(Company).join(cur, cur.c.entity_id == Company.id)
         .where(Company.dismissed.is_(False), early, cur.c.score >= EMERGING_MIN_SCORE))
    pcur = latest_score_subq("person", today, min_day=today - timedelta(days=14))
    pq = (select(func.count()).select_from(Person).join(pcur, pcur.c.entity_id == Person.id)
          .where(Person.dismissed.is_(False), pcur.c.score >= 50))
    sq = select(func.count()).select_from(Signal).where(Signal.event_at >= now - timedelta(hours=24),
                                                        Signal.event_at <= now)
    if not include_demo:
        q, pq, sq = (q.where(Company.is_demo.is_(False)), pq.where(Person.is_demo.is_(False)),
                     sq.where(Signal.is_demo.is_(False)))
    ne = new_entities(session, today, days=NEW_DAYS)
    n_new = sum(1 for rows in ne.values() for r in rows
                if (include_demo or not r["is_demo"]) and (r.get("score") or 0) >= NEW_MIN_SCORE)
    return RadarCounts(emerging_companies=session.scalar(q) or 0, founders=session.scalar(pq) or 0,
                       strange_signals=n_strange, thesis_shifts=n_shifts, new_entities_7d=n_new,
                       signals_24h=session.scalar(sq) or 0)


def data_freshness(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(IngestionRun.provider, func.max(IngestionRun.finished_at))
        .where(IngestionRun.status.in_(("ok", "partial")), IngestionRun.finished_at.is_not(None))
        .group_by(IngestionRun.provider)
    )
    return {p: d.isoformat() for p, d in rows if d}


def snapshot_note() -> str | None:
    a = clock.as_of()
    return f"Real public data captured {a:%b %-d, %Y} · fictional examples labeled DEMO" if a else None


# ── /api/radar ───────────────────────────────────────────────────────────────


def radar_feed(session: Session, *, include_demo: bool | None = None, new_only: bool = False,
               theme: str | None = None) -> RadarFeed:
    settings = get_settings()
    include_demo = settings.demo_mode if include_demo is None else include_demo
    theme = validate_theme(theme)
    cards = top_company_cards(session, include_demo=include_demo, theme=theme, new_only=new_only)
    founders = top_founder_cards(session, include_demo=include_demo, theme=theme, new_only=new_only)
    strange_all = strange_signals(session, include_demo=include_demo, limit=None)
    if theme:
        shown = {c.id for c in cards} | {f.id for f in founders}
        strange_all = [s for s in strange_all if any(e.get("id") in shown for e in s.entities)]
    if new_only:
        cutoff = clock.now() - timedelta(days=NEW_DAYS)
        new_ids = {str(i) for model in (Company, Person)
                   for i in session.scalars(select(model.id).where(model.first_seen_at >= cutoff))}
        strange_all = [s for s in strange_all if any(e.get("id") in new_ids for e in s.entities)]
    shifts = thesis_shifts(session, include_demo=include_demo)
    if theme:
        shifts = [s for s in shifts if s.theme == theme.partition(".")[0]]
    return RadarFeed(
        date=clock.today(), generated_at=clock.now(),
        counts=_counts(session, include_demo=include_demo, n_strange=len(strange_all), n_shifts=len(shifts)),
        cards=cards, founders=founders, strange_signals=strange_all[:STRANGE_LIMIT],
        thesis_shifts=shifts[:SHIFT_LIMIT], data_freshness=data_freshness(session),
        demo_mode=settings.demo_mode, snapshot_note=snapshot_note(),
    )


# ── /api/daily ───────────────────────────────────────────────────────────────


def _changed(session: Session, entities: Sequence[Any], since: datetime, *, use_7d: bool
             ) -> list[tuple[Any, float, dict[str, Any]]]:
    """[(entity, change_key, activity)] for entities whose score rose or that have new signals since."""
    ids = [e.id for e in entities]
    snaps = P.latest_snapshots(session, ids)
    if use_7d:
        delta = P.score_deltas(session, snaps, 7)
    else:
        past = P.latest_snapshots(session, ids, since.date())
        delta = {i: round(snaps[i].score - past[i].score, 1) if i in snaps and i in past and
                 past[i].day < snaps[i].day else None for i in ids}
    act = signal_activity(session, ids, since)
    out = []
    for e in entities:
        d = delta.get(e.id) or 0.0
        a = act.get(e.id, {})
        n = a.get("count", 0)
        if d <= 0 and n == 0:
            continue
        out.append((e, max(d, 0.0) + 3 * n, a))
    out.sort(key=lambda t: (-t[1], -(snaps[t[0].id].score if t[0].id in snaps else 0)))
    return out


def recent_papers(session: Session, *, include_demo: bool, days: int = 30, limit: int = 3,
                  with_why: bool = True) -> list[PaperCard]:
    """Papers published (or first seen) in the window, ranked by entities.rank_papers."""
    lo = clock.now() - timedelta(days=days)
    q = select(ResearchPaper).where(or_(ResearchPaper.published_at >= lo, ResearchPaper.first_seen_at >= lo),
                                    ResearchPaper.published_at <= clock.now() + timedelta(days=1))
    if not include_demo:
        q = q.where(ResearchPaper.is_demo.is_(False))
    papers = list(session.scalars(q.order_by(ResearchPaper.published_at.desc()).limit(300)))
    scores = rank_papers(session, papers)
    top = sorted(papers, key=lambda p: (-scores.get(p.id, 0), -(p.published_at or lo).timestamp()))[:limit]
    why = {p.id: narratives.paper_significance(p)["text"] for p in top} if with_why else None
    return paper_cards(session, top, scores=scores, why=why)


_SIGNAL_NOUN = {
    "job_posted": ("new job posting", "new job postings"),
    "star_velocity": ("star-velocity spike", "star-velocity spikes"),
    "star_acceleration": ("star acceleration", "star accelerations"),
    "paper_published": ("new paper", "new papers"),
    "paper_with_code": ("paper with code", "papers with code"),
    "release_published": ("new release", "new releases"),
    "hn_discussion": ("Hacker News discussion", "Hacker News discussions"),
    "website_changed": ("website change", "website changes"),
    "affiliation_changed": ("affiliation change", "affiliation changes"),
}


def _signal_phrase(t: str, n: int) -> str:
    one, many = _SIGNAL_NOUN.get(t, (t.replace("_", " ") + " signal", t.replace("_", " ") + " signals"))
    return f"{n} {one if n == 1 else many}"


def watchlist_changes(session: Session, *, include_demo: bool, limit: int = 3) -> list[WatchChange]:
    items = list(session.scalars(select(WatchlistItem).join(Watchlist).where(
        WatchlistItem.entity_id.is_not(None), WatchlistItem.entity_type.in_(("company", "person")))))
    if not items:
        return []
    cids = [i.entity_id for i in items if i.entity_type == "company"]
    pids = [i.entity_id for i in items if i.entity_type == "person"]
    ents: dict[uuid.UUID, Any] = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(cids)))}
    ents.update({p.id: p for p in session.scalars(select(Person).where(Person.id.in_(pids)))})
    snaps = P.latest_snapshots(session, ents)
    metrics = P.company_metrics(session, cids)
    earliest = min(i.last_seen_at for i in items)
    act = signal_activity(session, list(ents), earliest)
    jobs = defaultdict(list)
    for j in session.scalars(select(Job).where(Job.company_id.in_(cids), Job.first_seen_at >= earliest)):
        jobs[j.company_id].append(j)
    out = []
    for it in items:
        e = ents.get(it.entity_id)
        if e is None or (e.is_demo and not include_demo):
            continue
        seen = it.last_seen_at
        changes: list[str] = []
        snap = snaps.get(e.id)
        past = P.latest_snapshots(session, [e.id], seen.date()).get(e.id)
        delta = round(snap.score - past.score, 1) if snap and past and past.day < snap.day else None
        if delta:
            changes.append(f"ANTI SCORE {'+' if delta > 0 else ''}{delta:g} since last review")
        new_jobs = [j for j in jobs.get(e.id, []) if (j.posted_at or j.first_seen_at) >= seen
                    or j.first_seen_at >= seen]
        tech = sum(1 for j in new_jobs if is_technical_class(job_class(j)))
        if tech:
            changes.append(f"{tech} new engineering role{'s' if tech != 1 else ''}")
        if len(new_jobs) - tech:
            n = len(new_jobs) - tech
            changes.append(f"{n} new non-technical role{'s' if n != 1 else ''}")
        m = metrics.get(e.id) or {}
        s30, total = m.get("stars_30d"), m.get("stars_total")
        if s30 and total and total - s30 > 0:
            pct = s30 / (total - s30) * 100
            if pct >= 5:
                changes.append(f"GitHub stars +{pct:.0f}% (30d)")
        sigs = [s for s in act.get(e.id, {}).get("signals", []) if s.event_at >= seen
                and s.signal_type not in ("job_posted", "hiring_active")]
        by_type: dict[str, int] = defaultdict(int)
        for s in sigs:
            by_type[s.signal_type] += 1
        changes += [_signal_phrase(t, n) for t, n in sorted(by_type.items(), key=lambda kv: -kv[1])[:3]]
        if not changes:
            continue
        out.append(WatchChange(entity_type=it.entity_type, entity_id=str(e.id), slug=e.slug, name=e.name,
                               changes=changes, score=round(snap.score) if snap else None, delta=delta,
                               is_demo=e.is_demo))
    out.sort(key=lambda w: (-len(w.changes), -(w.score or 0)))
    return out[:limit]


def new_entity_summaries(session: Session, *, include_demo: bool, limit: int = 10
                         ) -> list[CompanySummary | PersonCard]:
    ne = new_entities(session, clock.today(), days=NEW_DAYS)
    cids = [uuid.UUID(r["id"]) for r in ne["companies"] if include_demo or not r["is_demo"]]
    pids = [uuid.UUID(r["id"]) for r in ne["people"] if include_demo or not r["is_demo"]]
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(cids)))}
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(pids)))}
    out: list[CompanySummary | PersonCard] = []
    out += P.company_summaries(session, [companies[i] for i in cids if i in companies])
    out += P.person_cards(session, [people[i] for i in pids if i in people])
    return out[:limit]


def daily_radar(session: Session, *, since: datetime | None = None, include_demo: bool | None = None) -> DailyRadar:
    include_demo = get_settings().demo_mode if include_demo is None else include_demo
    since = resolve_since(session, since)
    people = candidate_people(session, include_demo=include_demo, floor=0)
    companies = candidate_companies(session, include_demo=include_demo)
    f_changed = _changed(session, people, since, use_7d=False)[:5]
    c_changed = _changed(session, companies, since, use_7d=True)[:5]
    founders = P.founder_intel_cards(session, [e for e, _k, _a in f_changed])
    company_cards = P.company_cards(session, [e for e, _k, _a in c_changed])
    papers = recent_papers(session, include_demo=include_demo, days=max(14, (clock.now() - since).days + 1))
    strange = strange_signals(session, include_demo=include_demo, limit=3)
    items: list[dict[str, Any]] = []
    for card, (_e, _k, a) in zip(company_cards, c_changed, strict=True):
        bits = []
        if card.delta_7d and card.delta_7d > 0:
            bits.append(f"ANTI SCORE +{card.delta_7d:g} over 7 days to {card.score}")
        if a.get("count"):
            bits.append(_plural(a["count"], "new signal"))
        items.append({"text": f"{card.name}: {'; '.join(bits)}.",
                      "evidence": [str(s.id) for s in a.get("signals", [])[:4]]})
    for card, (_e, _k, a) in zip(founders, f_changed, strict=True):
        n = a.get("count", 0)
        items.append({"text": f"{card.name} (founder score {card.score})"
                              + (f": {_plural(n, 'new signal')}." if n else " is moving up."),
                      "evidence": [str(s.id) for s in a.get("signals", [])[:4]]})
    for s in strange:
        items.append({"text": s.headline, "evidence": [e.signal_id for e in s.evidence if e.signal_id]})
    items = [{**i, "n": n} for n, i in enumerate(items[:6], 1)]
    return DailyRadar(
        date=clock.today(), since=since, founders=founders, companies=company_cards, papers=papers,
        strange_signals=strange, watchlist_changes=watchlist_changes(session, include_demo=include_demo),
        new_entities=new_entity_summaries(session, include_demo=include_demo),
        summary=Narrative.model_validate(narratives.morning_brief_summary(items)),
    )

