"""Data age: how old is what we are showing?

Two views:

* **Per entity** (`data_age`) — when each *source family* last produced an observation about a
  company or person, plus the row's ``updated_at``. Families:

  ========== ==================================================================
  github     signals with source_type=github, observed (not backfilled) metric
             snapshots on the entity or — for companies — its repositories; for people
             also their contributed repositories' observed metrics / GitHub signals and
             the recorded GitHub profile / commit-history checks (Person.attrs)
  research   arxiv signals; papers first seen (authored / linked to company)
  hiring     jobs signals; job postings first seen
  web        website signals; company web pages last fetched
  community  hackernews signals
  social     x signals
  ========== ==================================================================

  Signal observation time is ``detected_at`` (stamped on the data clock at ingest), never later
  than the data clock's "now". Computed in one grouped query per table, for any number of ids.

* **Global** — last successful ingestion per real provider (`provider_freshness`) and the date
  the data is *as of* (`feed_as_of`): the pinned snapshot day when serving the verified snapshot,
  else today.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
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
    WebPage,
)
from radar.schemas.api import DataAge
from radar.settings import get_settings

FAMILIES = ("github", "research", "hiring", "web", "community", "social")
SOURCE_FAMILY = {"github": "github", "arxiv": "research", "jobs": "hiring", "website": "web",
                 "hackernews": "community", "x": "social"}
# Provider runs that are not data sources: the snapshot loader and the fictional demo seeder.
PSEUDO_PROVIDERS = frozenset({"snapshot", "demo"})

EntityType = Literal["company", "person"]


# Person.attrs timestamps written when GitHub was queried about the person (providers/github.py).
PERSON_GITHUB_CHECKS = ("talent_pool_observed_at", "activity_checked_at", "github_orgs_checked_at")


def _parse_dt(v: object) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _at(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=UTC)


def _bump(out: dict[uuid.UUID, dict[str, datetime | None]], eid: uuid.UUID, fam: str, at: datetime | None) -> None:
    if at is None:
        return
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    cur = out[eid].get(fam)
    if cur is None or at > cur:
        out[eid][fam] = at


def family_last_observed(session: Session, entity_type: EntityType, ids: Iterable[uuid.UUID]
                         ) -> dict[uuid.UUID, dict[str, datetime | None]]:
    """{entity_id: {family: last_observed_at | None}} for every requested id (batched)."""
    ids = list(set(ids))
    out: dict[uuid.UUID, dict[str, datetime | None]] = defaultdict(lambda: dict.fromkeys(FAMILIES))
    if not ids:
        return {}
    now, today = clock.now(), clock.today()

    # Signals: on the entity, linked to it, or (companies) on its repositories.
    parts = [
        select(Signal.entity_id.label("owner"), Signal.id.label("sid")).where(Signal.entity_id.in_(ids)),
        select(SignalEntity.entity_id.label("owner"), SignalEntity.signal_id.label("sid"))
        .where(SignalEntity.entity_id.in_(ids)),
    ]
    if entity_type == "company":
        parts.append(select(Repository.company_id.label("owner"), Signal.id.label("sid"))
                     .join(Signal, Signal.entity_id == Repository.id).where(Repository.company_id.in_(ids)))
    links = union_all(*parts).subquery()
    rows = session.execute(
        select(links.c.owner, Signal.source_type, func.max(Signal.detected_at))
        .join(Signal, Signal.id == links.c.sid)
        .where(Signal.source_type.in_(list(SOURCE_FAMILY)), Signal.detected_at <= now)
        .group_by(links.c.owner, Signal.source_type)
    )
    for owner, st, at in rows:
        _bump(out, owner, SOURCE_FAMILY[st], at)

    # Observed (not reconstructed) metric snapshots → github.
    if entity_type == "company":
        ms = session.execute(
            select(Repository.company_id, func.max(MetricSnapshot.day))
            .join(MetricSnapshot, MetricSnapshot.entity_id == Repository.id)
            .where(Repository.company_id.in_(ids), MetricSnapshot.source == "observed", MetricSnapshot.day <= today)
            .group_by(Repository.company_id))
    else:
        ms = session.execute(
            select(MetricSnapshot.entity_id, func.max(MetricSnapshot.day))
            .where(MetricSnapshot.entity_id.in_(ids), MetricSnapshot.source == "observed", MetricSnapshot.day <= today)
            .group_by(MetricSnapshot.entity_id))
    for eid, d in ms:
        if d is not None:
            _bump(out, eid, "github", _at(d))
    if entity_type == "person":
        # Key repositories (the person is a recorded contributor): observed metric snapshots and GitHub
        # signals on those repos are GitHub observations of the person's work.
        for pid, d in session.execute(
                select(RepoContributor.person_id, func.max(MetricSnapshot.day))
                .join(MetricSnapshot, MetricSnapshot.entity_id == RepoContributor.repository_id)
                .where(RepoContributor.person_id.in_(ids), MetricSnapshot.source == "observed",
                       MetricSnapshot.day <= today)
                .group_by(RepoContributor.person_id)):
            if d is not None:
                _bump(out, pid, "github", _at(d))
        for pid, at in session.execute(
                select(RepoContributor.person_id, func.max(Signal.detected_at))
                .join(Signal, Signal.entity_id == RepoContributor.repository_id)
                .where(RepoContributor.person_id.in_(ids), Signal.source_type == "github", Signal.detected_at <= now)
                .group_by(RepoContributor.person_id)):
            _bump(out, pid, "github", at)
        # Direct GitHub profile / commit-history checks recorded on the person (talent pool, activity check).
        for pid, attrs in session.execute(select(Person.id, Person.attrs).where(Person.id.in_(ids))):
            for key in PERSON_GITHUB_CHECKS:
                at = _parse_dt((attrs or {}).get(key))
                if at is not None and at <= now:
                    _bump(out, pid, "github", at)

    if entity_type == "company":
        for cid, at in session.execute(
                select(WebPage.company_id, func.max(WebPage.last_fetched_at))
                .where(WebPage.company_id.in_(ids), WebPage.last_fetched_at <= now).group_by(WebPage.company_id)):
            _bump(out, cid, "web", at)
        for cid, at in session.execute(
                select(Job.company_id, func.max(Job.first_seen_at))
                .where(Job.company_id.in_(ids), Job.first_seen_at <= now).group_by(Job.company_id)):
            _bump(out, cid, "hiring", at)
        for cid, at in session.execute(
                select(ResearchPaper.company_id, func.max(ResearchPaper.first_seen_at))
                .where(ResearchPaper.company_id.in_(ids), ResearchPaper.first_seen_at <= now)
                .group_by(ResearchPaper.company_id)):
            _bump(out, cid, "research", at)
    else:
        for pid, at in session.execute(
                select(PaperAuthor.person_id, func.max(ResearchPaper.first_seen_at))
                .join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
                .where(PaperAuthor.person_id.in_(ids), ResearchPaper.first_seen_at <= now)
                .group_by(PaperAuthor.person_id)):
            _bump(out, pid, "research", at)
    return {i: dict(out[i]) for i in ids}


def age_days(at: datetime | None) -> int | None:
    """Whole days between an observation and the data clock's today (never negative)."""
    if at is None:
        return None
    return max((clock.today() - at.date()).days, 0)


