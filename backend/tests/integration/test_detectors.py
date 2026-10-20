from __future__ import annotations

import hashlib
import uuid
from datetime import date, timedelta

from radar.detectors.strange import (
    _Ctx,
    _young_orgs,
    affiliation_exodus,
    age_phrase,
    code_and_jobs,
    credible_engineers_tiny_repo,
    detect_strange_signals,
    former_coworkers_converging,
    hiring_pivot,
    lab_cluster,
    paper_code_launch,
    quiet_specialized_hiring,
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
    Relationship,
    RepoContributor,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
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
    # The hit is on-thesis and its HN attention was measured by a probe (0 mentions).
    from radar.models import EntityTopic
    from radar.thesis.classifier import ensure_topics
    from radar.thesis.loader import get_thesis

    topics = ensure_topics(db, get_thesis())
    db.add(EntityTopic(entity_type="company", entity_id=hit[0].id, topic_id=topics["robotics.manipulation"],
                       score=0.8, method="manual"))
    db.add(MetricSnapshot(entity_type="company", entity_id=hit[0].id, metric="hn_probe_mentions", day=DAY, value=0))
    db.add(MetricSnapshot(entity_type="company", entity_id=hit[0].id, metric="hn_probe_points", day=DAY, value=0))
    db.flush()
    if hn_on_hit:
        add_signal(db, hit[1], "repository", "hn_post", "hackernews", 20, "Show HN", raw=30)
    return hit


def test_quiet_velocity_fires(db, thesis):
    c, _ = _velocity_world(db, hn_on_hit=False)
    found = validate(quiet_velocity(ctx(db, thesis)))
    assert [f["entities"][0]["id"] for f in found] == [str(c.id)]
    assert found[0]["headline"] == "Proj9 gained 600 GitHub stars in 30 days without a single Hacker News thread"


def test_quiet_velocity_near_misses(db, thesis):
    _velocity_world(db, hn_on_hit=True)
    assert quiet_velocity(ctx(db, thesis)) == []


def test_quiet_velocity_requires_hn_measurement(db, thesis):
    _velocity_world(db, hn_on_hit=False)
    db.query(MetricSnapshot).filter(MetricSnapshot.metric.like("hn_probe%")).delete(synchronize_session=False)
    db.flush()
    assert quiet_velocity(ctx(db, thesis)) == []  # cannot claim "zero discussion" without measuring it


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


# ── credible engineers → tiny / young repo ───────────────────────────────────

def talent(db, name, lab="acme-lab", commits=40):
    p = person(db, name)
    p.attrs = {"lab_contributions": [{"org": lab, "label": "Acme Lab", "tier": 1, "repo": f"{lab}/core",
                                      "commits": commits}]}
    db.flush()
    return p


def solo_repo(db, full_name, stars, created_days_ago=20):
    owner = full_name.split("/")[0]
    r = Repository(full_name=full_name, owner=owner, gh_created_at=at(created_days_ago), first_seen_at=at(10),
                   stars=stars)
    db.add(r)
    db.flush()
    return r


def commits(db, repo, p, n=10, last_days_ago=5):
    db.add(RepoContributor(repository_id=repo.id, person_id=p.id, contributions=n, last_commit_at=at(last_days_ago)))
    db.flush()


def test_credible_engineers_tiny_repo_fires(db, thesis):
    r = solo_repo(db, "someone/tactile-sim", stars=12)
    a, b = talent(db, "Ann A"), talent(db, "Bob B")
    commits(db, r, a)
    commits(db, r, b)
    found = validate(credible_engineers_tiny_repo(ctx(db, thesis)))
    assert len(found) == 1 and found[0]["pattern"] == "credible_engineers_tiny_repo"
    assert found[0]["headline"] == ("Two engineers with verified commits to acme-lab/core are committing to "
                                    "someone/tactile-sim, a 12-star repository")
    assert "not employment" in found[0]["detail"]
    assert {e["type"] for e in found[0]["entities"]} == {"repository", "person"}


def test_credible_engineers_tiny_repo_young_org_fires_even_when_starred(db, thesis):
    c, r = org(db, "newco", created_days_ago=30)
    r.stars = 900
    a, b = talent(db, "Ann A"), talent(db, "Bob B")
    commits(db, r, a)
    commits(db, r, b)
    found = validate(credible_engineers_tiny_repo(ctx(db, thesis)))
    assert len(found) == 1 and "4-week-old GitHub organization's repository newco/core" in found[0]["headline"]


def test_credible_engineers_tiny_repo_near_misses(db, thesis):
    a, b, plain = talent(db, "Ann A"), talent(db, "Bob B"), person(db, "Cy C")
    one = solo_repo(db, "x/one-talent", stars=3)  # only one talent-pool person (+ a non-talent person)
    commits(db, one, a)
    commits(db, one, plain)
    big = solo_repo(db, "x/popular", stars=5000, created_days_ago=400)  # popular, no young org
    commits(db, big, a)
    commits(db, big, b)
    few = solo_repo(db, "x/drive-by", stars=3)  # two talent people but only 1 commit each
    commits(db, few, a, n=1)
    commits(db, few, b, n=1)
    stale = solo_repo(db, "x/stale", stars=3, created_days_ago=900)  # last commits years ago
    commits(db, stale, a, last_days_ago=700)
    commits(db, stale, b, last_days_ago=700)
    lab = solo_repo(db, "acme-lab/tiny-tool", stars=4)  # the lab's own repositories are pedigree, not news
    commits(db, lab, a)
    commits(db, lab, b)
    assert credible_engineers_tiny_repo(ctx(db, thesis)) == []


# ── code release + technical jobs ────────────────────────────────────────────


def test_code_and_jobs_fires(db, thesis):
    c, r = org(db, "shipco", created_days_ago=400)
    add_signal(db, r, "repository", "release_published", "github", 6, "shipco/core v0.4.0")
    _jobs(db, c, ["Senior Robotics Software Engineer", "Controls Engineer"], 3)
    found = validate(code_and_jobs(ctx(db, thesis)))
    assert len(found) == 1
    assert found[0]["headline"].startswith("Shipco released code and opened two technical roles within ")
    assert any(e["source_type"] == "jobs" for e in found[0]["evidence"])
    assert any(e["source_type"] == "github" for e in found[0]["evidence"])


def test_code_and_jobs_near_misses(db, thesis):
    c, r = org(db, "oldrelease", created_days_ago=400)
    add_signal(db, r, "repository", "release_published", "github", 45, "oldrelease/core v1")  # release too old
    _jobs(db, c, ["Firmware Engineer"], 3)
    c2, _ = org(db, "salesonly", created_days_ago=400)
    _jobs(db, c2, ["Account Executive"], 3)  # only non-technical roles
    r2 = Repository(full_name="salesonly/new", owner="salesonly", company_id=c2.id, gh_created_at=at(4),
                    first_seen_at=at(4), stars=1)
    db.add(r2)
    c3, _ = org(db, "codeonly", created_days_ago=10)  # code, no jobs
    db.flush()
    assert code_and_jobs(ctx(db, thesis)) == []


# ── quiet specialized hiring ─────────────────────────────────────────────────


def _attention(db, c, attention, day=DAY):
    db.add(ScoreSnapshot(entity_type="company", entity_id=c.id, day=day, score=40.0, components={},
                         attention={"technical": 80.0, "attention": attention, "gap": 0, "flagged": False,
                                    "coverage": "partial",
                                    "sources": [{"key": "hn_points", "label": "Hacker News points (90d)",
                                                 "available": attention is not None}]},
                         collision={}, version="t"))
    db.flush()


def test_quiet_specialized_hiring_fires(db, thesis):
    c, _ = org(db, "quietfab", created_days_ago=400)
    _jobs(db, c, ["ASIC Design Engineer", "RTL Engineer"], 5)
    _attention(db, c, 12.0)
    found = validate(quiet_specialized_hiring(ctx(db, thesis)))
    assert len(found) == 1
    assert found[0]["headline"] == ("Quietfab opened two specialized silicon roles in 30 days while public "
                                    "attention sits at the 12th percentile")


def test_quiet_specialized_hiring_near_misses(db, thesis):
    c, _ = org(db, "loudfab", created_days_ago=400)
    _jobs(db, c, ["ASIC Design Engineer", "RTL Engineer"], 5)
    _attention(db, c, 85.0)  # well known
    c2, _ = org(db, "unmeasured", created_days_ago=400)
    _jobs(db, c2, ["ASIC Design Engineer", "RTL Engineer"], 5)
    _attention(db, c2, None)  # attention not measured: unknown, not low
    c3, _ = org(db, "onerole", created_days_ago=400)
    _jobs(db, c3, ["ASIC Design Engineer"], 5)
    _attention(db, c3, 5.0)  # only one specialized role
    c4, _ = org(db, "generic", created_days_ago=400)
    _jobs(db, c4, ["Backend Engineer", "Frontend Engineer"], 5)
    _attention(db, c4, 5.0)  # technical but not specialized
    c5, _ = org(db, "oldroles", created_days_ago=400)
    _jobs(db, c5, ["ASIC Design Engineer", "RTL Engineer"], 60)
    _attention(db, c5, 5.0)  # roles posted two months ago
    assert quiet_specialized_hiring(ctx(db, thesis)) == []


# ── former co-workers converging ─────────────────────────────────────────────


def tie(db, a, b, kind="COAUTHORED_WITH", days_ago=300, epistemic="VERIFIED", **ev):
    src, dst = (a, b) if str(a.id) < str(b.id) else (b, a)
    rel = Relationship(src_type="person", src_id=src.id, dst_type="person", dst_id=dst.id, kind=kind,
                       first_seen_at=at(days_ago), epistemic=epistemic,
                       evidence=[{"title": ev.get("title", "Dense Tactile Skins"), "source_type": "arxiv",
                                  "source_url": "https://arxiv.org/abs/2601.00001", "event_at": at(days_ago).isoformat(),
                                  **{k: v for k, v in ev.items() if k != "title"}}])
    db.add(rel)
    db.flush()
    return rel


def test_former_coworkers_converging_fires(db, thesis):
    _, repo = org(db, "tactilelab", created_days_ago=40)
    a, b = person(db, "Ann A"), person(db, "Bob B")
    contribute(db, repo, a, b)
    tie(db, a, b)
    c = ctx(db, thesis)
    found = validate(former_coworkers_converging(c, *_young_orgs(c)))
    assert len(found) == 1
    assert found[0]["headline"] == ("Two people who previously co-authored “Dense Tactile Skins” are contributing "
                                    "to the same 6-week-old GitHub organization")
    assert found[0]["evidence"][0]["source_type"] == "arxiv"
    assert [f["pattern"] for f in validate(detect_strange_signals(db, DAY, thesis))] == ["former_coworkers_converging"]


def test_former_coworkers_converging_near_misses(db, thesis):
    _, repo = org(db, "tactilelab", created_days_ago=40)
    a, b, cc, d, e, f = (person(db, n) for n in ("Ann A", "Bob B", "Cy C", "Dee D", "Eve E", "Fay F"))
    contribute(db, repo, a, b, cc, d, e, f)
    tie(db, a, b, days_ago=10)  # tie dated after the org existed: met there, not prior
    tie(db, cc, d, kind="WORKED_WITH", days_ago=300)  # wrong kind (self-reported employer overlap)
    tie(db, e, f, epistemic="INFERRED", days_ago=300)  # not verified
    _, repo2 = org(db, "selfref", created_days_ago=40)
    g, h = person(db, "Gus G"), person(db, "Hal H")
    contribute(db, repo2, g, h)
    tie(db, g, h, kind="CONTRIBUTED_WITH", days_ago=300, title="selfref/core", repo_id=str(repo2.id))  # about the unit
    _, old = org(db, "oldorg", created_days_ago=400)
    i, j = person(db, "Ian I"), person(db, "Jo J")
    contribute(db, old, i, j)
    tie(db, i, j, days_ago=600)  # org is not young
    c = ctx(db, thesis)
    assert former_coworkers_converging(c, *_young_orgs(c)) == []
