"""Stage 1: collect every raw change about tracked companies (portfolio companies and their comps).

A raw change is one dated observation already in the database:

* signals on the company or its repositories (company posts, federal awards, releases, repository creation,
  acceleration, Hacker News stories, hiring state, formation language, website and README changes);
* ``affiliation_changed`` of people linked to the company as founders or leaders;
* papers linked to the company (``ResearchPaper.company_id``), dated by publication;
* job postings dated by the board's posting date (or, without one, first seen in a crawl *after* the first crawl,
  so a first crawl never looks like a hiring burst) and job closures;
* jumps in the observed ``open_roles`` metric between two crawls.

Every raw change in the review window is counted for the noise funnel. Changes in the ``horizon`` before the
window are returned with ``in_window=False``: they only inform novelty ("first launch in 90 days").
``job_posted`` signals are skipped because the job rows themselves are the raw changes (no double count).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, union_all
from sqlalchemy.orm import Session

from radar.models import Company, CompanyPerson, Job, MetricSnapshot, Person, Repository, ResearchPaper, Signal, Source
from radar.portfolio.types import RawChange, SourceRef
from radar.providers.domains import registrable_domain

WINDOW_DAYS = 30
HORIZON_DAYS = 90
SKIP_SIGNAL_TYPES = frozenset({"job_posted"})
FAMILY = {"website": "web", "usaspending": "commercial", "github": "github", "arxiv": "research",
          "hackernews": "community", "jobs": "hiring", "x": "social"}
LEADER_ROLE_RE = re.compile(r"\b(founder|co-?founder|ceo|cto|coo|cfo|chief|president|vp|vice president|head of)\b",
                            re.IGNORECASE)
OPEN_ROLES_JUMP_MIN = 5
OPEN_ROLES_JUMP_RATIO = 1.5


def window_bounds(day: date, window_days: int = WINDOW_DAYS) -> tuple[datetime, datetime]:
    end = datetime.combine(day, time.max, tzinfo=UTC)
    return end - timedelta(days=window_days), end


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def publisher_of(source_type: str, url: str | None, company: Company) -> str:
    """company (own site, own repositories, own job board) | registry (USAspending, arXiv) | third_party."""
    if source_type in ("usaspending", "arxiv"):
        return "registry"
    if source_type in ("jobs", "github"):
        return "company"  # job rows and repositories linked to the company are its own
    if source_type == "website":
        dom = registrable_domain(url) if url else None
        return "company" if dom and dom in own_domains(company) else "third_party"
    return "third_party"


def own_domains(company: Company) -> set[str]:
    """The company's own registrable domains: its domain and website, the domains its crawled pages resolved to
    (redirects such as cognition.ai -> cognition.com) and the portfolio config's website / newsroom domains."""
    attrs = company.attrs or {}
    urls = [company.website, *((attrs.get("pages") or {}).values() if isinstance(attrs.get("pages"), dict) else [])]
    out = {d for d in (registrable_domain(u) for u in urls if isinstance(u, str) and u) if d}
    if company.domain:
        out.add(company.domain.lower())
    out.update(str(d).lower() for d in attrs.get("portfolio_domains") or [])
    return out


def collect(session: Session, company_ids: Iterable[uuid.UUID], day: date, *, window_days: int = WINDOW_DAYS,
            horizon_days: int = HORIZON_DAYS) -> list[RawChange]:
    ids = sorted(set(company_ids), key=str)
    if not ids:
        return []
    start, end = window_bounds(day, window_days)
    horizon = start - timedelta(days=horizon_days)
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(ids)))}
    out: list[RawChange] = []
    out += _signals(session, companies, horizon, start, end)
    out += _people(session, companies, horizon, start, end)
    out += _papers(session, companies, horizon, start, end)
    out += _jobs(session, companies, horizon, start, end)
    out += _metric_jumps(session, companies, start, end)
    out.sort(key=lambda r: (r.event_at, str(r.company_id), r.signal_type, r.title, r.source.url or ""))
    return out


