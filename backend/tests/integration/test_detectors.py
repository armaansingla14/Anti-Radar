from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta

from radar.detectors.strange import (
    _Ctx,
    _young_orgs,
    affiliation_exodus,
    age_phrase,
    detect_strange_signals,
    hiring_pivot,
    lab_cluster,
    paper_code_launch,
    quiet_velocity,
    shared_prior_employer_new_org,
)
from radar.models.core import (
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
)
from radar.schemas.api import StrangeSignal
from radar.scoring.extract import load_signal_index
from tests.integration.test_score_run import add_signal, at

DAY = date(2026, 10, 6)


def ctx(db, thesis) -> _Ctx:
    return _Ctx(db, DAY, thesis, load_signal_index(db, DAY, 180), at(0))


def org(db, slug, created_days_ago=42, **kw):
    c = Company(slug=slug, name=slug.title(), github_org=slug, first_seen_at=at(10), **kw)
    db.add(c)
    db.flush()
    r = Repository(
        full_name=f"{slug}/core",
        owner=slug,
        company_id=c.id,
        gh_created_at=at(created_days_ago),
        first_seen_at=at(10),
        stars=10,
    )
    db.add(r)
    db.flush()
    return c, r


def person(db, name, prior=(), current=None):
    p = Person(
        slug=name.lower().replace(" ", "-"),
        name=name,
        prior_affiliations=list(prior),
        current_company=current,
        github_handle=name.lower().replace(" ", ""),
        first_seen_at=at(10),
    )
    db.add(p)
    db.flush()
    return p


def contribute(db, repo, *people):
    for p in people:
        db.add(RepoContributor(repository_id=repo.id, person_id=p.id, contributions=10))
    db.flush()


def validate(found):
    for f in found:
        StrangeSignal.model_validate(f)
    return found


def test_age_phrase():
    assert (
        age_phrase(42) == "6-week-old" and age_phrase(3) == "3-day-old" and age_phrase(120) == "4-month-old"
    )


# ── shared prior employer ────────────────────────────────────────────────────


def test_shared_prior_employer_fires(db, thesis):
    _, repo = org(db, "orbitlabs")
    contribute(db, repo, *(person(db, n, prior=["SpaceX"]) for n in ("Ann A", "Bob B", "Cy C")))
    c = ctx(db, thesis)
    found = validate(shared_prior_employer_new_org(c, *_young_orgs(c)))
    assert len(found) == 1
    assert found[0]["headline"] == (
        "Three people who previously worked at SpaceX are contributing to the same "
        "6-week-old GitHub organization"
    )
    assert len(found[0]["entities"]) == 4 and 0 < found[0]["strength"] <= 1


def test_shared_prior_employer_near_misses(db, thesis):
    _, repo = org(db, "orbitlabs")
    contribute(
        db,
        repo,
        person(db, "Ann A", prior=["SpaceX"]),
        person(db, "Bob B", prior=["SpaceX"]),
        person(db, "Cy C", prior=["Blue Origin"]),
    )
    _, old_repo = org(db, "oldco", created_days_ago=400)
    contribute(db, old_repo, *(person(db, n, prior=["Tesla"]) for n in ("Dee D", "Eve E", "Fay F")))
    c = ctx(db, thesis)
    assert shared_prior_employer_new_org(c, *_young_orgs(c)) == []


# ── lab cluster ──────────────────────────────────────────────────────────────


def _paper(db, title, days_ago, authors, code_urls=()):
    p = ResearchPaper(
        arxiv_id=f"2609.{uuid.uuid4().int % 100000:05d}",
        title=title,
        published_at=at(days_ago),
        code_urls=list(code_urls),
        first_seen_at=at(days_ago),
    )
    db.add(p)
    db.flush()
    for i, a in enumerate(authors):
        db.add(PaperAuthor(paper_id=p.id, person_id=a.id, position=i))
    db.flush()
    return p


def test_lab_cluster_fires_and_near_miss(db, thesis):
    _, repo = org(db, "tactilelab", created_days_ago=60)
    a, b, cc, d = (person(db, n) for n in ("Ann A", "Bob B", "Cy C", "Dee D"))
    _paper(db, "Dense Tactile Skins", 90, [a, b, cc, d])
    contribute(db, repo, a, b, cc)
    c = ctx(db, thesis)
    found = validate(lab_cluster(c, *_young_orgs(c)))
    assert len(found) == 1 and found[0]["headline"].startswith("Three co-authors of “Dense Tactile Skins”")
    assert "2-month-old" in found[0]["headline"]


def test_lab_cluster_two_coauthors_do_not_fire(db, thesis):
    _, repo = org(db, "tactilelab", created_days_ago=60)
    a, b, cc = (person(db, n) for n in ("Ann A", "Bob B", "Cy C"))
    _paper(db, "Dense Tactile Skins", 90, [a, b])
    contribute(db, repo, a, b, cc)
    c = ctx(db, thesis)
    assert lab_cluster(c, *_young_orgs(c)) == []


# ── paper + code + HN ────────────────────────────────────────────────────────


def test_paper_code_launch_fires_and_near_miss(db, thesis):
    _, repo = org(db, "labx", created_days_ago=18)
    paper = _paper(db, "Fast Kernels", 20, [], code_urls=["https://github.com/labx/core"])
    hn = add_signal(db, repo, "repository", "hn_post", "hackernews", 15, "Show HN: Fast Kernels", raw=180)
    found = validate(paper_code_launch(ctx(db, thesis)))
    assert len(found) == 1 and "within 5 days" in found[0]["headline"]
    assert any(e["signal_id"] == str(hn.id) for e in found[0]["evidence"])
    paper.published_at = at(70)  # HN post now 55 days after the paper
    db.flush()
    assert paper_code_launch(ctx(db, thesis)) == []


