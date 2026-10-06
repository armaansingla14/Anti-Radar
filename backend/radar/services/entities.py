"""Entity services: company / founder lists and detail pages, signals, reports.

Every function takes a Session and returns API models from ``radar.schemas.api``.
Provenance rules for displayed facts (``FactField.status``):

* ``VERIFIED`` — the value is backed by a ``FieldProvenance`` row with
  epistemic FACT (or directly observed provider data such as open job postings
  or a measured metric), and the source URL is attached when known.
* ``INFERRED`` — the value is derived (stage estimate, team size from links) or
  present on the record without field-level provenance.
* ``UNKNOWN`` — we have no value.

Narratives (why now / why Anti / risks) come from ``radar.llm.narratives``,
which always labels its generator and falls back to deterministic templates.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Select, and_, case, false, func, not_, null, or_, select, union
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import hiring_summary
from radar.llm import narratives
from radar.models import (
    Company,
    CompanyMetric,
    CompanyPerson,
    EntityTopic,
    FieldProvenance,
    Job,
    MatchCandidate,
    MetricSnapshot,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    ResearchReport,
    ScoreSnapshot,
    Signal,
    SignalEntity,
    Topic,
)
from radar.schemas.api import (
    AntiScore,
    CompanyDetail,
    CompanySummary,
    FactField,
    Narrative,
    PaperCard,
    PersonCard,
    PersonDetail,
    RepoCard,
    ReportOut,
    ScorePoint,
    SignalOut,
    SourceRef,
    TimelineEvent,
    TimeSeries,
    Verification,
)
from radar.scoring.extract import weekly_sum, window_delta
from radar.scoring.features import FORMATION_SIGNAL_TYPES
from radar.services import presenters as P
from radar.thesis.loader import get_thesis

TIMELINE_LIMIT = 40
SERIES_DAYS = 90
SOURCES_LIMIT = 30

CompanySort = Literal["score", "delta", "attention_gap", "recent", "stars"]
FounderSort = Literal["score", "delta", "recent", "followers"]


SeriesSource = Literal["observed", "backfill", "demo", "mixed"]


class InvalidParam(ValueError):
    """A query parameter failed semantic validation (mapped to HTTP 422)."""


# ── shared query helpers ─────────────────────────────────────────────────────


THEME_ALIASES = {"frontier_infra": "frontier_infrastructure", "frontier": "frontier_infrastructure",
                 "semis": "semiconductors", "semiconductor": "semiconductors", "infra": "frontier_infrastructure"}


def validate_theme(theme: str | None) -> str | None:
    """Accept a thesis theme key ('robotics') or topic key ('robotics.humanoids')."""
    if not theme:
        return None
    theme = THEME_ALIASES.get(theme.strip().lower(), theme.strip().lower())
    th, _, sub = theme.partition(".")
    themes = get_thesis().themes
    if th not in themes or (sub and sub not in themes[th].subcategories):
        raise InvalidParam(f"Unknown thesis theme: {theme!r}. Valid themes: {', '.join(sorted(themes))}")
    return theme


def theme_entity_ids(theme: str) -> Select:
    """Subquery of entity ids tagged with a theme (or exact topic key)."""
    q = select(EntityTopic.entity_id).join(Topic, Topic.id == EntityTopic.topic_id)
    return q.where(Topic.key == theme) if "." in theme else q.where(Topic.theme == theme)


def latest_score_subq(entity_type: str, day: date, *, min_day: date | None = None):
    """(entity_id, score, attention) of the latest snapshot on/before ``day`` per entity."""
    conds = [ScoreSnapshot.entity_type == entity_type, ScoreSnapshot.day <= day]
    if min_day is not None:
        conds.append(ScoreSnapshot.day >= min_day)
    last = (
        select(ScoreSnapshot.entity_id, func.max(ScoreSnapshot.day).label("d"))
        .where(*conds).group_by(ScoreSnapshot.entity_id).subquery()
    )
    return (
        select(ScoreSnapshot.entity_id.label("entity_id"), ScoreSnapshot.score.label("score"),
               ScoreSnapshot.attention.label("attention"), ScoreSnapshot.day.label("day"),
               ScoreSnapshot.version.label("version"))
        .join(last, and_(ScoreSnapshot.entity_id == last.c.entity_id, ScoreSnapshot.day == last.c.d))
        .subquery()
    )


def score_delta_sql(cur, past):
    """SQL twin of presenters.comparable_baseline: cur.score − past.score for two latest_score_subq
    rows, NULL when a live snapshot would be diffed against reconstructed history (unless the live
    row carries attention.reconstructed_score) or when the score versions differ."""
    cur_rec = func.coalesce(cur.c.attention["reconstructed"].as_boolean(), false())
    past_rec = func.coalesce(past.c.attention["reconstructed"].as_boolean(), false())
    base = case((and_(past_rec, not_(cur_rec)), cur.c.attention["reconstructed_score"].as_float()),
                else_=cur.c.score)
    return case((cur.c.version != past.c.version, null()), else_=base - past.c.score)


def related_signal_rows(session: Session, ids: Iterable[uuid.UUID], since: datetime | None = None,
                        until: datetime | None = None, *, include_repos: bool = True):
    """(owner_id, signal_id) pairs for signals directly on, linked to (SignalEntity), or — for
    companies — recorded on the company's repositories. One UNION query."""
    ids = list(set(ids))
    if not ids:
        return []

    def window(q):
        if since is not None:
            q = q.where(Signal.event_at >= since)
        if until is not None:
            q = q.where(Signal.event_at <= until)
        return q

    parts = [
        window(select(Signal.entity_id.label("owner"), Signal.id.label("sid")).where(Signal.entity_id.in_(ids))),
        window(select(SignalEntity.entity_id.label("owner"), Signal.id.label("sid"))
               .join(Signal, Signal.id == SignalEntity.signal_id).where(SignalEntity.entity_id.in_(ids))),
    ]
    if include_repos:
        parts.append(window(select(Repository.company_id.label("owner"), Signal.id.label("sid"))
                            .join(Signal, Signal.entity_id == Repository.id)
                            .where(Repository.company_id.in_(ids))))
    return session.execute(union(*parts)).all()