def _signals(session: Session, companies: dict[uuid.UUID, Company], horizon: datetime, start: datetime,
             end: datetime) -> list[RawChange]:
    ids = list(companies)
    links = union_all(
        select(Signal.entity_id.label("owner"), Signal.id.label("sid")).where(Signal.entity_id.in_(ids)),
        select(Repository.company_id.label("owner"), Signal.id.label("sid"))
        .join(Signal, Signal.entity_id == Repository.id).where(Repository.company_id.in_(ids)),
    ).subquery()
    rows = session.execute(
        select(links.c.owner, Signal).join(Signal, Signal.id == links.c.sid)
        .where(Signal.event_at >= horizon, Signal.event_at <= end, Signal.detected_at <= end,
               Signal.signal_type.not_in(list(SKIP_SIGNAL_TYPES)))
    ).all()
    repos = {r.id: r for r in session.scalars(select(Repository).where(Repository.company_id.in_(ids)))}
    page_titles = _page_titles(session, [s for _, s in rows if s.signal_type == "website_changed"])
    out: list[RawChange] = []
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for owner, sig in rows:
        if (owner, sig.id) in seen:
            continue
        seen.add((owner, sig.id))
        company = companies[owner]
        repo = repos.get(sig.entity_id)
        meta = dict(sig.meta or {})
        if repo is not None:
            meta.setdefault("repo", repo.full_name)
            meta["repo_stars"] = repo.stars or 0
            meta["repo_archived"] = bool(repo.archived)
        if sig.signal_type == "website_changed":
            meta.update(page_titles.get(sig.id, {}))
        at = _aware(sig.event_at)
        out.append(RawChange(
            company_id=owner, kind="signal", signal_type=sig.signal_type, title=sig.title or "",
            event_at=at, description=sig.description, confidence=float(sig.confidence or 1.0),
            epistemic=sig.epistemic or "FACT", raw_value=sig.raw_value, meta=meta, in_window=at >= start,
            source=SourceRef(url=sig.source_url, title=sig.title or "", source_type=sig.source_type,
                             family=FAMILY.get(sig.source_type, sig.source_type),
                             publisher=publisher_of(sig.source_type, sig.source_url, company),
                             observed_at=_aware(sig.detected_at), ref=f"signal:{sig.id}",
                             snippet=(sig.description or "")[:300] or None),
        ))
    return out


def _page_titles(session: Session, sigs: list[Signal]) -> dict[uuid.UUID, dict[str, str | None]]:
    """Old / new page title and opening text for website_changed signals (from the stored Source versions)."""
    out: dict[uuid.UUID, dict[str, str | None]] = {}
    wanted = {(s.meta or {}).get("url") for s in sigs}
    if not wanted - {None}:
        return out
    by = {(r.url, r.content_hash): r for r in session.scalars(select(Source).where(Source.url.in_(
        [u for u in wanted if u])))}
    for s in sigs:
        m = s.meta or {}
        old, new = by.get((m.get("url"), m.get("old_hash"))), by.get((m.get("url"), m.get("new_hash")))
        out[s.id] = {"old_title": old.title if old else None, "new_title": new.title if new else None,
                     "old_snippet": (old.snippet or "")[:300] if old else None,
                     "new_snippet": (new.snippet or "")[:300] if new else None}
    return out


def _people(session: Session, companies: dict[uuid.UUID, Company], horizon: datetime, start: datetime,
            end: datetime) -> list[RawChange]:
    """Affiliation changes of founders / leaders linked to the company (verified links only)."""
    links = session.execute(select(CompanyPerson, Person).join(Person, Person.id == CompanyPerson.person_id)
                            .where(CompanyPerson.company_id.in_(list(companies)))).all()
    by_person: dict[uuid.UUID, list[tuple[CompanyPerson, Person]]] = defaultdict(list)
    for cp, p in links:
        if cp.is_founder or LEADER_ROLE_RE.search(cp.role or ""):
            by_person[p.id].append((cp, p))
    if not by_person:
        return []
    out: list[RawChange] = []
    for sig in session.scalars(select(Signal).where(
            Signal.entity_id.in_(list(by_person)), Signal.signal_type == "affiliation_changed",
            Signal.event_at >= horizon, Signal.event_at <= end, Signal.detected_at <= end)):
        for cp, p in by_person[sig.entity_id]:
            company = companies[cp.company_id]
            at = _aware(sig.event_at)
            out.append(RawChange(
                company_id=company.id, kind="signal", signal_type="affiliation_changed", title=sig.title or "",
                event_at=at, description=sig.description, confidence=float(sig.confidence or 1.0),
                epistemic=sig.epistemic or "FACT",
                meta={**(sig.meta or {}), "person": p.name, "person_slug": p.slug, "role": cp.role,
                      "is_founder": cp.is_founder, "company_name": company.name},
                in_window=at >= start,
                source=SourceRef(url=sig.source_url, title=sig.title or "", source_type=sig.source_type,
                                 family=FAMILY.get(sig.source_type, sig.source_type), publisher="third_party",
                                 observed_at=_aware(sig.detected_at), ref=f"signal:{sig.id}"),
            ))
    return out


