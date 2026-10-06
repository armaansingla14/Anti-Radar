"""Watchlist: a single default user + default watchlist (internal single-analyst tool).

"Changes since last visit" are deterministic: new signals (by ``detected_at``) and new
jobs (by ``first_seen_at``) since each item's ``last_seen_at``, plus the score and
GitHub-star change between the last visit and today. Nothing is generated.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import is_technical_class
from radar.models import (
    Company,
    CompanyPerson,
    EntityTopic,
    Job,
    MetricSnapshot,
    Person,
    Repository,
    ScoreSnapshot,
    Signal,
    Topic,
    User,
    UserAction,
    Watchlist,
    WatchlistItem,
)
from radar.schemas.api import WatchItemOut, WatchlistOut, WatchRequest
from radar.services.presenters import latest_snapshots, resolve_entity, score_deltas, snapshot_delta

DEFAULT_USER = "Default analyst"
DEFAULT_WATCHLIST = "My watchlist"
SCORE_CHANGE_MIN = 3.0
STARS_CHANGE_MIN_PCT = 5.0
PAPER_TYPES = {"paper_published", "paper_with_code", "author_recurrence"}
X_TYPES_PREFIX = "x_"
HN_TYPES_PREFIX = "hn_"
GITHUB_MOMENTUM_TYPES = {"star_velocity", "star_acceleration", "commit_acceleration", "new_contributors"}
JOB_TYPES = {"job_posted", "hiring_active", "hiring_acceleration"}


class WatchTargetNotFound(LookupError):
    pass


def default_user(session: Session) -> User:
    u = session.scalar(select(User).order_by(User.created_at).limit(1))
    if u is None:
        u = User(name=DEFAULT_USER)
        session.add(u)
        session.flush()
    return u


def default_watchlist(session: Session) -> Watchlist:
    u = default_user(session)
    wl = session.scalar(select(Watchlist).where(Watchlist.user_id == u.id).order_by(Watchlist.created_at).limit(1))
    if wl is None:
        wl = Watchlist(user_id=u.id, name=DEFAULT_WATCHLIST)
        session.add(wl)
        session.flush()
    return wl


def _record(session: Session, action: str, entity_type: str | None, entity_id: uuid.UUID | None, **meta) -> None:
    session.add(UserAction(user_id=default_user(session).id, action=action, entity_type=entity_type,
                           entity_id=entity_id, meta=meta))


# ── resolve targets ──────────────────────────────────────────────────────────


def _resolve_target(session: Session, req: WatchRequest) -> tuple[uuid.UUID, str | None]:
    """-> (entity_id, topic_key). Raises WatchTargetNotFound."""
    if req.entity_type == "topic":
        key = (req.topic_key or "").strip()
        topic = session.scalar(select(Topic).where(Topic.key == key)) if key else None
        if topic is None and req.entity_id:
            try:
                topic = session.get(Topic, uuid.UUID(req.entity_id))
            except ValueError:
                topic = None
        if topic is None:
            raise WatchTargetNotFound(f"unknown topic {key or req.entity_id!r}")
        return topic.id, topic.key
    ref = (req.entity_id or "").strip()
    if not ref:
        raise WatchTargetNotFound("entity_id is required")
    if req.entity_type in ("company", "person"):
        ent = resolve_entity(session, Company if req.entity_type == "company" else Person, ref)
    else:
        ent = None
        try:
            ent = session.get(Repository, uuid.UUID(ref))
        except ValueError:
            ent = session.scalar(select(Repository).where(Repository.full_name.ilike(ref)))
    if ent is None:
        raise WatchTargetNotFound(f"unknown {req.entity_type} {ref!r}")
    return ent.id, None


# ── change detection ─────────────────────────────────────────────────────────


def _plural(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _signal_lines(sigs: list[tuple[Signal, str]]) -> list[str]:
    """sigs: (signal, via) where via is 'self' | 'founder' | 'repo'."""
    lines: list[str] = []
    by_type: dict[str, list[tuple[Signal, str]]] = defaultdict(list)
    for s, via in sigs:
        by_type[s.signal_type].append((s, via))
    papers = [x for t in PAPER_TYPES for x in by_type.pop(t, [])]
    if papers:
        if any(v == "founder" for _, v in papers):
            lines.append("Founder published new paper" if len(papers) == 1 else
                         f"Founders published {len(papers)} new papers")
        else:
            lines.append(f"Published {_plural(len(papers), 'new paper')}")
    for s, _ in by_type.pop("affiliation_changed", [])[:2]:
        lines.append(f"Affiliation change: {s.title}")
    web = by_type.pop("website_changed", [])
    if web:
        if any("product" in (s.title or "").lower() for s, _ in web):
            lines.append("New product page detected")
        else:
            lines.append(f"Website updated ({_plural(len(web), 'change')})")
    for t in sorted(GITHUB_MOMENTUM_TYPES & set(by_type)):
        lines.append(f"GitHub: {by_type.pop(t)[0][0].title}")
    for t in ("release_published", "org_created", "funding_announced", "formation_language"):
        for s, _ in by_type.pop(t, [])[:1]:
            lines.append(s.title)
    xs = [x for t in list(by_type) if t.startswith(X_TYPES_PREFIX) for x in by_type.pop(t)]
    if xs:
        lines.append(f"{_plural(len(xs), 'new X post')}")
    hn = [x for t in list(by_type) if t.startswith(HN_TYPES_PREFIX) for x in by_type.pop(t)]
    if hn:
        lines.append(f"{_plural(len(hn), 'new Hacker News mention')}")
    for t in JOB_TYPES:
        by_type.pop(t, None)  # covered by the jobs table (counted separately)
    for t, items in sorted(by_type.items()):
        lines.append(f"{_plural(len(items), 'new ' + t.replace('_', ' ') + ' signal')}")
    return lines


def _job_lines(classes: list[str | None]) -> list[str]:
    tech = sum(1 for c in classes if is_technical_class(c))
    other = len(classes) - tech
    out = []
    if tech:
        out.append(f"{_plural(tech, 'new engineering role')}")
    if other:
        out.append(f"{_plural(other, 'new non-technical role')}")
    return out


def _stars_at(series: dict[date, float], day: date) -> float | None:
    days = [d for d in series if d <= day]
    return series[max(days)] if days else None


def _changes(session: Session, items: list[WatchlistItem]) -> dict[uuid.UUID, list[str]]:
    if not items:
        return {}
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    since_min = min(i.last_seen_at for i in items)
    ent_items = [i for i in items if i.entity_type != "topic"]
    company_items = [i for i in ent_items if i.entity_type == "company"]
    company_ids = [i.entity_id for i in company_items]

    # owner map: signal entity -> (item entity, via)
    owner: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = defaultdict(list)
    for i in ent_items:
        owner[i.entity_id].append((i.entity_id, "self"))
    repo_owner: dict[uuid.UUID, uuid.UUID] = {i.entity_id: i.entity_id for i in ent_items
                                              if i.entity_type == "repository"}
    if company_ids:
        for rid, cid in session.execute(select(Repository.id, Repository.company_id)
                                        .where(Repository.company_id.in_(company_ids))).tuples():
            owner[rid].append((cid, "repo"))
            repo_owner[rid] = cid
        for pid, cid in session.execute(select(CompanyPerson.person_id, CompanyPerson.company_id).where(
                CompanyPerson.company_id.in_(company_ids), CompanyPerson.is_founder.is_(True))).tuples():
            owner[pid].append((cid, "founder"))
    last_seen = {i.entity_id: i.last_seen_at for i in items}

    per_item: dict[uuid.UUID, list[tuple[Signal, str]]] = defaultdict(list)
    if owner:
        for s in session.scalars(select(Signal).where(Signal.entity_id.in_(list(owner)),
                                                      Signal.detected_at > since_min)
                                 .order_by(Signal.event_at.desc())):
            for eid, via in owner[s.entity_id]:
                if s.detected_at > last_seen[eid]:
                    per_item[eid].append((s, via))
    jobs: dict[uuid.UUID, list[str | None]] = defaultdict(list)
    if company_ids:
        for cid, rc, seen in session.execute(select(Job.company_id, Job.role_class, Job.first_seen_at).where(
                Job.company_id.in_(company_ids), Job.first_seen_at > since_min)).tuples():
            if seen > last_seen[cid]:
                jobs[cid].append(rc)

    # stars: total across a company's repos (or the repo itself) at last visit vs latest
    star_change: dict[uuid.UUID, tuple[float, float]] = {}
    if repo_owner:
        lo = since_min.date() - timedelta(days=14)
        per_repo: dict[uuid.UUID, dict[date, float]] = defaultdict(dict)
        for rid, d, v in session.execute(select(MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value)
                                         .where(MetricSnapshot.entity_id.in_(list(repo_owner)),
                                                MetricSnapshot.metric == "stars", MetricSnapshot.day >= lo)):
            per_repo[rid][d] = float(v)
        for i in ent_items:
            repos = [r for r, o in repo_owner.items() if o == i.entity_id and r in per_repo]
            before = [_stars_at(per_repo[r], i.last_seen_at.date()) for r in repos]
            if any(b is not None for b in before):
                star_change[i.entity_id] = (sum(b or 0 for b in before),
                                            sum(per_repo[r][max(per_repo[r])] for r in repos))

    # score change since last visit
    scored = [i for i in ent_items if i.entity_type in ("company", "person")]
    current = latest_snapshots(session, [i.entity_id for i in scored])
    by_day: dict[date, list[uuid.UUID]] = defaultdict(list)
    for i in scored:
        by_day[i.last_seen_at.date()].append(i.entity_id)
    past: dict[uuid.UUID, ScoreSnapshot] = {}
    for d, ids in by_day.items():
        past.update(latest_snapshots(session, ids, d))

    # topics: new companies tagged since last visit
    topic_items = [i for i in items if i.entity_type == "topic"]
    topic_new: dict[uuid.UUID, int] = Counter()
    if topic_items:
        for tid, first_seen in session.execute(
            select(EntityTopic.topic_id, Company.first_seen_at).join(Company, Company.id == EntityTopic.entity_id)
            .where(EntityTopic.topic_id.in_([i.entity_id for i in topic_items]), EntityTopic.score >= 0.3,
                   Company.first_seen_at > since_min)
        ).tuples():
            if first_seen > last_seen[tid]:
                topic_new[tid] += 1

    for i in items:
        eid = i.entity_id
        lines: list[str] = []
        if i.entity_type == "topic":
            if topic_new.get(eid):
                lines.append(f"{_plural(topic_new[eid], 'new company', 'new companies')} in this thesis area")
            out[eid] = lines
            continue
        lines += _job_lines(jobs.get(eid, []))
        lines += _signal_lines(per_item.get(eid, []))
        if eid in star_change:
            b, n = star_change[eid]
            if n > b > 0 and (n - b) / b * 100 >= STARS_CHANGE_MIN_PCT:
                lines.append(f"GitHub stars +{(n - b) / b * 100:.0f}% since {i.last_seen_at:%b %-d}")
            elif n > b and b == 0:
                lines.append(f"GitHub stars +{int(n):,} since {i.last_seen_at:%b %-d}")
        cur, prev = current.get(eid), past.get(eid)
        d = snapshot_delta(cur, prev)
        if d is not None and abs(d) >= SCORE_CHANGE_MIN:
            lines.append(f"Score {'↑' if d > 0 else '↓'} {abs(d):.0f}")
        out[eid] = lines
    return out


# ── public API ───────────────────────────────────────────────────────────────


def _item_outs(session: Session, items: list[WatchlistItem]) -> list[WatchItemOut]:
    ids_by_type: dict[str, list[uuid.UUID]] = defaultdict(list)
    for i in items:
        ids_by_type[i.entity_type].append(i.entity_id)
    names: dict[uuid.UUID, tuple[str, str | None, bool]] = {}
    if ids_by_type.get("company"):
        for c in session.scalars(select(Company).where(Company.id.in_(ids_by_type["company"]))):
            names[c.id] = (c.name, c.slug, c.is_demo)
    if ids_by_type.get("person"):
        for p in session.scalars(select(Person).where(Person.id.in_(ids_by_type["person"]))):
            names[p.id] = (p.name, p.slug, p.is_demo)
    if ids_by_type.get("repository"):
        for r in session.scalars(select(Repository).where(Repository.id.in_(ids_by_type["repository"]))):
            names[r.id] = (r.full_name, r.full_name, r.is_demo)
    if ids_by_type.get("topic"):
        for t in session.scalars(select(Topic).where(Topic.id.in_(ids_by_type["topic"]))):
            names[t.id] = (t.label, t.key, False)
    snaps = latest_snapshots(session, ids_by_type.get("company", []) + ids_by_type.get("person", []))
    d7 = score_deltas(session, snaps, 7)
    changes = _changes(session, items)
    topic_stats = _topic_stats(session, [i for i in items if i.entity_type == "topic"])
    out = []
    for i in items:
        name, slug, demo = names.get(i.entity_id, ("(removed entity)", None, False))
        snap = snaps.get(i.entity_id)
        score, delta = (round(snap.score) if snap else None), d7.get(i.entity_id)
        item_changes = changes.get(i.entity_id, [])
        if i.entity_type == "topic" and i.entity_id in topic_stats:
            score, delta, moved = topic_stats[i.entity_id]
            if moved:
                item_changes = [*item_changes, moved]
        out.append(WatchItemOut(
            id=str(i.id), entity_type=i.entity_type, entity_id=str(i.entity_id), topic_key=i.topic_key, name=name,
            slug=slug, note=i.note, created_at=i.created_at, last_seen_at=i.last_seen_at,
            score=score, delta_7d=delta, is_demo=demo, changes=item_changes,
        ))
    return out


TOPIC_TOP_N = 5
TOPIC_MOVE_MIN = 5.0


def _topic_stats(session: Session, topic_items: list[WatchlistItem]
                 ) -> dict[uuid.UUID, tuple[int | None, float | None, str | None]]:
    """Topic 'score' = mean ANTI SCORE of its top linked companies; delta (7d) likewise; plus a movement
    line counting leading companies whose score moved ≥ TOPIC_MOVE_MIN *since the item was last seen*
    (so it is a change since last visit, cleared by mark-seen like every other change line)."""
    if not topic_items:
        return {}
    topic_ids = [i.entity_id for i in topic_items]
    links: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for tid, cid in session.execute(
        select(EntityTopic.topic_id, EntityTopic.entity_id)
        .where(EntityTopic.topic_id.in_(topic_ids), EntityTopic.entity_type == "company", EntityTopic.score >= 0.3)
    ).tuples():
        links[tid].append(cid)
    snaps = latest_snapshots(session, [c for cs in links.values() for c in cs])
    d7 = score_deltas(session, snaps, 7)
    out: dict[uuid.UUID, tuple[int | None, float | None, str | None]] = {}
    for item in topic_items:
        tid = item.entity_id
        top = sorted((c for c in links.get(tid, []) if c in snaps), key=lambda c: -snaps[c].score)[:TOPIC_TOP_N]
        if not top:
            out[tid] = (None, None, None)
            continue
        score = round(sum(snaps[c].score for c in top) / len(top))
        deltas = [d for c in top if (d := d7.get(c)) is not None]
        delta = round(sum(deltas) / len(deltas), 1) if deltas else None
        past = latest_snapshots(session, top, item.last_seen_at.date())
        moved = sum(1 for c in top if abs(snapshot_delta(snaps[c], past.get(c)) or 0) >= TOPIC_MOVE_MIN)
        line = (f"{_plural(moved, 'leading company', 'leading companies')} moved ≥{TOPIC_MOVE_MIN:.0f} points "
                f"since {item.last_seen_at:%b %-d}" if moved else None)
        out[tid] = (score, delta, line)
    return out


def get_watchlist(session: Session) -> WatchlistOut:
    wl = default_watchlist(session)
    items = list(session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)
                                 .order_by(WatchlistItem.created_at.desc())))
    outs = _item_outs(session, items)
    return WatchlistOut(name=wl.name, items=outs, changes_since_last_visit=sum(len(o.changes) for o in outs))


def add_item(session: Session, req: WatchRequest) -> WatchItemOut:
    """Idempotent: watching the same entity twice returns the existing item (note updated if given)."""
    entity_id, topic_key = _resolve_target(session, req)
    wl = default_watchlist(session)
    item = session.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id,
                                                      WatchlistItem.entity_id == entity_id))
    if item is None:
        now = clock.now()
        item = WatchlistItem(watchlist_id=wl.id, entity_type=req.entity_type, entity_id=entity_id,
                             topic_key=topic_key, note=(req.note or None), last_seen_at=now, created_at=now)
        session.add(item)
        _record(session, "watch", req.entity_type, entity_id, topic_key=topic_key)
    elif req.note is not None:
        item.note = req.note or None
    session.flush()
    return _item_outs(session, [item])[0]


def remove_item(session: Session, ref: str) -> bool:
    """Delete by watchlist item id OR by watched entity id. Returns False if nothing matched."""
    try:
        uid = uuid.UUID(str(ref))
    except ValueError:
        return False
    wl = default_watchlist(session)
    item = session.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.id == uid))
    if item is None:
        item = session.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id,
                                                          WatchlistItem.entity_id == uid))
    if item is None:
        return False
    _record(session, "unwatch", item.entity_type, item.entity_id, topic_key=item.topic_key)
    session.delete(item)
    session.flush()
    return True


def mark_seen(session: Session, at: datetime | None = None) -> int:
    wl = default_watchlist(session)
    at = at or clock.now()
    items = list(session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id)))
    for i in items:
        i.last_seen_at = at
    u = default_user(session)
    # An explicit "mark all as seen" also closes the Daily "since last visit" window.
    u.last_visit_at = at
    u.prev_visit_at = at
    session.flush()
    return len(items)


# ── feedback ─────────────────────────────────────────────────────────────────


def _feedback_target(session: Session, entity_type: str, ref: str):
    from radar.models import ResearchPaper

    if entity_type in ("company", "person"):
        return resolve_entity(session, Company if entity_type == "company" else Person, ref)
    model = Repository if entity_type == "repository" else ResearchPaper
    try:
        return session.get(model, uuid.UUID(ref))
    except ValueError:
        col = Repository.full_name if model is Repository else ResearchPaper.arxiv_id
        return session.scalar(select(model).where(col == ref))


def submit_feedback(session: Session, req) -> dict:
    """Store UserFeedback (+ context), nudge preference weights, dismiss if asked. Raises WatchTargetNotFound."""
    from radar.models import UserFeedback
    from radar.scoring.feedback import apply_feedback, entity_context, load_prefs

    ent = _feedback_target(session, req.entity_type, req.entity_id.strip())
    if ent is None:
        raise WatchTargetNotFound(f"unknown {req.entity_type} {req.entity_id!r}")
    user = default_user(session)
    ctx = entity_context(session, req.entity_type, ent.id)
    session.add(UserFeedback(user_id=user.id, entity_type=req.entity_type, entity_id=ent.id, label=req.label,
                             note=req.note, context=ctx))
    changed = apply_feedback(session, req.entity_type, ent.id, req.label)
    dismissed = False
    if req.label == "dismiss" and hasattr(ent, "dismissed"):
        ent.dismissed = True
        dismissed = True
    _record(session, "dismiss" if dismissed else "feedback", req.entity_type, ent.id, label=req.label,
            changed=changed)
    session.flush()
    return {"ok": True, "weights": load_prefs(session), "changed": changed, "dismissed": dismissed}