def signal_activity(session: Session, ids: Iterable[uuid.UUID], since: datetime,
                    until: datetime | None = None) -> dict[uuid.UUID, dict[str, Any]]:
    """{entity_id: {count, latest, signals: [Signal newest first], by_type: Counter}} (batched)."""
    until = until or clock.now() + timedelta(days=1)
    rows = related_signal_rows(session, ids, since, until)
    if not rows:
        return {}
    sigs = {s.id: s for s in session.scalars(select(Signal).where(Signal.id.in_({r.sid for r in rows})))}
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for owner, sid in rows:
        s = sigs.get(sid)
        if s is None:
            continue
        a = out.setdefault(owner, {"count": 0, "latest": None, "signals": [], "by_type": Counter()})
        a["count"] += 1
        a["signals"].append(s)
        a["by_type"][s.signal_type] += 1
        if a["latest"] is None or s.event_at > a["latest"]:
            a["latest"] = s.event_at
    for a in out.values():
        a["signals"].sort(key=lambda s: s.event_at, reverse=True)
    return out


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# ── cards shared by feeds and details ────────────────────────────────────────


def repo_cards(session: Session, repos: Sequence[Repository]) -> list[RepoCard]:
    """RepoCards with 30-day star gain and 4-week commit totals from MetricSnapshot (one query)."""
    if not repos:
        return []
    ids = [r.id for r in repos]
    today = clock.today()
    rows = session.execute(
        select(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value)
        .where(MetricSnapshot.entity_id.in_(ids), MetricSnapshot.metric.in_(("stars", "commits_week")),
               MetricSnapshot.day >= today - timedelta(days=45), MetricSnapshot.day <= today)
        .order_by(MetricSnapshot.day)
    )
    series: dict[tuple[uuid.UUID, str], list[tuple[date, float]]] = defaultdict(list)
    for eid, metric, d, v in rows:
        series[(eid, metric)].append((d, v))
    out = []
    for r in repos:
        created = r.gh_created_at.date() if r.gh_created_at else None
        stars_pts = series.get((r.id, "stars"), [])
        s30 = window_delta(stars_pts, today, 0, 30, created) if stars_pts else None
        c4 = weekly_sum(series.get((r.id, "commits_week"), []), today, range(4), created)
        out.append(RepoCard(
            id=str(r.id), full_name=r.full_name, description=r.description, language=r.language,
            stars=r.stars or 0, forks=r.forks or 0, stars_30d=s30, commits_4w=c4,
            contributors=r.contributors_count, gh_created_at=r.gh_created_at,
            url=gh_url(r.full_name, r.is_demo), is_demo=r.is_demo,
        ))
    return out


def _code_url_keys(urls: Iterable[str]) -> set[str]:
    """'https://github.com/Owner/Name(.git)/...' -> 'owner/name'."""
    keys = set()
    for u in urls or []:
        low = u.lower().rstrip("/")
        for host in ("github.com/", "github.example/"):
            if host in low:
                parts = low.split(host, 1)[1].split("/")
                if len(parts) >= 2 and parts[0] and parts[1]:
                    keys.add(f"{parts[0]}/{parts[1].removesuffix('.git')}")
    return keys


def paper_links(session: Session, papers: Sequence[ResearchPaper]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    """Linked entities per paper: company (company_id), code repositories' companies, authors. Batched."""
    if not papers:
        return {}
    pids = [p.id for p in papers]
    out: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    seen: dict[uuid.UUID, set[str]] = defaultdict(set)

    def add(pid: uuid.UUID, ref: dict[str, Any]) -> None:
        if ref["id"] not in seen[pid]:
            seen[pid].add(ref["id"])
            out[pid].append(ref)

    keys = {p.id: _code_url_keys(p.code_urls) for p in papers}
    all_keys = set().union(*keys.values()) if keys else set()
    repos = list(session.scalars(select(Repository).where(func.lower(Repository.full_name).in_(all_keys)))) \
        if all_keys else []
    repo_by_key = {r.full_name.lower(): r for r in repos}
    cids = {p.company_id for p in papers if p.company_id} | {r.company_id for r in repos if r.company_id}
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(cids)))} if cids else {}
    for p in papers:
        cands = [p.company_id] + [repo_by_key[k].company_id for k in keys[p.id] if k in repo_by_key]
        for cid in cands:
            if cid and cid in companies:
                c = companies[cid]
                add(p.id, {"type": "company", "id": str(c.id), "slug": c.slug, "name": c.name, "is_demo": c.is_demo})
        for k in keys[p.id]:
            r = repo_by_key.get(k)
            if r:
                add(p.id, {"type": "repository", "id": str(r.id), "slug": r.full_name, "name": r.full_name,
                           "is_demo": r.is_demo})
    for pid, person in session.execute(
        select(PaperAuthor.paper_id, Person).join(Person, Person.id == PaperAuthor.person_id)
        .where(PaperAuthor.paper_id.in_(pids)).order_by(PaperAuthor.position)
    ).tuples():
        add(pid, {"type": "person", "id": str(person.id), "slug": person.slug, "name": person.name,
                  "is_demo": person.is_demo})
    return out



def gh_url(path: str, is_demo: bool) -> str:
    """GitHub URL for a handle or owner/name — fictional entities only ever get the reserved .example host."""
    return f"https://{'github.example' if is_demo else 'github.com'}/{path}"