def _papers(session: Session, companies: dict[uuid.UUID, Company], horizon: datetime, start: datetime,
            end: datetime) -> list[RawChange]:
    out: list[RawChange] = []
    for paper in session.scalars(select(ResearchPaper).where(
            ResearchPaper.company_id.in_(list(companies)), ResearchPaper.published_at >= horizon,
            ResearchPaper.published_at <= end, ResearchPaper.first_seen_at <= end)):
        if (paper.attrs or {}).get("stub"):
            continue
        at = _aware(paper.published_at)
        url = paper.url or f"https://arxiv.org/abs/{paper.arxiv_id}"
        out.append(RawChange(
            company_id=paper.company_id, kind="paper", signal_type="paper_published", title=paper.title,
            event_at=at, description=(paper.abstract or "")[:500] or None, meta={"arxiv_id": paper.arxiv_id},
            in_window=at >= start,
            source=SourceRef(url=url, title=paper.title, source_type="arxiv", family="research",
                             publisher="registry", observed_at=_aware(paper.first_seen_at), ref=f"paper:{paper.id}",
                             snippet=(paper.abstract or "")[:300] or None),
        ))
    return out


def _jobs(session: Session, companies: dict[uuid.UUID, Company], horizon: datetime, start: datetime,
          end: datetime) -> list[RawChange]:
    out: list[RawChange] = []
    for job in session.scalars(select(Job).where(Job.company_id.in_(list(companies)))):
        company = companies[job.company_id]
        first_crawl = _parse((company.attrs or {}).get("jobs_first_crawled_at"))
        posted = _aware(job.posted_at)
        basis = "posted_at"
        if posted is None:
            seen = _aware(job.first_seen_at)
            # Without a board date, only a posting discovered after the first crawl is known to be new.
            if seen is not None and first_crawl is not None and seen > first_crawl + timedelta(days=1):
                posted, basis = seen, "first_seen_after_first_crawl"
        src = SourceRef(url=job.url, title=f"Job posting: {job.title}", source_type="jobs", family="hiring",
                        publisher="company", observed_at=_aware(job.first_seen_at), ref=f"job:{job.id}")
        meta = {"role_class": job.role_class, "ats": job.ats, "external_id": job.external_id,
                "department": job.department, "location": job.location, "date_basis": basis}
        if posted is not None and horizon <= posted <= end and _aware(job.first_seen_at) <= end:
            out.append(RawChange(company_id=company.id, kind="job_posted", signal_type="job_posted",
                                 title=job.title, event_at=posted, meta=meta, in_window=posted >= start, source=src))
        closed = _aware(job.closed_at)
        if closed is not None and horizon <= closed <= end:
            out.append(RawChange(company_id=company.id, kind="job_closed", signal_type="job_closed",
                                 title=job.title, event_at=closed, meta={**meta, "date_basis": "closed_at"},
                                 in_window=closed >= start, source=src))
    return out


def _metric_jumps(session: Session, companies: dict[uuid.UUID, Company], start: datetime, end: datetime
                  ) -> list[RawChange]:
    """open_roles jumps between two consecutive observed crawls inside the window (a hiring acceleration)."""
    rows = session.execute(select(MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value)
                           .where(MetricSnapshot.entity_id.in_(list(companies)), MetricSnapshot.metric == "open_roles",
                                  MetricSnapshot.source == "observed", MetricSnapshot.day <= end.date())
                           .order_by(MetricSnapshot.entity_id, MetricSnapshot.day)).all()
    series: dict[uuid.UUID, list[tuple[date, float]]] = defaultdict(list)
    for eid, d, v in rows:
        series[eid].append((d, float(v)))
    out: list[RawChange] = []
    for eid, pts in series.items():
        for (d0, v0), (d1, v1) in zip(pts, pts[1:], strict=False):
            at = datetime.combine(d1, time(12), tzinfo=UTC)
            if at < start or v1 - v0 < OPEN_ROLES_JUMP_MIN or v1 < OPEN_ROLES_JUMP_RATIO * max(v0, 1.0):
                continue
            company = companies[eid]
            out.append(RawChange(
                company_id=eid, kind="metric_jump", signal_type="open_roles_jump",
                title=f"Open roles on the public job board rose from {v0:.0f} to {v1:.0f}", event_at=at,
                epistemic="FACT", raw_value=v1, meta={"from": v0, "to": v1, "from_day": d0.isoformat(),
                                                      "to_day": d1.isoformat()},
                source=SourceRef(url=(company.attrs or {}).get("pages", {}).get("careers") if isinstance(
                    (company.attrs or {}).get("pages"), dict) else None,
                    title=f"{company.name} public job board", source_type="jobs", family="hiring",
                    publisher="company", observed_at=at, ref=f"metric:open_roles:{eid}:{d1.isoformat()}"),
            ))
    return out


def _parse(v: object) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return _aware(datetime.fromisoformat(v.replace("Z", "+00:00")))
    except ValueError:
        return None
