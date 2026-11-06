"""A small, fully fictional portfolio scenario ingested through the real pipeline (shared by portfolio tests).

Day: 2026-10-06 (conftest TODAY_OVERRIDE). Companies on fixture domains; nothing here describes a real company.
Expected funnel (window Sep 6 – Oct 6): 15 raw changes → 10 candidates → 9 events → 5 MATERIAL / 1 WATCH / 3 NOISE.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

from radar.pipeline.ingest import ingest_batch
from radar.portfolio.config import load_config
from radar.portfolio.sync import sync_portfolio
from radar.providers.base import (
    CompanyRecord,
    JobRecord,
    NormalizedSignal,
    ProviderBatch,
    domain_ref,
    gh_org_ref,
)
from tests.conftest import FIXTURES

DAY = date(2026, 10, 6)
CONFIG = FIXTURES / "portfolio" / "portfolio.yaml"
ACME = domain_ref("acme-robotics.com")
ORBIT = domain_ref("orbitform.ai")
GRIP = domain_ref("gripworks.io")
HALCYON = domain_ref("halcyonfusion.com")
LAUNCH_URL = "https://acme-robotics.com/news/introducing-gelgrip-2"


def at(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=UTC)


def post(ref, url: str, title: str, when: datetime, summary: str | None = None) -> tuple[NormalizedSignal, dict]:
    sig = NormalizedSignal(entity=ref, signal_type="company_post", source_type="website", source_url=url, title=title,
                           description=summary, event_at=when, metadata={"page_kind": "news", "via": "jsonld"})
    src = {"url": url, "source_type": "website", "title": title, "snippet": summary or title,
           "content_hash": hashlib.sha256(url.encode()).hexdigest(), "company_ref": ref.key}
    return sig, src


def _batch(provider: str, **kw) -> ProviderBatch:
    return ProviderBatch(provider=provider, **kw)


def home_source(text_hash: str, title: str = "Acme Robotics | Tactile grippers") -> dict:
    return {"url": "https://acme-robotics.com/", "source_type": "website", "title": title, "snippet": title,
            "content_hash": text_hash, "page_kind": "home", "company_ref": ACME.key}


def build(db) -> dict:
    """Ingest the scenario, sync the fixture config, and return the config."""
    # Orbitform is already known by its GitHub org (sync must match it, not duplicate it).
    ingest_batch(db, _batch("github", companies=[CompanyRecord(ref=gh_org_ref("orbitform"), name="Orbitform",
                                                                 github_org="orbitform")]))
    # Acme's site: three dated posts, the homepage (first observation), jobs.
    sigs, srcs = zip(*[
        post(ACME, "https://acme-robotics.com/news/series-b", "Acme Robotics raises $40M Series B", at(10, 1),
             "Acme Robotics raised a $40M Series B to scale tactile grippers."),
        post(ACME, LAUNCH_URL, "Introducing Gelgrip 2", at(9, 30), "Gelgrip 2 is our next-generation tactile gripper."),
        post(ACME, "https://acme-robotics.com/news/offsite", "Team offsite recap", at(9, 28)),
    ], strict=True)
    jobs = [JobRecord(company=ACME, external_id=f"r{i}", ats="ashby", title=t,
                      url=f"https://jobs.ashbyhq.com/acme/r{i}", posted_at=at(10, 5) - timedelta(days=2 * i))
            for i, t in enumerate(["Robotics Engineer, Manipulation", "Controls Engineer", "Motion Planning Engineer",
                                   "Perception Engineer", "Mechatronics Engineer", "Senior Robotics Engineer"])]
    jobs += [JobRecord(company=ACME, external_id=f"old{i}", ats="ashby", title="Software Engineer",
                       url=f"https://jobs.ashbyhq.com/acme/old{i}", posted_at=at(8, 1) - timedelta(days=i))
             for i in range(3)]
    ingest_batch(db, _batch("website", companies=[CompanyRecord(ref=ACME, name="Acme Robotics",
                                                                 website="https://acme-robotics.com",
                                                                 attrs={"pages": {"home": "https://acme-robotics.com/"}})],
                            signals=list(sigs), sources=[*srcs, home_source("home-v1")], jobs=jobs))
    sync = sync_portfolio(db, load_config(CONFIG))
    # Second crawl: the homepage text changed but its title did not (a copy edit).
    ingest_batch(db, _batch("website", sources=[home_source("home-v2")]))
    # Hacker News launch story linking the launch post.
    ingest_batch(db, _batch("hackernews", signals=[NormalizedSignal(
        entity=ACME, signal_type="hn_launch", source_type="hackernews",
        source_url="https://news.ycombinator.com/item?id=4242", title="Launch HN: Acme Robotics – tactile grippers",
        raw_value=120, event_at=at(9, 30, 16), confidence=1.0,
        metadata={"points": 120, "num_comments": 40, "link": LAUNCH_URL, "story_id": "4242"})],
        sources=[{"url": "https://news.ycombinator.com/item?id=4242", "source_type": "hackernews",
                  "title": "Launch HN: Acme Robotics – tactile grippers", "snippet": LAUNCH_URL,
                  "content_hash": "hn4242"}]))
    # Comps.
    s1, c1 = post(ORBIT, "https://orbitform.ai/blog/orbit-one", "Orbitform launches Orbit One humanoid hand", at(10, 3))
    s2, c2 = post(GRIP, "https://gripworks.io/news/seed", "Gripworks raises $10M seed round", at(10, 2))
    ingest_batch(db, _batch("website", signals=[s1, s2], sources=[c1, c2]))
    # Halcyon: a federal award (registry) and a generic post.
    s3, c3 = post(HALCYON, "https://halcyonfusion.com/news/summit", "See you at the fusion summit", at(9, 25))
    award = NormalizedSignal(
        entity=HALCYON, signal_type="federal_award", source_type="usaspending",
        source_url="https://www.usaspending.gov/award/CONT_AWD_TEST_1",
        title="Department of Energy: magnet prototype for a fusion pilot plant", raw_value=2_400_000.0,
        event_at=at(9, 20, 0),
        metadata={"amount": 2_400_000.0, "agency": "Department of Energy", "award_id": "TEST-1"})
    ingest_batch(db, _batch("usaspending", signals=[award], sources=[{
        "url": "https://www.usaspending.gov/award/CONT_AWD_TEST_1", "source_type": "usaspending",
        "title": "Department of Energy: magnet prototype for a fusion pilot plant",
        "snippet": "Department of Energy · $2,400,000 · magnet prototype for a fusion pilot plant",
        "content_hash": "award1"}]))
    ingest_batch(db, _batch("website", signals=[s3], sources=[c3]))
    db.flush()
    return sync