def x_url(handle: str, is_demo: bool) -> str:
    return f"https://{'x.example' if is_demo else 'x.com'}/{handle.lstrip('@')}"


def demo_status(status: Verification, is_demo: bool) -> Verification:
    """Fictional data is never VERIFIED."""
    return "INFERRED" if is_demo and status == "VERIFIED" else status


def paper_cards(session: Session, papers: Sequence[ResearchPaper], *, scores: dict[uuid.UUID, int] | None = None,
                why: dict[uuid.UUID, Any] | None = None) -> list[PaperCard]:
    links = paper_links(session, papers)
    out = []
    for p in papers:
        out.append(PaperCard(
            id=str(p.id), arxiv_id=p.arxiv_id, title=p.title, abstract=p.abstract,
            authors=[a.get("name", "") for a in (p.authors or []) if isinstance(a, dict)],
            categories=list(p.categories or []), published_at=p.published_at, url=p.url,
            code_urls=list(p.code_urls or []), linked_entities=links.get(p.id, []),
            **_why_fields((why or {}).get(p.id)), score=(scores or {}).get(p.id), is_demo=p.is_demo,
        ))
    return out


def _why_fields(w: Any) -> dict[str, Any]:
    """Accepts a Narrative-shaped dict (preferred) or plain text (treated as template INFERENCE)."""
    if not w:
        return {}
    if isinstance(w, str):
        return {"why_it_matters": w, "why_it_matters_epistemic": "INFERENCE", "why_it_matters_generator": "template"}
    return {"why_it_matters": w.get("text"), "why_it_matters_epistemic": w.get("epistemic"),
            "why_it_matters_generator": w.get("generator")}


def rank_papers(session: Session, papers: Sequence[ResearchPaper]) -> dict[uuid.UUID, int]:
    """Deterministic 0..100 research relevance:

    40 × (max linked-entity score / 100)  — papers tied to strong companies/people
    + 20 if code is released
    + 30 × (best thesis-topic match score for the paper, 0..1, from EntityTopic)
    + 10 × min(author recurrence, 3) / 3 — authors with other papers on the radar.
    """
    if not papers:
        return {}
    pids = [p.id for p in papers]
    links = paper_links(session, papers)
    linked_ids = {_uuid(e["id"]) for refs in links.values() for e in refs if e["type"] in ("company", "person")}
    snaps = P.latest_scores(session, [i for i in linked_ids if i])
    fit = dict(session.execute(
        select(EntityTopic.entity_id, func.max(EntityTopic.score)).where(EntityTopic.entity_id.in_(pids))
        .group_by(EntityTopic.entity_id)).tuples().all())
    authors = defaultdict(set)
    for pid, person_id in session.execute(
            select(PaperAuthor.paper_id, PaperAuthor.person_id).where(PaperAuthor.paper_id.in_(pids))):
        authors[pid].add(person_id)
    all_authors = set().union(*authors.values()) if authors else set()
    paper_counts = dict(session.execute(
        select(PaperAuthor.person_id, func.count()).where(PaperAuthor.person_id.in_(all_authors))
        .group_by(PaperAuthor.person_id)).tuples().all()) if all_authors else {}
    out = {}
    for p in papers:
        linked = [snaps[i] for e in links.get(p.id, []) if (i := _uuid(e["id"])) in snaps]
        recurrence = max((paper_counts.get(a, 1) - 1 for a in authors.get(p.id, ())), default=0)
        score = (40 * (max(linked, default=0) / 100) + (20 if p.code_urls else 0)
                 + 30 * min(float(fit.get(p.id) or 0), 1.0) + 10 * min(recurrence, 3) / 3)
        out[p.id] = round(score)
    return out


def entity_refs(session: Session, ids_by_type: dict[str, set[uuid.UUID]]) -> dict[uuid.UUID, dict[str, Any]]:
    """{id: {type, id, slug, name, is_demo}} for companies, people, repositories, papers (one query per type)."""
    out: dict[uuid.UUID, dict[str, Any]] = {}
    spec: dict[str, tuple[Any, Any, Any]] = {
        "company": (Company, Company.slug, Company.name),
        "person": (Person, Person.slug, Person.name),
        "repository": (Repository, Repository.full_name, Repository.full_name),
        "paper": (ResearchPaper, ResearchPaper.arxiv_id, ResearchPaper.title),
    }
    for et, ids in ids_by_type.items():
        if et not in spec or not ids:
            continue
        model, slug_col, name_col = spec[et]
        for eid, slug, name, demo in session.execute(
                select(model.id, slug_col, name_col, model.is_demo).where(model.id.in_(list(ids)))):
            out[eid] = {"type": et, "id": str(eid), "slug": slug, "name": name, "is_demo": demo}
    return out


def signal_outs(session: Session, signals: Sequence[Signal]) -> list[SignalOut]:
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for s in signals:
        by_type[s.entity_type].add(s.entity_id)
    refs = entity_refs(session, by_type)
    out = []
    for s in signals:
        ref = refs.get(s.entity_id, {})
        out.append(SignalOut(
            id=str(s.id), entity_id=str(s.entity_id), entity_type=s.entity_type, entity_name=ref.get("name"),
            entity_slug=ref.get("slug"), signal_type=s.signal_type, source_type=s.source_type,
            source_url=s.source_url, title=s.title, description=s.description, raw_value=s.raw_value,
            normalized_value=s.normalized_value, event_at=s.event_at, detected_at=s.detected_at,
            confidence=s.confidence, epistemic=s.epistemic if s.epistemic in ("FACT", "INFERENCE", "MODEL_OPINION")
            else "FACT", is_demo=s.is_demo, metadata=s.meta or {},
        ))
    return out


