from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from radar.detectors.hiring import classify_role, hiring_summary
from radar.detectors.unknown_yesterday import new_entities
from radar.models.core import (
    Company,
    CompanyMetric,
    CompanyPerson,
    EntityTopic,
    Job,
    MetricSnapshot,
    Person,
    PreferenceWeight,
    RepoContributor,
    Repository,
    ScoreSnapshot,
    Signal,
    SignalEntity,
    Topic,
)
from radar.scoring.anti_score import SCORE_VERSION
from radar.scoring.deltas import score_delta
from radar.scoring.extract import build_company_features, build_person_features
from radar.scoring.feedback import apply_feedback, load_prefs
from radar.scoring.run import anti_score_from_snapshot, score_all

DAY = date(2026, 10, 6)


def at(days_ago: float) -> datetime:
    return datetime(2026, 10, 6, 12, tzinfo=UTC) - timedelta(days=days_ago)


def add_signal(db, entity, etype, stype, source, days_ago, title, strength=0.8, related=(), raw=None):
    s = Signal(
        entity_id=entity.id,
        entity_type=etype,
        signal_type=stype,
        source_type=source,
        title=title,
        source_url=f"https://example.org/{uuid.uuid4().hex[:8]}",
        event_at=at(days_ago),
        normalized_value=strength,
        raw_value=raw,
        confidence=1.0,
        dedup_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
    )
    db.add(s)
    db.flush()
    for r, rtype in related:
        db.add(SignalEntity(signal_id=s.id, entity_id=r.id, entity_type=rtype))
    return s


def company(db, slug, first_seen_days=60, **kw):
    c = Company(slug=slug, name=slug.title(), first_seen_at=at(first_seen_days), **kw)
    db.add(c)
    db.flush()
    return c


def repo(db, c, name, stars_per_day, created_days_ago=200, days=45):
    r = Repository(
        full_name=f"{c.slug}/{name}",
        owner=c.slug,
        owner_type="Organization",
        company_id=c.id,
        stars=int(stars_per_day * days),
        gh_created_at=at(created_days_ago),
        first_seen_at=at(60),
        description=f"{name} robot manipulation stack",
    )
    db.add(r)
    db.flush()
    for d in range(days, -1, -1):
        db.add(
            MetricSnapshot(
                entity_type="repository",
                entity_id=r.id,
                metric="stars",
                day=DAY - timedelta(days=d),
                value=float(stars_per_day * (days - d)),
            )
        )
        if d % 7 == 0:
            db.add(
                MetricSnapshot(
                    entity_type="repository",
                    entity_id=r.id,
                    metric="commits_week",
                    day=DAY - timedelta(days=d),
                    value=float(stars_per_day),
                )
            )
    return r


def build_world(db):
    topic = Topic(key="robotics.manipulation", theme="robotics", label="Manipulation")
    db.add(topic)
    db.flush()
    hot = company(db, "hotbot", stage="pre_seed", description="Dexterous manipulation for factories")
    cold = company(db, "coldco", stage="growth", description="Enterprise CRM")
    fresh = company(db, "newco", first_seen_days=2, stage="formation")
    demo = company(db, "demobot", is_demo=True, description="Fictional robot")
    gone = company(db, "dismissed", dismissed=True)
    r_hot = repo(db, hot, "hands", 20)
    repo(db, cold, "crm", 1)
    repo(db, demo, "fake", 500)
    repo(db, gone, "x", 50)
    db.add(
        EntityTopic(
            entity_type="company",
            entity_id=hot.id,
            topic_id=topic.id,
            score=0.9,
            evidence=["manipulation", "dexterous"],
        )
    )
    founder = Person(
        slug="ada",
        name="Ada",
        github_handle="ada-l",
        prior_affiliations=["NVIDIA"],
        first_seen_at=at(3),
        followers=12,
    )
    db.add(founder)
    db.flush()
    db.add(CompanyPerson(company_id=hot.id, person_id=founder.id, is_founder=True, role="CEO"))
    sigs = [
        add_signal(
            db,
            r_hot,
            "repository",
            "star_velocity",
            "github",
            2,
            "Stars accelerating",
            0.9,
            related=[(hot, "company")],
        ),
        add_signal(db, founder, "person", "affiliation_changed", "github", 10, "Ada left NVIDIA", 0.9),
        add_signal(db, hot, "company", "hn_post", "hackernews", 5, "Show HN: hands", 0.6, raw=40),
        add_signal(db, hot, "company", "job_posted", "jobs", 4, "Hiring robotics engineers", 0.7),
    ]
    db.add(Job(company_id=hot.id, external_id="1", title="Senior Robotics Engineer", first_seen_at=at(5)))
    db.add(
        Job(
            company_id=hot.id,
            external_id="2",
            title="Account Executive",
            first_seen_at=at(50),
            closed_at=at(10),
        )
    )
    db.flush()
    return {
        "hot": hot,
        "cold": cold,
        "fresh": fresh,
        "demo": demo,
        "gone": gone,
        "founder": founder,
        "sigs": sigs,
    }