# ── affiliation exodus ───────────────────────────────────────────────────────


def _left(db, p, org_name, days_ago):
    s = Signal(
        entity_id=p.id,
        entity_type="person",
        signal_type="affiliation_changed",
        source_type="github",
        title=f"{p.name} left {org_name}",
        event_at=at(days_ago),
        meta={"from": org_name, "to": "stealth"},
        dedup_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
    )
    db.add(s)
    db.flush()


def test_affiliation_exodus_fires_and_near_miss(db, thesis):
    ppl = [person(db, n) for n in ("Ann A", "Bob B", "Cy C")]
    for i, p in enumerate(ppl):
        _left(db, p, "Tesla", 3 + i * 5)
    found = validate(affiliation_exodus(ctx(db, thesis)))
    assert len(found) == 1 and found[0]["headline"] == "Three people left Tesla within 10 days"
    assert len(found[0]["evidence"]) == 3


def test_affiliation_exodus_outside_window_does_not_fire(db, thesis):
    ppl = [person(db, n) for n in ("Ann A", "Bob B", "Cy C")]
    _left(db, ppl[0], "Tesla", 3)
    _left(db, ppl[1], "Tesla", 8)
    _left(db, ppl[2], "Tesla", 45)
    _left(db, ppl[2], "Waymo", 5)
    assert affiliation_exodus(ctx(db, thesis)) == []


# ── quiet velocity ───────────────────────────────────────────────────────────


def _velocity_world(db, hn_on_hit: bool):
    db.add(IngestionRun(provider="hackernews", status="ok"))
    hit = None
    for i in range(10):
        c, r = org(db, f"proj{i}", created_days_ago=300)
        per_day = 20 if i == 9 else 1
        for d in (0, 30):
            db.add(
                MetricSnapshot(
                    entity_type="repository",
                    entity_id=r.id,
                    metric="stars",
                    day=DAY - timedelta(days=d),
                    value=float(1000 - per_day * d),
                )
            )
        if i == 9:
            hit = (c, r)
    db.flush()
    if hn_on_hit:
        add_signal(db, hit[1], "repository", "hn_post", "hackernews", 20, "Show HN", raw=30)
    return hit


def test_quiet_velocity_fires(db, thesis):
    c, _ = _velocity_world(db, hn_on_hit=False)
    found = validate(quiet_velocity(ctx(db, thesis)))
    assert [f["entities"][0]["id"] for f in found] == [str(c.id)]
    assert found[0]["headline"] == "Proj9 gained 600 GitHub stars in 30 days with zero Hacker News discussion"


def test_quiet_velocity_near_misses(db, thesis):
    _velocity_world(db, hn_on_hit=True)
    assert quiet_velocity(ctx(db, thesis)) == []


def test_quiet_velocity_requires_hn_coverage(db, thesis):
    _velocity_world(db, hn_on_hit=False)
    db.query(IngestionRun).delete()
    db.flush()
    assert quiet_velocity(ctx(db, thesis)) == []  # cannot claim "zero discussion" without HN data


# ── hiring pivot ─────────────────────────────────────────────────────────────


def _jobs(db, c, titles, days_ago):
    for t in titles:
        db.add(Job(company_id=c.id, external_id=uuid.uuid4().hex, title=t, first_seen_at=at(days_ago)))
    db.flush()


def test_hiring_pivot_fires(db, thesis):
    c, _ = org(db, "pivotco", created_days_ago=400)
    _jobs(db, c, ["Account Executive", "Backend Engineer"], 90)
    _jobs(db, c, ["ASIC Design Engineer", "RTL Engineer", "Physical Design Lead"], 10)
    found = validate(hiring_pivot(ctx(db, thesis)))
    assert len(found) == 1
    assert found[0]["headline"].startswith("Pivotco opened three silicon roles in 30 days")


def test_hiring_pivot_near_misses(db, thesis):
    c, _ = org(db, "steady", created_days_ago=400)
    _jobs(db, c, ["Verification Engineer"], 90)  # silicon was posted before
    _jobs(db, c, ["ASIC Design Engineer", "RTL Engineer", "Physical Design Lead"], 10)
    c2, _ = org(db, "fresh", created_days_ago=400)
    _jobs(db, c2, ["ASIC Design Engineer", "RTL Engineer", "Physical Design Lead"], 10)  # first hiring ever
    c3, _ = org(db, "small", created_days_ago=400)
    _jobs(db, c3, ["Account Executive"], 90)
    _jobs(db, c3, ["ASIC Design Engineer", "RTL Engineer"], 10)  # only two
    assert hiring_pivot(ctx(db, thesis)) == []


def test_detect_strange_signals_end_to_end(db, thesis):
    _, repo = org(db, "orbitlabs")
    contribute(db, repo, *(person(db, n, prior=["SpaceX"]) for n in ("Ann A", "Bob B", "Cy C")))
    found = validate(detect_strange_signals(db, DAY, thesis))
    assert [f["pattern"] for f in found] == ["shared_prior_employer_new_org"]
    again = detect_strange_signals(db, DAY, thesis)
    assert again[0]["id"] == found[0]["id"]  # stable ids across runs