def timeline_events(signals: Sequence[Signal]) -> list[TimelineEvent]:
    return [TimelineEvent(
        day=s.event_at.date(), title=s.title, detail=s.description, source_type=s.source_type,
        source_url=s.source_url, signal_type=s.signal_type,
        epistemic=s.epistemic if s.epistemic in ("FACT", "INFERENCE", "MODEL_OPINION") else "FACT",
        is_demo=s.is_demo,
    ) for s in signals]


def _timeline_signals(session: Session, ids: Iterable[uuid.UUID], limit: int = TIMELINE_LIMIT) -> list[Signal]:
    rows = related_signal_rows(session, ids, include_repos=False)
    sids = {r.sid for r in rows}
    if not sids:
        return []
    return list(session.scalars(select(Signal).where(Signal.id.in_(sids))
                                .order_by(Signal.event_at.desc(), Signal.id).limit(limit)))


def _sources(prov: Sequence[FieldProvenance], signals: Sequence[Signal], extra: Iterable[tuple[str, str, str]] = ()
             ) -> list[SourceRef]:
    """Distinct source URLs behind displayed facts and timeline signals."""
    seen: dict[str, SourceRef] = {}

    def add(url: str | None, title: str | None, st: str | None, at: datetime | None = None) -> None:
        if not url or url in seen or len(seen) >= SOURCES_LIMIT:
            return
        seen[url] = SourceRef(id=f"S{len(seen) + 1}", url=url, title=title, source_type=st, fetched_at=at)

    for f in prov:
        add(f.source_url, f"{f.field.replace('_', ' ').capitalize()} source", f.source_type, f.last_updated)
    for url, title, st in extra:
        add(url, title, st)
    for s in signals:
        add(s.source_url, s.title, s.source_type, s.detected_at)
    return list(seen.values())


def _provenance(session: Session, entity_id: uuid.UUID) -> list[FieldProvenance]:
    return list(session.scalars(select(FieldProvenance).where(FieldProvenance.entity_id == entity_id)
                                .order_by(FieldProvenance.confidence.desc(), FieldProvenance.last_updated.desc())))


def _fact(label: str, value: Any, prov: dict[str, FieldProvenance], *fields: str,
          missing_note: str | None = None, unverified_note: str = "On record; no field-level source captured"
          ) -> FactField:
    if value in (None, "", []):
        return FactField(label=label, value=None, status="UNKNOWN", note=missing_note)
    for f in fields:
        row = prov.get(f)
        if row is not None:
            status: Verification = "VERIFIED" if row.epistemic == "FACT" else "INFERRED"
            return FactField(label=label, value=value, status=status, source_url=row.source_url or None,
                             source_type=row.source_type)
    return FactField(label=label, value=value, status="INFERRED", note=unverified_note)


def _narr(d: dict[str, Any] | None) -> Narrative | None:
    return Narrative.model_validate(d) if d else None


def _metric_series(session: Session, ids: Sequence[uuid.UUID], metric: str, label: str, *,
                   cumulative: bool, days: int = SERIES_DAYS, note_by_source: dict[str, str] | None = None
                   ) -> TimeSeries | None:
    """Daily sum over entities. Cumulative metrics carry forward the last known value per entity."""
    if not ids:
        return None
    today = clock.today()
    lo = today - timedelta(days=days)
    rows = session.execute(
        select(MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value, MetricSnapshot.source)
        .where(MetricSnapshot.entity_id.in_(list(ids)), MetricSnapshot.metric == metric,
               MetricSnapshot.day >= lo - timedelta(days=30 if cumulative else 0), MetricSnapshot.day <= today)
        .order_by(MetricSnapshot.day)
    ).all()
    if not rows:
        return None
    sources = {src or "observed" for *_x, src in rows}
    per: dict[uuid.UUID, dict[date, float]] = defaultdict(dict)
    for eid, d, v, _src in rows:
        per[eid][d] = v
    points: list[dict[str, Any]] = []
    if cumulative:
        first = min(d for s in per.values() for d in s)
        start = max(lo, first)
        last: dict[uuid.UUID, float] = {}
        early = {eid: [d for d in sorted(s) if d <= start] for eid, s in per.items()}
        for eid, ds in early.items():
            if ds:
                last[eid] = per[eid][ds[-1]]
        d = start
        while d <= today:
            for eid, s in per.items():
                if d in s:
                    last[eid] = s[d]
            points.append({"day": d.isoformat(), "value": round(sum(last.values()), 2)})
            d += timedelta(days=1)
    else:
        agg: dict[date, float] = defaultdict(float)
        for eid, s in per.items():
            for d, v in s.items():
                if d >= lo:
                    agg[d] += v
        points = [{"day": d.isoformat(), "value": round(v, 2)} for d, v in sorted(agg.items())]
    if not points:
        return None
    one = next(iter(sources)) if len(sources) == 1 else "mixed"
    src: SeriesSource = one if one in ("observed", "backfill", "demo", "mixed") else "observed"  # type: ignore[assignment]
    notes = note_by_source or {}
    note = notes.get(src) or {
        "observed": "Daily observations recorded by ANTI RADAR",
        "backfill": "Reconstructed from provider history",
        "demo": "Synthetic series for a fictional demo entity",
        "mixed": "Provider history backfill joined with daily observations",
    }[src]
    return TimeSeries(metric=metric, label=label, points=points, source=src, note=note)


# ── companies ────────────────────────────────────────────────────────────────