def test_score_all_writes_snapshots_with_evidence(db):
    w = build_world(db)
    summary = score_all(db, DAY)
    assert summary["companies_scored"] == 4 and summary["people_scored"] == 1
    assert summary["company_cohort"] == 3  # demo excluded from cohort, dismissed excluded entirely

    snaps = {s.entity_id: s for s in db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.day == DAY))}
    assert w["gone"].id not in snaps
    hot = snaps[w["hot"].id]
    assert hot.version == SCORE_VERSION and 0 <= hot.score <= 100
    assert snaps[w["hot"].id].score > snaps[w["cold"].id].score
    assert set(hot.components) == {
        "technical_momentum",
        "founder_signal",
        "acceleration",
        "thesis_fit",
        "novelty",
        "attention_gap",
    }
    tm_ev = hot.components["technical_momentum"]["evidence"]
    assert tm_ev and tm_ev[0]["signal_id"] == str(w["sigs"][0].id) and tm_ev[0]["source_url"]
    assert hot.components["founder_signal"]["value"] > 0
    assert "manipulation" in hot.components["thesis_fit"]["explanation"]
    assert set(hot.collision["families"]) >= {"github", "hackernews", "hiring/web"}
    assert snaps[w["demo"].id].is_demo

    api = anti_score_from_snapshot(hot)
    assert api.total == round(hot.score) and len(api.components) == 6 and api.collision is not None

    cm = db.get(CompanyMetric, w["hot"].id).metrics
    assert cm["stars_30d"] == 600 and cm["open_roles"] == 1 and cm["technical_roles_30d"] == 1
    assert cm["hn_points_90d"] == 40


def test_features_distinguish_missing_history(db):
    w = build_world(db)
    feats = {f.entity_id: f for f in build_company_features(db, DAY)}
    assert feats[w["fresh"].id].stars_30d is None and feats[w["fresh"].id].stars_total is None
    assert feats[w["hot"].id].stars_7d == 140 and feats[w["hot"].id].stars_prev_28d == 560
    assert feats[w["hot"].id].commits_4w == 80
    assert w["founder"].id in feats[w["hot"].id].founder_ids


def test_score_deltas(db):
    w = build_world(db)
    score_all(db, DAY - timedelta(days=7))
    score_all(db, DAY)
    d = score_delta(db, w["hot"].id, DAY, 7)
    now = db.scalar(
        select(ScoreSnapshot.score).where(ScoreSnapshot.entity_id == w["hot"].id, ScoreSnapshot.day == DAY)
    )
    then = db.scalar(
        select(ScoreSnapshot.score).where(
            ScoreSnapshot.entity_id == w["hot"].id, ScoreSnapshot.day == DAY - timedelta(days=7)
        )
    )
    assert d == round(now - then, 2)
    assert score_delta(db, w["hot"].id, DAY, 30) is None
    # re-running the same day upserts rather than duplicating
    score_all(db, DAY)
    assert len(db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == w["hot"].id)).all()) == 2


def test_feedback_updates_bounded_weights_and_flags_bad_data(db, thesis):
    w = build_world(db)
    for _ in range(10):
        out = apply_feedback(db, "company", w["hot"].id, "useful")
    assert out["thesis:robotics"] == thesis.scoring.feedback.max_multiplier
    assert "signal:star_velocity" in out
    apply_feedback(db, "company", w["hot"].id, "too_early")
    assert db.get(PreferenceWeight, "stage:pre_seed").multiplier == 1 - thesis.scoring.feedback.step / 2
    assert apply_feedback(db, "company", w["cold"].id, "bad_data") == {}
    assert db.get(Company, w["cold"].id).attrs["data_quality_flag"] is True
    prefs = load_prefs(db)
    assert all(
        thesis.scoring.feedback.min_multiplier <= v <= thesis.scoring.feedback.max_multiplier
        for v in prefs.values()
    )


