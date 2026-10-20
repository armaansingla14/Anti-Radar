"""FORMATION RADAR end to end: relationships -> scores -> formation_candidates."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from radar.models.core import (
    Company,
    EntityTopic,
    FormationCandidate,
    Job,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
    SignalEntity,
    Topic,
)
from radar.pipeline import relationships
from radar.scoring import formation
from radar.scoring.run import score_all

DAY = date(2026, 10, 6)


def at(days_ago: float) -> datetime:
    return datetime(2026, 10, 6, 12, tzinfo=UTC) - timedelta(days=days_ago)


def topic(db, key="robotics.manipulation") -> Topic:
    t = db.scalar(select(Topic).where(Topic.key == key))
    if t is None:
        t = Topic(key=key, theme=key.split(".")[0], label=key)
        db.add(t)
        db.flush()
    return t


def tag(db, entity_id, etype="company", score=0.8, key="robotics.manipulation"):
    db.add(EntityTopic(entity_type=etype, entity_id=entity_id, topic_id=topic(db, key).id, score=score,
                       method="manual", evidence=["manipulation"]))
    db.flush()


def signal(db, entity, etype, stype, source, days_ago, title, related=()):
    s = Signal(entity_id=entity.id, entity_type=etype, signal_type=stype, source_type=source, title=title,
               source_url=f"https://example.org/{uuid.uuid4().hex[:8]}", event_at=at(days_ago), normalized_value=0.7,
               confidence=1.0, dedup_key=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
               is_demo=bool(getattr(entity, "is_demo", False)))
    db.add(s)
    db.flush()
    for r, rt in related:
        db.add(SignalEntity(signal_id=s.id, entity_id=r.id, entity_type=rt))
    db.flush()
    return s


def young_org(db, slug="acme", *, days_old=60, kind="project", stage="unknown", n_people=3, demo_member=False,
              prior_paper=True, on_thesis=True) -> tuple[Company, list[Person]]:
    created = DAY - timedelta(days=days_old)
    c = Company(slug=slug, name=slug.title(), github_org=slug, kind=kind, stage=stage, founded_date=created,
                domain=f"{slug}.dev", website=f"https://{slug}.dev", first_seen_at=at(8),
                attrs={"gh_org_created_at": f"{created.isoformat()}T00:00:00Z"})
    db.add(c)
    db.flush()
    r = Repository(full_name=f"{slug}/core", owner=slug, owner_type="Organization", company_id=c.id,
                   gh_created_at=at(days_old - 1), pushed_at=at(1), stars=120)
    db.add(r)
    db.flush()
    people = []
    for i in range(n_people):
        p = Person(slug=f"{slug}-p{i}", name=f"{slug.title()} Person {i}", github_handle=f"{slug}-p{i}",
                   prior_affiliations=["NVIDIA"] if i < 2 else [], is_demo=demo_member and i == 0,
                   first_seen_at=at(8), attrs={"self_reported": True})
        db.add(p)
        db.flush()
        db.add(RepoContributor(repository_id=r.id, person_id=p.id, contributions=30 - i,
                               first_commit_at=at(days_old - 2), last_commit_at=at(1)))
        people.append(p)
    if prior_paper:
        pp = ResearchPaper(arxiv_id=f"2503.{abs(hash(slug)) % 99999:05d}", title="Dexterous manipulation at scale",
                           published_at=at(days_old + 120), url="https://arxiv.org/abs/2503.00001")
        db.add(pp)
        db.flush()
        for i, p in enumerate(people[:2]):
            db.add(PaperAuthor(paper_id=pp.id, person_id=p.id, position=i))
    if on_thesis:
        tag(db, c.id)
    signal(db, c, "company", "org_created", "github", days_old, f"GitHub organization {slug} created")
    signal(db, c, "company", "hn_show", "hackernews", 5, f"Show HN: {slug}")
    signal(db, c, "company", "young_org_team", "github", 2, f"{slug}: 3 contributors with >=3 commits",
           related=[(p, "person") for p in people])
    db.add(Job(company_id=c.id, external_id=f"{slug}-1", title="Founding Robotics Engineer", url=f"https://{slug}.dev/jobs",
               posted_at=at(4), first_seen_at=at(4)))
    db.flush()
    return c, people


def run_all(db):
    relationships.build(db)
    score_all(db, DAY)
    return formation.run(db, DAY)


def cand(db, anchor_id) -> FormationCandidate | None:
    return db.scalar(select(FormationCandidate).where(FormationCandidate.anchor_id == anchor_id))


def test_young_org_team_becomes_candidate_with_evidence(db):
    c, people = young_org(db)
    out = run_all(db)
    fc = cand(db, c.id)
    assert fc is not None, out
    assert fc.kind == "team" and fc.anchor_type == "company" and fc.slug == "team-acme"
    assert fc.tier in ("high", "emerging", "early") and fc.score >= formation.TIER_EARLY
    assert set(fc.member_ids) == {p.id for p in people}
    assert fc.theme == "robotics" and fc.subtopic == "robotics.manipulation"
    assert set(fc.components) == set(formation.FORMATION_MAX)  # jsonb: order via formation.ORDER
    assert abs(sum(v["value"] for v in fc.components.values()) - fc.score) < 0.05
    # convergence: the paper predates the org, NVIDIA is a shared prior employer (self-reported)
    assert fc.convergence["prior_relationships"] >= 1 and fc.convergence["relationship_ids"]
    assert "co-authored a paper" in fc.convergence["explanation"]
    assert "self-reported" in fc.convergence["explanation"]
    kinds = fc.components["formation_evidence"]["raw"]["kinds"]
    assert {"org_created", "launch", "first_job", "website", "working_team", "founding_team"} <= set(kinds)
    assert {"github", "community", "hiring", "research"} <= set(fc.signal_families)
    assert set(fc.signal_families) <= {"github", "research", "hiring", "web", "community", "social"}
    assert fc.independent_signals >= len(fc.signal_families)
    assert fc.why_now and all(w["event_at"] for w in fc.why_now)
    assert [w["event_at"] for w in fc.why_now] == sorted((w["event_at"] for w in fc.why_now), reverse=True)
    assert not fc.is_demo and fc.day == DAY
    text = json.dumps([fc.title, fc.label, fc.components, fc.convergence, fc.why_now]).lower()
    assert "founded" not in text
    assert fc.label in (formation.LABEL_TEAM, formation.LABEL_SIGNALS)


def test_exclusions(db):
    old, _ = young_org(db, "oldco", days_old=400)
    lab, _ = young_org(db, "labco", kind="lab")
    big, _ = young_org(db, "bigco", stage="growth")
    off, _ = young_org(db, "offco", on_thesis=False)
    solo, _ = young_org(db, "soloco", n_people=1)
    run_all(db)
    for c in (old, lab, big, off, solo):
        assert cand(db, c.id) is None, c.slug


def test_rerun_is_stable_and_deletes_when_no_longer_qualifying(db):
    c, _ = young_org(db)
    run_all(db)
    first = cand(db, c.id)
    fid, detected, score = first.id, first.first_detected_at, first.score
    run_all(db)
    db.expire_all()
    again = cand(db, c.id)
    assert again.id == fid and again.first_detected_at == detected and again.score == score
    c.dismissed = True
    db.flush()
    out = run_all(db)
    assert cand(db, c.id) is None and out["deleted"] == 1


def test_demo_member_propagates(db):
    c, _ = young_org(db, demo_member=True)
    run_all(db)
    assert cand(db, c.id).is_demo


def test_person_candidate_from_own_formation_signals(db):
    p = Person(slug="solo-builder", name="Solo Builder", github_handle="solo", current_company="Stealth",
               prior_affiliations=["Anthropic"], first_seen_at=at(5), followers=40)
    db.add(p)
    db.flush()
    tag(db, p.id, "person", 0.9)
    signal(db, p, "person", "affiliation_changed", "github", 6, "Solo Builder's profile no longer lists Anthropic")
    signal(db, p, "person", "formation_language", "github", 6, "Bio now says 'building something new'")
    signal(db, p, "person", "x_formation_post", "x", 3, "Post: 'starting something new in robotics'")
    out = run_all(db)
    fc = cand(db, p.id)
    assert fc is not None, out
    assert fc.kind == "person" and fc.label == formation.LABEL_PERSON and fc.member_ids == [p.id]
    assert fc.components["team_convergence"]["value"] == 0
    assert fc.title == "Solo Builder — possible formation"
    assert set(fc.components["formation_evidence"]["raw"]["kinds"]) == {"affiliation_change", "formation_language"}


def test_team_members_are_not_duplicated_as_person_candidates(db):
    c, people = young_org(db)
    signal(db, people[0], "person", "bio_changed", "github", 3, "Bio changed", )
    tag(db, people[0].id, "person", 0.9)
    run_all(db)
    assert cand(db, c.id) is not None and cand(db, people[0].id) is None


def test_seeded_demo_halcyon_is_demo_candidate(seeded):
    from radar import ops
    from radar.db import session_scope

    out = ops.compute_formation()
    assert out["relationships"]["FOUNDED"] >= 2
    with session_scope() as s:
        halcyon = s.scalar(select(Company).where(Company.slug.like("%halcyon%")))
        fc = s.scalar(select(FormationCandidate).where(FormationCandidate.anchor_id == halcyon.id))
        assert fc is not None and fc.is_demo and fc.kind == "team"
        assert all(f.is_demo for f in s.scalars(select(FormationCandidate)) if f.anchor_id == halcyon.id)


def test_org_brand_account_is_not_a_member(db):
    c, people = young_org(db)
    brand = Person(slug="acme-brand", name="Acme", github_handle="acme-hq", first_seen_at=at(8))
    db.add(brand)
    db.flush()
    r = db.scalar(select(Repository).where(Repository.company_id == c.id))
    db.add(RepoContributor(repository_id=r.id, person_id=brand.id, contributions=50))
    db.flush()
    run_all(db)
    fc = cand(db, c.id)
    assert fc is not None and brand.id not in fc.member_ids and len(fc.member_ids) == len(people)


# ── M2.5: evidence scope, first-commit dates, person candidates, seed ─────────


def _repo(db, full_name, *, company=None, days_old=400) -> Repository:
    owner = full_name.split("/")[0]
    r = Repository(full_name=full_name, owner=owner, owner_type="User", company_id=company.id if company else None,
                   gh_created_at=at(days_old), pushed_at=at(1), stars=900)
    db.add(r)
    db.flush()
    return r


def test_member_activity_on_unrelated_repo_is_not_cited(db):
    """Regression (DeepLethe citing a member's 'bilawalsidhu/gods-eye-view')."""
    c, people = young_org(db)
    other = _repo(db, "someone/gods-eye-view")
    db.add(RepoContributor(repository_id=other.id, person_id=people[0].id, contributions=12))
    db.flush()
    signal(db, other, "repository", "new_contributors", "github", 1, "someone/gods-eye-view: 45 new contributors",
           related=[(people[0], "person")])
    signal(db, people[0], "person", "star_velocity", "github", 1, "Person 0's own side project +300 stars")
    aff = signal(db, people[1], "person", "affiliation_changed", "github", 2, "Acme Person 1: NVIDIA -> Acme")
    run_all(db)
    fc = cand(db, c.id)
    cited = json.dumps([fc.why_now, fc.components])
    assert "gods-eye-view" not in cited and "side project" not in cited
    # the member's own formation signal is still evidence
    assert str(aff.id) in cited
    assert "affiliation_change" in fc.components["formation_evidence"]["raw"]["kinds"]


def test_prior_cocontribution_dated_by_first_commit(db):
    c, people = young_org(db, prior_paper=False, days_old=60)
    for p in people:
        p.prior_affiliations = []
    old = _repo(db, "lab/old-sim")
    for p in people[:2]:
        db.add(RepoContributor(repository_id=old.id, person_id=p.id, contributions=20,
                               first_commit_at=at(300), last_commit_at=at(90)))
    undated = _repo(db, "lab/undated")
    for p in people[1:]:
        db.add(RepoContributor(repository_id=undated.id, person_id=p.id, contributions=20))
    db.flush()
    run_all(db)
    conv = cand(db, c.id).convergence
    assert conv["prior_relationships"] == 1 and "CONTRIBUTED_WITH" in conv["kinds"]
    assert "contributed to lab/old-sim together earlier" in conv["explanation"]
    assert "no first-commit dates" in conv["explanation"] and conv["undated_cocontributions"] == 1
    # every member's first commit on acme/core is 2 days after creation -> founding team
    assert len(conv["founding_members"]) == 3
    assert "within 30 days of its creation" in conv["explanation"]


def test_missing_first_commit_dates_are_said(db):
    c, people = young_org(db)
    for rc in db.scalars(select(RepoContributor)):
        rc.first_commit_at = None
    db.flush()
    run_all(db)
    fc = cand(db, c.id)
    assert "First-commit dates on Acme are unknown for all three" in fc.convergence["explanation"]
    assert "founding_team" not in fc.components["formation_evidence"]["raw"]["kinds"]


def considered(db) -> dict:
    """Every scored candidate (stored or below EARLY) by anchor id."""
    return {x.inp.anchor_id: x for x in formation.compute(db, DAY)}


def _founder_bio_person(db, slug, company, *, phrase="co-founder", followers=10, fit=0.9):
    p = Person(slug=slug, name=slug.title(), github_handle=slug, current_company=company, first_seen_at=at(5),
               followers=followers, attrs={"self_reported": True})
    db.add(p)
    db.flush()
    if fit:
        tag(db, p.id, "person", fit)
    s = signal(db, p, "person", "formation_language", "github", 4, f"{p.name}'s bio now mentions: {phrase}")
    s.meta = {"phrases": [phrase], "field": "bio"}
    db.flush()
    return p


def test_person_generic_founder_at_established_company_left_out(db):
    big = Company(slug="bigcorp", name="BigCorp", github_org="bigcorp", kind="company", stage="growth",
                  first_seen_at=at(900), attrs={"followers": 5000})
    db.add(big)
    db.flush()
    generic = _founder_bio_person(db, "generic-founder", "BigCorp")
    stealth = _founder_bio_person(db, "stealth-builder", "BigCorp", phrase="stealth")
    small = _founder_bio_person(db, "small-founder", "TinyCo")
    run_all(db)
    seen = considered(db)
    assert generic.id not in seen and cand(db, generic.id) is None
    for p in (stealth, small):
        sc = seen[p.id]
        assert sc.inp.kind == "person" and sc.title.endswith("— possible formation")
        assert sc.components["formation_evidence"]["raw"]["kinds"] == ["formation_language"]
        assert sc.tier != "high"  # a single person has no convergence
        # one bio phrase and a modest Founder Score is not enough on its own: below EARLY, not stored
        assert sc.score < formation.TIER_EARLY and cand(db, p.id) is None


def test_person_needs_qualifying_signal_and_fit_or_young_org(db):
    bio_only = Person(slug="bio-only", name="Bio Only", github_handle="bio-only", first_seen_at=at(5))
    off = Person(slug="off-thesis", name="Off Thesis", github_handle="off-thesis", first_seen_at=at(5))
    db.add_all([bio_only, off])
    db.flush()
    tag(db, bio_only.id, "person", 0.9)
    signal(db, bio_only, "person", "bio_changed", "github", 3, "Bio Only updated their bio")
    signal(db, off, "person", "affiliation_changed", "github", 3, "Off Thesis: A -> B")
    # off-thesis but linked to a young org (that is itself not a team candidate: only one member)
    _, members = young_org(db, "soloco", n_people=1, on_thesis=False)
    joiner = members[0]
    signal(db, joiner, "person", "affiliation_changed", "github", 3, "Soloco Person 0: X -> Soloco")
    # signals older than 180 days do not qualify
    stale = Person(slug="stale", name="Stale", github_handle="stale", first_seen_at=at(400))
    db.add(stale)
    db.flush()
    tag(db, stale.id, "person", 0.9)
    signal(db, stale, "person", "affiliation_changed", "github", 200, "Stale: A -> B")
    run_all(db)
    seen = considered(db)
    assert bio_only.id not in seen and off.id not in seen and stale.id not in seen
    fc = cand(db, joiner.id)
    assert fc is not None
    assert {"affiliation_change", "young_org"} <= set(fc.components["formation_evidence"]["raw"]["kinds"])


def test_person_candidate_cites_only_own_evidence(db):
    p = _founder_bio_person(db, "builder", None, phrase="building something new")
    elsewhere = _repo(db, "famous/project")
    signal(db, elsewhere, "repository", "star_velocity", "github", 1, "famous/project: +5,000 stars",
           related=[(p, "person")])
    mine = _repo(db, "builder/new-thing", days_old=20)
    signal(db, mine, "repository", "repo_created", "github", 20, "Repository builder/new-thing created",
           related=[(p, "person")])
    run_all(db)
    sc = considered(db)[p.id]
    cited = json.dumps([sc.why_now, sc.components, [x.title for x in sc.inp.signals]])
    assert "famous/project" not in cited and "builder/new-thing" in cited