def list_companies(
    session: Session, *, q: str | None = None, theme: str | None = None, stage: str | None = None,
    kind: str | None = None, sort: CompanySort = "score", include_demo: bool = True, limit: int = 50,
    offset: int = 0, with_total: bool = False,
) -> list[CompanySummary] | tuple[list[CompanySummary], int]:
    theme = validate_theme(theme)
    today = clock.today()
    cur = latest_score_subq("company", today)
    stmt = select(Company).outerjoin(cur, cur.c.entity_id == Company.id).where(Company.dismissed.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Company.name.ilike(like), Company.description.ilike(like), Company.slug.ilike(like)))
    if theme:
        stmt = stmt.where(Company.id.in_(theme_entity_ids(theme)))
    if stage:
        stmt = stmt.where(Company.stage == stage)
    if kind:
        stmt = stmt.where(Company.kind == kind)
    if not include_demo:
        stmt = stmt.where(Company.is_demo.is_(False))
    total = session.scalar(select(func.count()).select_from(stmt.with_only_columns(Company.id).subquery())) \
        if with_total else None
    if sort == "delta":
        past = latest_score_subq("company", today - timedelta(days=7), min_day=today - timedelta(days=10))
        stmt = stmt.outerjoin(past, past.c.entity_id == Company.id).order_by(
            score_delta_sql(cur, past).desc().nulls_last(), cur.c.score.desc().nulls_last())
    elif sort == "attention_gap":
        stmt = stmt.order_by(cur.c.attention["gap"].as_float().desc().nulls_last(), cur.c.score.desc().nulls_last())
    elif sort == "recent":
        stmt = stmt.order_by(Company.first_seen_at.desc())
    elif sort == "stars":
        stmt = stmt.outerjoin(CompanyMetric, CompanyMetric.company_id == Company.id).order_by(
            CompanyMetric.metrics["stars_30d"].as_float().desc().nulls_last(), cur.c.score.desc().nulls_last())
    else:
        stmt = stmt.order_by(cur.c.score.desc().nulls_last())
    stmt = stmt.order_by(Company.name).offset(offset).limit(limit)
    items = P.company_summaries(session, list(session.scalars(stmt)))
    return (items, int(total or 0)) if with_total else items


def _company_repos(session: Session, c: Company) -> list[Repository]:
    return list(session.scalars(select(Repository).where(Repository.company_id == c.id)
                                .order_by(Repository.stars.desc())))


def _company_people(session: Session, c: Company) -> list[tuple[CompanyPerson, Person]]:
    return list(session.execute(
        select(CompanyPerson, Person).join(Person, Person.id == CompanyPerson.person_id)
        .where(CompanyPerson.company_id == c.id)
        .order_by(CompanyPerson.is_founder.desc(), CompanyPerson.confidence.desc(), Person.name)
    ).tuples())


def _company_papers(session: Session, c: Company, repos: Sequence[Repository], person_ids: Sequence[uuid.UUID]
                    ) -> list[ResearchPaper]:
    conds = [ResearchPaper.company_id == c.id]
    urls = [gh_url(r.full_name, r.is_demo) for r in repos]
    if urls:
        conds.append(ResearchPaper.code_urls.overlap(urls))
    if person_ids:
        conds.append(ResearchPaper.id.in_(select(PaperAuthor.paper_id).where(PaperAuthor.person_id.in_(person_ids))))
    return list(session.scalars(select(ResearchPaper).where(or_(*conds))
                                .order_by(ResearchPaper.published_at.desc().nulls_last()).limit(20)))


def _company_facts(session: Session, c: Company, prov_rows: Sequence[FieldProvenance], team: int,
                   hiring: dict[str, Any], jobs: Sequence[Job]) -> list[FactField]:
    prov: dict[str, FieldProvenance] = {}
    for r in prov_rows:
        prov.setdefault(r.field, r)
    facts = [
        _fact("Founded", c.founded_date.isoformat() if c.founded_date else None, prov, "founded_date", "founded",
              unverified_note="Earliest observed public activity (e.g. GitHub organization creation)",
              missing_note="No founding date found in public sources"),
        _fact("Website", c.website, prov, "website", "domain", missing_note="No website found"),
        _fact("Location", c.location, prov, "location", missing_note="Location not disclosed"),
    ]
    if c.stage and c.stage != "unknown":
        facts.append(FactField(label="Stage estimate", value=c.stage.replace("_", "-"), status="INFERRED",
                               source_type="system", note=c.stage_basis or "Estimated from public signals"))
    else:
        facts.append(FactField(label="Stage estimate", value=None, status="UNKNOWN",
                               note=c.stage_basis or "Not enough public evidence to estimate stage"))
    gh = _fact("GitHub org", c.github_org, prov, "github_org", missing_note="No GitHub organization linked")
    if gh.status != "UNKNOWN" and not gh.source_url:
        gh.source_url = gh_url(c.github_org, c.is_demo)
    facts.append(gh)
    x = _fact("X handle", f"@{c.x_handle.lstrip('@')}" if c.x_handle else None, prov, "x_handle",
              missing_note="No X account linked")
    if x.status != "UNKNOWN" and not x.source_url and c.x_handle:
        x.source_url = x_url(c.x_handle, c.is_demo)
    facts.append(x)
    facts.append(FactField(label="Team size known", value=team if team else None,
                           status="INFERRED" if team else "UNKNOWN",
                           note=(f"{team} people linked from public sources (lower bound)" if team
                                 else "No team members identified yet")))
    if jobs:
        url = next((j.url for j in jobs if j.url), None)
        facts.append(FactField(label="Open roles", value=hiring.get("open_roles", 0),
                               status=demo_status("VERIFIED", c.is_demo),
                               source_url=url, source_type="jobs",
                               note=f"{hiring.get('technical_roles', 0)} technical; observed on the company job board"))
    else:
        facts.append(FactField(label="Open roles", value=None, status="UNKNOWN", note="No job board observed"))
    return facts