def test_unknown_yesterday_and_hiring(db, thesis):
    w = build_world(db)
    score_all(db, DAY)
    new = new_entities(db, DAY, 7)
    assert [c["slug"] for c in new["companies"]] == ["newco"]
    assert new["people"][0]["id"] == str(w["founder"].id) and new["people"][0]["score"] is not None
    h = hiring_summary(db, w["hot"].id, DAY)
    assert h["open_roles"] == 1 and h["technical_roles"] == 1 and h["closed_30d"] == 1
    assert h["by_class"] == {"robotics_controls": 1} and h["new_30d"] == 1
    assert classify_role("Staff CUDA Kernel Engineer", thesis) == "gpu_compiler"
    assert classify_role("Office Manager", thesis) is None


def _metric(db, r, metric, days_ago, value, source="observed"):
    db.add(MetricSnapshot(entity_type="repository", entity_id=r.id, metric=metric,
                          day=DAY - timedelta(days=days_ago), value=float(value), source=source))


def test_transition_credit_only_for_the_person_it_is_about(db):
    """#1: Mira's bio change linked to a shared repo must not give Ana a 'recent transition'."""
    c = company(db, "halcyon")
    r = repo(db, c, "tactile", 5)
    ana = Person(slug="ana", name="Ana", first_seen_at=at(30))
    mira = Person(slug="mira", name="Mira", first_seen_at=at(30))
    db.add_all([ana, mira])
    db.flush()
    for p in (ana, mira):
        db.add(RepoContributor(repository_id=r.id, person_id=p.id, contributions=50))
    bio = add_signal(db, mira, "person", "bio_changed", "github", 3, "Mira's bio no longer lists X", 0.9,
                     related=[(r, "repository")])
    add_signal(db, r, "repository", "new_contributors", "github", 1, "halcyon/tactile: 14 new contributors",
               0.9, raw=14)
    db.flush()
    feats = {p.person_id: p for p in build_person_features(db, DAY)}
    assert feats[mira.id].recent_transition and feats[mira.id].transition_evidence == [bio.id]
    assert not feats[ana.id].recent_transition and feats[ana.id].transition_evidence == []
    assert all(s.id != bio.id for s in feats[ana.id].own_signals)

    score_all(db, DAY)
    snap = db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == ana.id)).one()
    form = snap.components["formation"]
    assert form["value"] == 0 and "transition" not in form["explanation"].lower()
    assert form["evidence"] == []


def test_contributors_are_not_counted_as_founders(db):
    """#7: contributor-only links give no founder credit and are not called founders."""
    c = company(db, "vmodal")
    repo(db, c, "tools", 3)
    for i in range(3):
        p = Person(slug=f"contrib-{i}", name=f"C{i}", first_seen_at=at(30))
        db.add(p)
        db.flush()
        db.add(CompanyPerson(company_id=c.id, person_id=p.id, role="contributor", is_founder=False))
    db.flush()
    f = next(x for x in build_company_features(db, DAY) if x.entity_id == c.id)
    assert f.founder_ids == [] and f.linked_people == 3
    score_all(db, DAY)
    fs = db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == c.id)).one().components["founder_signal"]
    assert fs["value"] == 0 and "linked founder" not in fs["explanation"]
    assert "3 linked people are not marked as founders" in fs["explanation"]


def test_new_contributors_never_mix_contributor_definitions(db):
    """#33/#16: backfill (first-commit) series vs /contributors totals; repo created from history."""
    c = company(db, "godseye")
    r = repo(db, c, "view", 2, created_days_ago=20)
    for d, v in ((60, 50), (40, 55), (10, 65)):
        _metric(db, r, "contributors", d, v, source="backfill")
    _metric(db, r, "contributors", 0, 34)  # legacy observed /contributors count (different measure)
    db.flush()
    f = next(x for x in build_company_features(db, DAY) if x.entity_id == c.id)
    assert f.new_contributors_30d == 10.0  # 65 - 55, not 34 - 55 nor the full 34
    assert f.contributors_total == 34.0

    # once the provider stores totals separately, they are used for the total
    _metric(db, r, "contributors_total", 0, 36)
    db.flush()
    f = next(x for x in build_company_features(db, DAY) if x.entity_id == c.id)
    assert f.contributors_total == 36.0 and f.new_contributors_30d == 10.0

    # the provider's first-commit-based signal wins when fresh
    add_signal(db, r, "repository", "new_contributors", "github", 1, "godseye/view: 7 new contributors", 0.5,
               raw=7)
    db.flush()
    f = next(x for x in build_company_features(db, DAY) if x.entity_id == c.id)
    assert f.new_contributors_30d == 7.0