def data_age(session: Session, entity_type: EntityType, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, DataAge]:
    """Full per-entity data age: row ``updated_at`` plus last observation per source family."""
    ids = list(set(ids))
    if not ids:
        return {}
    model = Company if entity_type == "company" else Person
    updated = dict(session.execute(select(model.id, model.updated_at).where(model.id.in_(ids))).tuples().all())
    fams = family_last_observed(session, entity_type, ids)
    out = {}
    for i in ids:
        f = fams.get(i) or dict.fromkeys(FAMILIES)
        seen = [v for v in f.values() if v is not None]
        last = max(seen) if seen else None
        out[i] = DataAge(updated_at=updated.get(i), last_observed_at=last, age_days=age_days(last), families=f)
    return out


def light_data_age(entity) -> DataAge:
    """Card-weight data age: the row's updated_at only (no queries)."""
    return DataAge(updated_at=getattr(entity, "updated_at", None))


# ── global ───────────────────────────────────────────────────────────────────


def provider_freshness(session: Session) -> dict[str, datetime]:
    """Last successful (ok/partial) ingestion per real provider. Snapshot-replayed runs keep their
    original capture timestamps; the snapshot loader and fictional seeder are not providers."""
    rows = session.execute(
        select(IngestionRun.provider, func.max(IngestionRun.finished_at))
        .where(IngestionRun.status.in_(("ok", "partial")), IngestionRun.finished_at.is_not(None),
               IngestionRun.provider.not_in(list(PSEUDO_PROVIDERS)))
        .group_by(IngestionRun.provider)
    )
    return {p: d for p, d in rows if d}


def _latest_snapshot_day(session: Session) -> date | None:
    params = session.scalar(select(IngestionRun.params).where(IngestionRun.provider == "snapshot")
                            .order_by(IngestionRun.started_at.desc()).limit(1))
    try:
        return date.fromisoformat((params or {}).get("as_of") or "")
    except ValueError:
        return None


@dataclass(frozen=True)
class AsOf:
    data_as_of: date
    is_snapshot: bool
    snapshot_age_days: int | None  # wall-clock days since the snapshot day; None when live

    @property
    def anchor(self) -> datetime:
        """The data clock's "now" for windows such as "signals in the last 24h": the current time
        when live, the end of the (pinned) data day when serving a snapshot."""
        if clock.today() == self.data_as_of:
            return clock.now()
        return datetime.combine(self.data_as_of, time(18, 0), tzinfo=UTC)


def feed_as_of(session: Session) -> AsOf:
    """What date the served data is as of. A snapshot is served when the clock is pinned to a
    snapshot day, or when DEMO_SNAPSHOT is set (falling back to the last loaded snapshot day)."""
    pinned = clock.as_of()
    day = pinned
    if day is None and get_settings().demo_snapshot:
        day = _latest_snapshot_day(session)
    if day is None:
        return AsOf(data_as_of=clock.today(), is_snapshot=False, snapshot_age_days=None)
    return AsOf(data_as_of=day, is_snapshot=True,
                snapshot_age_days=max((clock.wall_now().date() - day).days, 0))


def snapshot_label(a: AsOf) -> str | None:
    """'Snapshot collected Oct 6, 2026 · 14 days old' (None when live)."""
    if not a.is_snapshot:
        return None
    n = a.snapshot_age_days or 0
    age = "today" if n == 0 else f"{n} day{'s' if n != 1 else ''} old"
    return f"Snapshot collected {a.data_as_of:%b %-d, %Y} · {age}"