def company_detail(session: Session, id_or_slug: str) -> CompanyDetail | None:
    c = P.resolve_entity(session, Company, id_or_slug)
    if c is None:
        return None
    today = clock.today()
    summary = P.company_summaries(session, [c])[0]
    snap = P.latest_snapshots(session, [c.id]).get(c.id)
    repos = _company_repos(session, c)
    links = _company_people(session, c)
    person_ids = [p.id for _cp, p in links]
    papers = _company_papers(session, c, repos, person_ids)

    # founders: this company's link decides role / founder flag
    cards = {card.id: card for card in P.person_cards(session, [p for _cp, p in links])}
    founders = []
    for cp, p in links:
        role = "Contributor" if (cp.confidence or 0) < 1 else cp.role
        founders.append(cards[str(p.id)].model_copy(update={"role": role, "is_founder": bool(cp.is_founder)}))

    jobs = list(session.scalars(select(Job).where(Job.company_id == c.id, Job.closed_at.is_(None))
                                .order_by(Job.posted_at.desc().nulls_last(), Job.first_seen_at.desc())))
    hiring = hiring_summary(session, c.id, today)
    prov_rows = _provenance(session, c.id)
    timeline_ids = [c.id] + [r.id for r in repos] + [pp.id for pp in papers] + person_ids
    tl_signals = _timeline_signals(session, timeline_ids)

    repo_ids = [r.id for r in repos]
    star_notes = {"backfill": "Star history from GitHub star-history API",
                  "mixed": "Star history from GitHub star-history API plus daily observations"}
    series = [s for s in (
        _metric_series(session, repo_ids, "stars", "GitHub stars (all repositories)", cumulative=True,
                       note_by_source=star_notes),
        _metric_series(session, repo_ids, "commits_week", "Commits per week", cumulative=False,
                       note_by_source={"backfill": "Weekly commit activity from the GitHub stats API"}),
        _metric_series(session, repo_ids, "contributors", "Contributors", cumulative=True),
        _metric_series(session, repo_ids, "forks", "Forks", cumulative=True),
    ) if s is not None]

    reports = [{"id": str(r.id), "kind": r.kind, "title": r.title, "created_at": r.created_at,
                "generator": r.generator, "status": r.status}
               for r in session.scalars(select(ResearchReport).where(ResearchReport.entity_id == c.id)
                                        .order_by(ResearchReport.created_at.desc()).limit(20))]
    job_sources = [(j.url, f"Job posting: {j.title}", "jobs") for j in jobs if j.url][:5]
    return CompanyDetail(
        summary=summary, description=c.description,
        facts=_company_facts(session, c, prov_rows, len(links), hiring, jobs),
        anti_score=P.anti_score(session, snap),
        score_history=[ScorePoint(day=r["day"], score=r["score"]) for r in P.score_history(session, c.id)],
        why_now=_narr(narratives.why_now(session, c)), why_anti=_narr(narratives.why_anti(session, c)),
        risks=[Narrative.model_validate(r) for r in narratives.risks_questions(session, c)],
        timeline=timeline_events(tl_signals), founders=founders, repositories=repo_cards(session, repos),
        papers=paper_cards(session, papers),
        jobs=[{"id": str(j.id), "title": j.title, "department": j.department, "role_class": j.role_class,
               "location": j.location, "url": j.url, "posted_at": j.posted_at, "first_seen_at": j.first_seen_at,
               "ats": j.ats, "is_demo": j.is_demo} for j in jobs],
        hiring=hiring, series=series, sources=_sources(prov_rows, tl_signals, job_sources), reports=reports,
        match_candidates=match_candidates(session, "company", c.id), watched=summary.watched,
        data_note=P.data_note(c.is_demo),
    )


