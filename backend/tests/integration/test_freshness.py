"""Integration tests: per-entity data age, provider freshness and the feed's as-of date."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from radar import clock
from radar.models import (
    Company,
    IngestionRun,
    Job,
    MetricSnapshot,
    PaperAuthor,
    Person,
    Repository,
    ResearchPaper,
    Signal,
    SignalEntity,
    WebPage,
)
from radar.services import freshness
from radar.settings import get_settings

DAY = date(2026, 10, 6)  # tests pin TODAY_OVERRIDE to this day


def _dt(d: date, h: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, h, tzinfo=UTC)


def _sig(entity_id, entity_type: str, source_type: str, detected: datetime, *, event: datetime | None = None,
         is_demo: bool = False) -> Signal:
    return Signal(entity_id=entity_id, entity_type=entity_type, signal_type="test", source_type=source_type,
                  title=f"{source_type} signal", event_at=event or detected, detected_at=detected,
                  dedup_key=uuid.uuid4().hex, is_demo=is_demo)


@pytest.fixture()
def pinned_snapshot(monkeypatch):
    """Serve the data as a snapshot pinned to DAY (no TODAY_OVERRIDE)."""
    monkeypatch.setattr(get_settings(), "today_override", None)
    clock.set_as_of(DAY)
    yield
    clock.set_as_of(None)


def test_company_data_age_per_family(db):
    c = Company(slug="acme", name="Acme")
    other = Company(slug="other", name="Other")
    db.add_all([c, other])
    db.flush()
    repo = Repository(full_name="acme/engine", owner="acme", company_id=c.id)
    db.add(repo)
    db.flush()
    gh_day, hn_day = DAY - timedelta(days=3), DAY - timedelta(days=9)
    db.add_all([
        _sig(repo.id, "repository", "github", _dt(gh_day)),           # on the company's repo
        _sig(c.id, "company", "hackernews", _dt(hn_day)),              # direct
        _sig(c.id, "company", "github", _dt(DAY + timedelta(days=2))),  # after the data clock: ignored
        MetricSnapshot(entity_type="repository", entity_id=repo.id, metric="stars", day=DAY - timedelta(days=1),
                       value=10, source="observed"),
        MetricSnapshot(entity_type="repository", entity_id=repo.id, metric="stars", day=DAY,
                       value=11, source="backfill"),                 # reconstructed: not an observation
        WebPage(company_id=c.id, url="https://acme.test/", page_kind="home", last_fetched_at=_dt(DAY, 8)),
        Job(company_id=c.id, external_id="1", title="Engineer", first_seen_at=_dt(DAY - timedelta(days=5))),
        ResearchPaper(arxiv_id="2610.00001", title="P", company_id=c.id, first_seen_at=_dt(DAY - timedelta(days=20))),
    ])
    linked = _sig(other.id, "company", "x", _dt(DAY - timedelta(days=4)))
    db.add(linked)
    db.flush()
    db.add(SignalEntity(signal_id=linked.id, entity_id=c.id, entity_type="company"))
    db.flush()

    ages = freshness.data_age(db, "company", [c.id, other.id])
    a = ages[c.id]
    assert set(a.families) == set(freshness.FAMILIES)
    assert a.families["github"] == _dt(DAY - timedelta(days=1), 0)  # observed metric day beats the older signal
    assert a.families["community"] == _dt(hn_day)
    assert a.families["web"] == _dt(DAY, 8)
    assert a.families["hiring"] == _dt(DAY - timedelta(days=5))
    assert a.families["research"] == _dt(DAY - timedelta(days=20))
    assert a.families["social"] == _dt(DAY - timedelta(days=4))  # via SignalEntity link
    assert a.last_observed_at == _dt(DAY, 8) and a.age_days == 0
    assert a.updated_at is not None

    b = ages[other.id]
    assert b.families["social"] == _dt(DAY - timedelta(days=4))
    assert b.families["github"] is None and b.age_days == 4


def test_person_data_age_and_unobserved_entity(db):
    p = Person(slug="ada", name="Ada")
    quiet = Person(slug="quiet", name="Quiet")
    db.add_all([p, quiet])
    db.flush()
    paper = ResearchPaper(arxiv_id="2610.00002", title="Q", first_seen_at=_dt(DAY - timedelta(days=2)))
    db.add(paper)
    db.flush()
    db.add_all([
        PaperAuthor(paper_id=paper.id, person_id=p.id),
        MetricSnapshot(entity_type="person", entity_id=p.id, metric="followers", day=DAY - timedelta(days=7),
                       value=50, source="observed"),
        _sig(p.id, "person", "x", _dt(DAY - timedelta(days=1))),
    ])
    db.flush()
    ages = freshness.data_age(db, "person", [p.id, quiet.id])
    assert ages[p.id].families["research"] == _dt(DAY - timedelta(days=2))
    assert ages[p.id].families["github"] == _dt(DAY - timedelta(days=7), 0)
    assert ages[p.id].families["social"] == _dt(DAY - timedelta(days=1))
    assert ages[p.id].age_days == 1
    assert set(ages[p.id].families) == set(freshness.PERSON_FAMILIES)  # no hiring / web for people
    q = ages[quiet.id]
    assert q.last_observed_at is None and q.age_days is None and all(v is None for v in q.families.values())
    assert freshness.data_age(db, "person", []) == {}


def test_provider_freshness_excludes_pseudo_providers(db):
    t = _dt(DAY - timedelta(days=1))
    db.add_all([
        IngestionRun(provider="github", status="ok", finished_at=t),
        IngestionRun(provider="github", status="error", finished_at=t + timedelta(hours=5)),  # failed: ignored
        IngestionRun(provider="hackernews", status="partial", finished_at=t - timedelta(hours=1)),
        IngestionRun(provider="snapshot", status="ok", finished_at=t, params={"as_of": DAY.isoformat()}),
        IngestionRun(provider="demo", status="ok", finished_at=t),
    ])
    db.flush()
    assert freshness.provider_freshness(db) == {"github": t, "hackernews": t - timedelta(hours=1)}


def test_feed_as_of_live_by_default(db):
    a = freshness.feed_as_of(db)
    assert a == freshness.AsOf(data_as_of=DAY, is_snapshot=False, snapshot_age_days=None)
    assert freshness.snapshot_label(a) is None


def test_feed_as_of_pinned_snapshot(db, pinned_snapshot):
    a = freshness.feed_as_of(db)
    assert a.is_snapshot and a.data_as_of == DAY
    assert a.snapshot_age_days == (clock.wall_now().date() - DAY).days
    assert a.anchor == clock.now() == datetime(2026, 10, 6, 18, tzinfo=UTC)
    assert freshness.snapshot_label(a).startswith("Snapshot collected Oct 6, 2026 · ")


def test_feed_as_of_demo_snapshot_unpinned_uses_snapshot_run(db, monkeypatch):
    """DEMO_SNAPSHOT serves the snapshot day even if the clock is not pinned."""
    monkeypatch.setattr(get_settings(), "today_override", None)
    monkeypatch.setattr(get_settings(), "demo_snapshot", True)
    clock.set_as_of(None)
    db.add(IngestionRun(provider="snapshot", status="ok", finished_at=_dt(DAY), params={"as_of": "2026-09-30"}))
    db.flush()
    a = freshness.feed_as_of(db)
    assert a.is_snapshot and a.data_as_of == date(2026, 9, 30)
    assert a.anchor == datetime(2026, 9, 30, 18, tzinfo=UTC)


def test_radar_feed_exposes_as_of_and_counts_signals_relative_to_snapshot(db, client, pinned_snapshot, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_synthetic", False)
    c = Company(slug="acme", name="Acme")
    db.add(c)
    db.flush()
    db.add_all([
        _sig(c.id, "company", "github", _dt(DAY, 10)),                      # within 24h of the snapshot day
        _sig(c.id, "company", "github", _dt(DAY - timedelta(days=3))),      # older
        _sig(c.id, "company", "github", clock.wall_now() - timedelta(hours=1), event=clock.wall_now()),  # "live"
    ])
    db.commit()
    feed = client.get("/api/radar").json()
    assert feed["is_snapshot"] is True and feed["data_as_of"] == "2026-10-06"
    assert feed["snapshot_age_days"] == (clock.wall_now().date() - DAY).days
    assert feed["counts"]["signals_24h"] == 1
    assert feed["snapshot_note"].startswith("Snapshot collected Oct 6, 2026")
    assert "DEMO" not in feed["snapshot_note"]  # no fictional examples by default


def test_radar_feed_live_has_no_snapshot_fields(db, client):
    feed = client.get("/api/radar").json()
    assert feed["is_snapshot"] is False and feed["snapshot_age_days"] is None
    assert feed["data_as_of"] == DAY.isoformat() and feed["snapshot_note"] is None


def test_person_github_family_from_key_repos_and_profile_checks(db):
    from radar.models import RepoContributor

    p = Person(slug="kz", name="KZ", github_handle="kz",
               attrs={"talent_pool_observed_at": _dt(DAY - timedelta(days=6)).isoformat(),
                      "activity_checked_at": "not-a-date"})
    solo = Person(slug="solo", name="Solo", attrs={"activity_checked_at": _dt(DAY - timedelta(days=8)).isoformat()})
    db.add_all([p, solo])
    db.flush()
    repo = Repository(full_name="lab/sim", owner="lab")
    db.add(repo)
    db.flush()
    db.add_all([
        RepoContributor(repository_id=repo.id, person_id=p.id, contributions=40),
        MetricSnapshot(entity_type="repository", entity_id=repo.id, metric="stars", day=DAY - timedelta(days=2),
                       value=5, source="observed"),
        MetricSnapshot(entity_type="repository", entity_id=repo.id, metric="stars", day=DAY,
                       value=6, source="backfill"),  # reconstructed: not an observation
    ])
    db.flush()
    ages = freshness.data_age(db, "person", [p.id, solo.id])
    assert ages[p.id].families["github"] == _dt(DAY - timedelta(days=2), 0)  # key repo observed metric
    assert ages[solo.id].families["github"] == _dt(DAY - timedelta(days=8))  # recorded GitHub profile check
    assert ages[solo.id].families["research"] is None