def test_measurement_signals_do_not_feed_acceleration(db):
    """#5/#6: a star_velocity measurement restating stars_30d is not collision evidence."""
    c = company(db, "dexmal")
    r = repo(db, c, "opendm", 30)
    add_signal(db, r, "repository", "star_velocity", "github", 0, "dexmal/opendm: +1,735 stars in 30 days", 0.92,
               related=[(c, "company")])
    db.flush()
    score_all(db, DAY)
    snap = db.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == c.id)).one()
    assert snap.collision["score"] == 0 and snap.collision["signal_count"] == 0
    assert snap.components["acceleration"]["raw"]["collision"] == 0


def test_score_all_persists_company_velocity(db):
    """Per-day velocities (sum over repos) with a provenance basis, under CompanyMetric.metrics['velocity']."""
    w = build_world(db)
    score_all(db, DAY)
    vel = db.get(CompanyMetric, w["hot"].id).metrics["velocity"]
    assert vel["computed_on"] == DAY.isoformat() and vel["repos"] == 1
    assert vel["star_velocity_7d"] == 20 and vel["star_velocity_7d_basis"] == "observed"
    assert vel["star_velocity_30d"] == 20 and vel["star_acceleration"] == 0
    assert vel["star_acceleration_text"] == "20 → 20 stars/day"
    # commits_week = 20 per sampled week: the last complete week gives 20/7 commits/day.
    assert abs(vel["commit_velocity_7d"] - 20 / 7) < 1e-3 and vel["commit_velocity_7d_basis"] == "observed"
    # Never measured -> None with an explicit basis, not 0.
    assert vel["release_velocity_90d"] is None and vel["release_velocity_90d_basis"] == "insufficient_history"
    assert vel["issue_velocity_30d"] is None
    # Reconstructed runs leave the denormalized metrics (and velocity) untouched.
    score_all(db, DAY - timedelta(days=3), reconstruct=True)
    assert db.get(CompanyMetric, w["hot"].id).metrics["velocity"]["computed_on"] == DAY.isoformat()


def test_company_velocity_sums_repos_and_never_mixes_contributor_definitions(db):
    from radar.scoring.extract import build_company_velocity

    c = company(db, "twinrepo")
    a = repo(db, c, "a", 10)
    b = repo(db, c, "b", 4)
    # Star backfill change points for a third repo with sparse history (step series) + observed today.
    r3 = Repository(full_name="twinrepo/c", owner="twinrepo", company_id=c.id, gh_created_at=at(300),
                    first_seen_at=at(1))
    db.add(r3)
    db.flush()
    for d, v in ((50, 100), (20, 130), (3, 160)):
        _metric(db, r3, "stars", d, v, source="backfill")
    _metric(db, r3, "stars", 0, 170)
    # Contributors: backfill first-commit series + a legacy observed /contributors count (other measure).
    for d, v in ((60, 3), (40, 5), (10, 8)):
        _metric(db, a, "contributors", d, v, source="backfill")
    _metric(db, a, "contributors", 0, 4)  # legacy, no contributors_total that day: ignored
    _metric(db, a, "releases_total", 120, 0, source="backfill")
    _metric(db, a, "releases_total", 30, 2, source="backfill")
    _metric(db, a, "releases_total", 0, 3)
    for d, v in ((30, 20), (0, 11)):
        _metric(db, b, "open_issues", d, v)
    db.flush()
    vel = build_company_velocity(db, DAY, [c.id])[c.id]
    assert vel["repos"] == 3
    # a: 10/day observed, b: 4/day observed, c: (170-130)/7 from backfill -> basis backfill.
    assert abs(vel["star_velocity_7d"] - (14 + 40 / 7)) < 1e-3
    assert vel["star_velocity_7d_basis"] == "backfill" and vel["coverage"]["star_velocity_7d"] == 3
    assert abs(vel["release_velocity_90d"] - 3 / 90) < 1e-3
    assert abs(vel["issue_velocity_30d"] - (-9 / 30)) < 1e-3 and vel["issue_velocity_30d_basis"] == "observed"
    # Legacy observed point dropped: end of window unknown -> insufficient, never 4 - 5.
    assert vel["contributor_velocity_30d"] is None

    # A deep fetch writes 'contributors' (same definition as backfill) together with 'contributors_total'.
    db.execute(MetricSnapshot.__table__.update().where(
        MetricSnapshot.entity_id == a.id, MetricSnapshot.metric == "contributors", MetricSnapshot.day == DAY,
    ).values(value=9.0))
    _metric(db, a, "contributors_total", 0, 12)
    db.flush()
    vel = build_company_velocity(db, DAY, [c.id])[c.id]
    assert abs(vel["contributor_velocity_30d"] - (9 - 5) / 30) < 1e-3
    assert vel["contributor_velocity_30d_basis"] == "backfill"