def match_candidates(session: Session, entity_type: str, entity_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = list(session.scalars(select(MatchCandidate).where(
        MatchCandidate.status == "pending", MatchCandidate.entity_type == entity_type,
        or_(MatchCandidate.a_id == entity_id, MatchCandidate.b_id == entity_id)).order_by(
        MatchCandidate.confidence.desc()).limit(20)))
    others = {r.b_id if r.a_id == entity_id else r.a_id for r in rows}
    refs = entity_refs(session, {entity_type: others})
    out = []
    for r in rows:
        oid = r.b_id if r.a_id == entity_id else r.a_id
        ref = refs.get(oid, {})
        out.append({"id": str(r.id), "other_id": str(oid), "other_name": ref.get("name"),
                    "other_slug": ref.get("slug"), "method": r.method, "confidence": r.confidence,
                    "evidence": r.evidence or {}, "status": r.status})
    return out


def entity_score(session: Session, model: type[Company | Person], id_or_slug: str) -> AntiScore | None:
    e = P.resolve_entity(session, model, id_or_slug)
    if e is None:
        return None
    return P.anti_score(session, P.latest_snapshots(session, [e.id]).get(e.id))


# ── founders ─────────────────────────────────────────────────────────────────


# A bio/headline that names an organization ("@vllm-project", "PhD Candidate @ HKUST", "CTO at X",
# "Founder of Y") contradicts "no company": the empty current_company field is only missing data.
ORG_MENTION_RE = r"@\s*[A-Za-z0-9]|\m(at|of)\s+[A-Z0-9]"
FORMATION_TARGET_DAYS = 120  # same lookback as the scorer's signal window


def formation_target_conditions() -> list:
    """Pre-company / formation-target filter. A missing company field is an UNKNOWN ("no company on
    record"), not a claim, so a person only qualifies with a *positive* signal: a recent
    formation-type signal about them (affiliation/bio change, formation language), no company on
    record, and no organization named in their own bio or headline."""
    lo = clock.now() - timedelta(days=FORMATION_TARGET_DAYS)
    headline = Person.attrs["headline"].as_string()
    return [
        Person.current_company.is_(None),
        or_(Person.bio.is_(None), not_(Person.bio.op("~")(ORG_MENTION_RE))),
        or_(headline.is_(None), not_(headline.op("~")(ORG_MENTION_RE))),
        select(Signal.id).where(Signal.entity_id == Person.id, Signal.signal_type.in_(FORMATION_SIGNAL_TYPES),
                                Signal.event_at >= lo, Signal.event_at <= clock.now()).exists(),
    ]


def list_founders(
    session: Session, *, q: str | None = None, theme: str | None = None, no_company: bool = False,
    include_demo: bool = True, sort: FounderSort = "score", limit: int = 50, offset: int = 0,
    with_total: bool = False,
) -> list[PersonCard] | tuple[list[PersonCard], int]:
    theme = validate_theme(theme)
    today = clock.today()
    cur = latest_score_subq("person", today)
    stmt = select(Person).outerjoin(cur, cur.c.entity_id == Person.id).where(Person.dismissed.is_(False))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Person.name.ilike(like), Person.bio.ilike(like), Person.slug.ilike(like),
                              Person.github_handle.ilike(like), Person.current_company.ilike(like)))
    if theme:
        th = theme.partition(".")[0]
        stmt = stmt.where(or_(Person.id.in_(theme_entity_ids(theme)), Person.domains.any(th)))
    if no_company:
        stmt = stmt.where(*formation_target_conditions())
    if not include_demo:
        stmt = stmt.where(Person.is_demo.is_(False))
    total = session.scalar(select(func.count()).select_from(stmt.with_only_columns(Person.id).subquery())) \
        if with_total else None
    if sort == "delta":
        past = latest_score_subq("person", today - timedelta(days=7), min_day=today - timedelta(days=10))
        stmt = stmt.outerjoin(past, past.c.entity_id == Person.id).order_by(
            score_delta_sql(cur, past).desc().nulls_last(), cur.c.score.desc().nulls_last())
    elif sort == "recent":
        stmt = stmt.order_by(Person.first_seen_at.desc())
    elif sort == "followers":
        stmt = stmt.order_by(Person.followers.desc().nulls_last())
    else:
        stmt = stmt.order_by(cur.c.score.desc().nulls_last())
    stmt = stmt.order_by(Person.name).offset(offset).limit(limit)
    items = P.person_cards(session, list(session.scalars(stmt)))
    return (items, int(total or 0)) if with_total else items


def _person_facts(session: Session, p: Person, prov_rows: Sequence[FieldProvenance]) -> list[FactField]:
    prov: dict[str, FieldProvenance] = {}
    for r in prov_rows:
        prov.setdefault(r.field, r)
    facts = [
        _fact("Current company", p.current_company, prov, "current_company",
              missing_note="No current company listed"),
        _fact("Current role", p.current_role, prov, "current_role", missing_note="Role not disclosed"),
        _fact("Prior affiliations", list(p.prior_affiliations or []) or None, prov, "prior_affiliations",
              unverified_note="Derived from profile, publications and affiliation-change signals",
              missing_note="No prior affiliations found"),
    ]
    gh = _fact("GitHub", p.github_handle, prov, "github_handle", missing_note="No GitHub account linked")
    if gh.status != "UNKNOWN" and not gh.source_url:
        gh.source_url = gh_url(p.github_handle, p.is_demo)
    facts.append(gh)
    x = _fact("X", f"@{p.x_handle.lstrip('@')}" if p.x_handle else None, prov, "x_handle",
              missing_note="No X account linked")
    if x.status != "UNKNOWN" and not x.source_url and p.x_handle:
        x.source_url = x_url(p.x_handle, p.is_demo)
    facts.append(x)
    facts.append(_fact("Personal site", p.personal_site, prov, "personal_site", missing_note="No personal site"))
    facts.append(_fact("Location", p.location, prov, "location", missing_note="Location not disclosed"))
    m = session.execute(
        select(MetricSnapshot.value, MetricSnapshot.day, MetricSnapshot.source)
        .where(MetricSnapshot.entity_id == p.id, MetricSnapshot.metric == "followers",
               MetricSnapshot.day <= clock.today())
        .order_by(MetricSnapshot.day.desc()).limit(1)).first()
    if m is not None:
        facts.append(FactField(label="Followers", value=int(m.value), status=demo_status("VERIFIED", p.is_demo),
                               source_type=m.source,
                               source_url=gh_url(p.github_handle, p.is_demo) if p.github_handle else None,
                               note=f"Measured {m.day.isoformat()}"))
    else:
        facts.append(_fact("Followers", p.followers, prov, "followers", missing_note="Follower count unknown"))
    return facts


def _collaborators(session: Session, p: Person, limit: int = 8) -> list[PersonCard]:
    repo_ids = select(RepoContributor.repository_id).where(RepoContributor.person_id == p.id)
    paper_ids = select(PaperAuthor.paper_id).where(PaperAuthor.person_id == p.id)
    overlap: Counter[uuid.UUID] = Counter()
    for pid, n in session.execute(
            select(RepoContributor.person_id, func.count()).where(
                RepoContributor.repository_id.in_(repo_ids), RepoContributor.person_id != p.id)
            .group_by(RepoContributor.person_id)):
        overlap[pid] += n
    for pid, n in session.execute(
            select(PaperAuthor.person_id, func.count()).where(
                PaperAuthor.paper_id.in_(paper_ids), PaperAuthor.person_id != p.id)
            .group_by(PaperAuthor.person_id)):
        overlap[pid] += n
    if not overlap:
        return []
    people = {x.id: x for x in session.scalars(select(Person).where(Person.id.in_(list(overlap))))}
    ranked = sorted(people.values(), key=lambda x: (-overlap[x.id], x.name))[:limit]
    return P.person_cards(session, ranked)


