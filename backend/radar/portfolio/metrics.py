"""Stage 8: public-proxy metrics with comparable windows.

Every value is a *public proxy*, stored in ``metric_snapshots`` (metric ``proxy_*``, source ``derived``) with a
source URL, a confidence and a ``proxy_label`` saying what it is not: open technical roles are a public hiring
signal, never headcount; federal awards are public obligations, never revenue.

Windows are dated by the event itself (posting date, release date, publication date, story date, award start),
so "last 14 days vs the 14 before" is comparable even for a company first observed today, as long as the dates
exist. ``compare_windows`` says when a pair cannot be compared and why (no dated postings, no repository observed,
etc.). Nothing is estimated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from radar.models import Company, Job, MetricSnapshot, Repository, ResearchPaper, Signal
from radar.portfolio.normalize import is_customer_story, semver_level, technical

PROXY_SOURCE = "derived"
LABELS: dict[str, str] = {
    "proxy_open_technical_roles": "Public hiring signal: open technical roles on the public job board (not headcount)",
    "proxy_new_technical_roles_14d": "Public hiring signal: technical roles posted in the last 14 days",
    "proxy_new_technical_roles_prior_14d": "Public hiring signal: technical roles posted in the 14 days before",
    "proxy_releases_30d": "Public code signal: non-prerelease GitHub releases in the last 30 days",
    "proxy_releases_prior_30d": "Public code signal: non-prerelease GitHub releases in the 30 days before",
    "proxy_contributors": "Public code signal: contributors on public repositories (not engineering headcount)",
    "proxy_papers_90d": "Public research signal: linked arXiv papers published in the last 90 days",
    "proxy_hn_points_30d": "Public attention signal: Hacker News points on stories linking the company, last 30 days",
    "proxy_hn_points_prior_30d": "Public attention signal: Hacker News points, the 30 days before",
    "proxy_federal_awards_24m": "Public record: federal awards started in the last 24 months (count, not revenue)",
    "proxy_federal_award_amount_24m": "Public record: federal award obligations in the last 24 months, USD "
                                      "(not revenue)",
    "proxy_customer_stories_30d": "Public customer stories published on the company's site, last 30 days "
                                  "(not revenue, not a customer count)",
}
PAIRS: tuple[tuple[str, str, str, str], ...] = (
    ("new_technical_roles", "proxy_new_technical_roles_14d", "proxy_new_technical_roles_prior_14d", "14 days"),
    ("releases", "proxy_releases_30d", "proxy_releases_prior_30d", "30 days"),
    ("hn_points", "proxy_hn_points_30d", "proxy_hn_points_prior_30d", "30 days"),
)


@dataclass
class Proxy:
    metric: str
    value: float
    source_url: str | None
    confidence: float
    note: str = ""


def _end(day: date) -> datetime:
    return datetime.combine(day, time.max, tzinfo=UTC)


def company_proxies(session: Session, company: Company, day: date) -> tuple[list[Proxy], dict[str, str]]:
    """(proxies, reasons a proxy was not computed)."""
    end = _end(day)
    out: list[Proxy] = []
    missing: dict[str, str] = {}
    attrs = company.attrs or {}
    pages = attrs.get("pages") if isinstance(attrs.get("pages"), dict) else {}
    careers = pages.get("careers") if pages else None

    jobs = list(session.scalars(select(Job).where(Job.company_id == company.id, Job.first_seen_at <= end)))
    if attrs.get("jobs_last_crawled_at") or jobs:
        open_tech = [j for j in jobs if j.closed_at is None and technical(j.role_class)]
        url = careers or next((j.url for j in jobs if j.url), None)
        out.append(Proxy("proxy_open_technical_roles", float(len(open_tech)), url, 0.9))
        dated = [j for j in jobs if j.posted_at is not None and technical(j.role_class)]
        if dated:
            cur = sum(1 for j in dated if end - timedelta(days=14) <= _aware(j.posted_at) <= end)
            prior = sum(1 for j in dated if end - timedelta(days=28) <= _aware(j.posted_at) < end - timedelta(days=14))
            out += [Proxy("proxy_new_technical_roles_14d", float(cur), url, 0.9),
                    Proxy("proxy_new_technical_roles_prior_14d", float(prior), url, 0.9)]
        else:
            missing["new_technical_roles"] = "the job board gives no posting dates"
    else:
        missing["open_technical_roles"] = "no public job board observed"
        missing["new_technical_roles"] = "no public job board observed"

    repos = list(session.scalars(select(Repository).where(Repository.company_id == company.id)))
    if repos:
        rels = session.execute(select(Signal.event_at, Signal.meta).where(
            Signal.entity_id.in_([r.id for r in repos]), Signal.signal_type == "release_published",
            Signal.event_at <= end, Signal.event_at >= end - timedelta(days=60))).all()

        def _count(lo: int, hi: int) -> int:
            n = 0
            for at, meta in rels:
                m = meta or {}
                if m.get("prerelease") or semver_level(str(m.get("tag") or "")) == "prerelease":
                    continue
                t = _aware(at)
                if end - timedelta(days=hi) <= t and (t <= end if lo == 0 else t < end - timedelta(days=lo)):
                    n += 1
            return n

        gh = f"https://github.com/{company.github_org}" if company.github_org else f"https://github.com/{repos[0].owner}"
        out += [Proxy("proxy_releases_30d", float(_count(0, 30)), gh, 1.0),
                Proxy("proxy_releases_prior_30d", float(_count(30, 60)), gh, 1.0)]
        contrib = session.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.value).where(
                MetricSnapshot.entity_id.in_([r.id for r in repos]), MetricSnapshot.metric == "contributors",
                MetricSnapshot.day <= day).order_by(MetricSnapshot.entity_id, MetricSnapshot.day.desc())
            .distinct(MetricSnapshot.entity_id)).all()
        if contrib:
            out.append(Proxy("proxy_contributors", float(sum(v for _, v in contrib)), gh, 0.8,
                             "sum over repositories; a person on two repos counts twice"))
        else:
            missing["contributors"] = "contributor counts not observed"
    else:
        missing["releases"] = "no public repository linked to the company"
        missing["contributors"] = "no public repository linked to the company"

    papers = session.scalar(select(func.count()).select_from(ResearchPaper).where(
        ResearchPaper.company_id == company.id, ResearchPaper.published_at <= end,
        ResearchPaper.published_at >= end - timedelta(days=90)))
    out.append(Proxy("proxy_papers_90d", float(papers or 0), None, 0.8,
                     "papers linked through code or the company's GitHub org"))

    hn = session.execute(select(Signal.event_at, Signal.raw_value).where(
        Signal.entity_id == company.id, Signal.source_type == "hackernews",
        Signal.signal_type.in_(["hn_launch", "hn_show", "hn_discussion"]), Signal.event_at <= end,
        Signal.event_at >= end - timedelta(days=60))).all()
    cur = sum(float(v or 0) for at, v in hn if _aware(at) >= end - timedelta(days=30))
    prior = sum(float(v or 0) for at, v in hn if _aware(at) < end - timedelta(days=30))
    out += [Proxy("proxy_hn_points_30d", cur, "https://news.ycombinator.com/", 0.9,
                  "stories that link to the company's domain or GitHub org"),
            Proxy("proxy_hn_points_prior_30d", prior, "https://news.ycombinator.com/", 0.9)]

    posts = session.execute(select(Signal.title, Signal.source_url).where(
        Signal.entity_id == company.id, Signal.signal_type == "company_post", Signal.event_at <= end,
        Signal.event_at >= end - timedelta(days=30))).all()
    if posts or (company.attrs or {}).get("pages"):
        stories = [u for t, u in posts if is_customer_story(t, u, company.name)]
        out.append(Proxy("proxy_customer_stories_30d", float(len(stories)), stories[0] if stories else None, 0.8,
                         "case studies / customer stories on the company's own newsroom or blog"))
    else:
        missing["customer_stories"] = "no newsroom or blog observed"

    awards = session.execute(select(Signal.raw_value, Signal.source_url).where(
        Signal.entity_id == company.id, Signal.signal_type == "federal_award", Signal.event_at <= end,
        Signal.event_at >= end - timedelta(days=730))).all()
    if awards or attrs.get("usaspending_checked_at"):
        url = awards[0][1] if awards else "https://www.usaspending.gov/"
        out += [Proxy("proxy_federal_awards_24m", float(len(awards)), url, 1.0),
                Proxy("proxy_federal_award_amount_24m", float(sum(float(v or 0) for v, _ in awards)), url, 1.0)]
    else:
        missing["federal_awards"] = "no federal award recorded (USAspending not queried or no exact-name match)"
    return out, missing


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def write_proxies(session: Session, company_id: uuid.UUID, day: date, proxies: list[Proxy]) -> int:
    if not proxies:
        return 0
    rows = [{"entity_type": "company", "entity_id": company_id, "metric": p.metric, "day": day, "value": p.value,
             "source": PROXY_SOURCE, "is_demo": False, "source_url": p.source_url, "confidence": p.confidence,
             "proxy_label": LABELS.get(p.metric)} for p in proxies]
    stmt = pg_insert(MetricSnapshot).values(rows)
    stmt = stmt.on_conflict_do_update(constraint="uq_metric_day", set_={
        "value": stmt.excluded.value, "source": stmt.excluded.source, "source_url": stmt.excluded.source_url,
        "confidence": stmt.excluded.confidence, "proxy_label": stmt.excluded.proxy_label})
    session.execute(stmt)
    return len(rows)


def compare_windows(proxies: dict[str, Proxy | dict[str, Any]], missing: dict[str, str] | None = None
                    ) -> list[dict[str, Any]]:
    """[{metric, current, prior, window, comparable, reason, proxy_label, source_url, confidence}] per pair."""
    missing = missing or {}

    def val(m: str) -> tuple[float | None, Any]:
        p = proxies.get(m)
        if p is None:
            return None, None
        return (p.value, p) if isinstance(p, Proxy) else (p.get("value"), p)

    out = []
    for name, cur_m, prior_m, window in PAIRS:
        cur, pc = val(cur_m)
        prior, _ = val(prior_m)
        comparable = cur is not None and prior is not None
        reason = None if comparable else (missing.get(name) or "one of the two windows was not observed")
        src = pc.source_url if isinstance(pc, Proxy) else (pc or {}).get("source_url") if pc else None
        conf = pc.confidence if isinstance(pc, Proxy) else (pc or {}).get("confidence") if pc else None
        out.append({"metric": name, "current": cur, "prior": prior, "window": window, "comparable": comparable,
                    "reason": reason, "delta": (cur - prior) if comparable else None,
                    "proxy_label": LABELS.get(cur_m), "source_url": src, "confidence": conf})
    return out


def stored_proxies(session: Session, company_id: uuid.UUID, day: date | None = None) -> dict[str, dict[str, Any]]:
    """Latest stored proxy values per metric (on or before ``day``)."""
    q = select(MetricSnapshot).where(MetricSnapshot.entity_id == company_id, MetricSnapshot.metric.like("proxy_%"))
    if day is not None:
        q = q.where(MetricSnapshot.day <= day)
    out: dict[str, dict[str, Any]] = {}
    for m in session.scalars(q.order_by(MetricSnapshot.metric, MetricSnapshot.day.desc())):
        if m.metric not in out:
            out[m.metric] = {"metric": m.metric, "value": m.value, "day": m.day.isoformat(),
                             "source_url": m.source_url, "confidence": m.confidence, "proxy_label": m.proxy_label}
    return out