def person_detail(session: Session, id_or_slug: str) -> PersonDetail | None:
    p = P.resolve_entity(session, Person, id_or_slug)
    if p is None:
        return None
    card = P.person_cards(session, [p])[0]
    snap = P.latest_snapshots(session, [p.id]).get(p.id)
    companies = list(session.scalars(
        select(Company).join(CompanyPerson, CompanyPerson.company_id == Company.id)
        .where(CompanyPerson.person_id == p.id).order_by(CompanyPerson.is_founder.desc(), Company.name)))
    repo_q = select(Repository).where(or_(
        Repository.id.in_(select(RepoContributor.repository_id).where(RepoContributor.person_id == p.id)),
        and_(Repository.owner_type == "User", func.lower(Repository.owner) == (p.github_handle or "").lower())
        if p.github_handle else false(),
    )).order_by(Repository.stars.desc()).limit(20)
    repos = list(session.scalars(repo_q))
    papers = list(session.scalars(
        select(ResearchPaper).join(PaperAuthor, PaperAuthor.paper_id == ResearchPaper.id)
        .where(PaperAuthor.person_id == p.id).order_by(ResearchPaper.published_at.desc().nulls_last()).limit(20)))
    prov_rows = _provenance(session, p.id)
    tl = _timeline_signals(session, [p.id])
    extra = [(gh_url(p.github_handle, p.is_demo), "GitHub profile", "github")] if p.github_handle else []
    return PersonDetail(
        card=card, bio=p.bio, facts=_person_facts(session, p, prov_rows), founder_score=P.anti_score(session, snap),
        score_history=[ScorePoint(day=r["day"], score=r["score"]) for r in P.score_history(session, p.id)],
        why_now=_narr(narratives.why_now(session, p)), timeline=timeline_events(tl),
        companies=P.company_summaries(session, companies), repositories=repo_cards(session, repos),
        papers=paper_cards(session, papers), collaborators=_collaborators(session, p),
        sources=_sources(prov_rows, tl, extra), watched=p.id in P.watched_ids(session),
        data_note=P.data_note(p.is_demo),
    )


# ── signals ──────────────────────────────────────────────────────────────────


def list_signals(
    session: Session, *, entity_id: str | None = None, entity_type: str | None = None,
    signal_type: str | None = None, source_type: str | None = None, since: datetime | None = None,
    include_demo: bool = True, limit: int = 100,
) -> list[SignalOut]:
    stmt = select(Signal)
    if entity_id:
        eid = _uuid(entity_id)
        if eid is None:
            raise InvalidParam("entity_id must be a UUID")
        linked = select(SignalEntity.signal_id).where(SignalEntity.entity_id == eid)
        stmt = stmt.where(or_(Signal.entity_id == eid, Signal.id.in_(linked)))
    if entity_type:
        stmt = stmt.where(Signal.entity_type == entity_type)
    if signal_type:
        stmt = stmt.where(Signal.signal_type == signal_type)
    if source_type:
        stmt = stmt.where(Signal.source_type == source_type)
    if since:
        stmt = stmt.where(Signal.event_at >= since)
    if not include_demo:
        stmt = stmt.where(Signal.is_demo.is_(False))
    stmt = stmt.order_by(Signal.event_at.desc(), Signal.id).limit(limit)
    return signal_outs(session, list(session.scalars(stmt)))


# ── reports ──────────────────────────────────────────────────────────────────


def _report_slug(session: Session, r: ResearchReport) -> str | None:
    if r.entity_id is None:
        return None
    model: Any = {"company": Company, "person": Person}.get(r.entity_type or "")
    return session.scalar(select(model.slug).where(model.id == r.entity_id)) if model else None


def report_out(session: Session, r: ResearchReport, *, light: bool = False) -> ReportOut:
    return ReportOut(
        id=str(r.id), kind=r.kind, title=r.title, entity_type=r.entity_type,
        entity_id=str(r.entity_id) if r.entity_id else None, entity_slug=_report_slug(session, r),
        body={} if light else (r.body or {}), markdown="" if light else (r.markdown or ""),
        sources=[] if light else (r.sources or []), model=r.model, generator=r.generator, status=r.status,
        cost_usd=r.cost_usd or 0.0, created_at=r.created_at, is_demo=r.is_demo,
    )


def get_report(session: Session, report_id: str) -> ResearchReport | None:
    rid = _uuid(report_id)
    return session.get(ResearchReport, rid) if rid else None


def list_reports(session: Session, *, entity_id: str | None = None, kind: str | None = None,
                 limit: int = 50) -> list[ReportOut]:
    stmt = select(ResearchReport)
    if entity_id:
        eid = _uuid(entity_id)
        if eid is None:
            raise InvalidParam("entity_id must be a UUID")
        stmt = stmt.where(ResearchReport.entity_id == eid)
    if kind:
        stmt = stmt.where(ResearchReport.kind == kind)
    rows = list(session.scalars(stmt.order_by(ResearchReport.created_at.desc()).limit(limit)))
    return [report_out(session, r, light=True) for r in rows]


def report_filename(session: Session, r: ResearchReport) -> str:
    import re

    slug = _report_slug(session, r) or "radar"
    slug = re.sub(r"[^a-z0-9\-]+", "-", slug.lower()).strip("-") or "radar"
    kind = {"memo": "memo", "brief": "brief", "morning_brief": "morning-brief"}.get(r.kind, "report")
    day = (r.created_at or clock.now()).date().isoformat()
    return f"anti-{kind}-{slug}-{day}.md"


def resolve(session: Session, model: type[Company | Person], id_or_slug: str) -> Company | Person | None:
    return P.resolve_entity(session, model, id_or_slug)
