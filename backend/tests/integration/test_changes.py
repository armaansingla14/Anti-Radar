"""SINCE LAST SNAPSHOT (services/changes.py, /api/changes) on a direct two-day fixture.

Base snapshot day 2026-09-20 (ISO week 2026-W38), current day 2026-10-06 (= TODAY_OVERRIDE, week 2026-W41).
Rows are inserted directly so the comparison contract is pinned independently of the scoring pipeline.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.models import (
    Company,
    FormationCandidate,
    FormationHistory,
    IngestionRun,
    Job,
    MeetingRecommendation,
    MetricSnapshot,
    PaperAuthor,
    Person,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
)
from radar.services import changes, memo

B, C = date(2026, 9, 20), date(2026, 10, 6)
B_AT = datetime(2026, 9, 20, 18, tzinfo=UTC)
C_AT = datetime(2026, 10, 6, 18, tzinfo=UTC)
OLD = datetime(2026, 8, 1, tzinfo=UTC)
V = "2026.10-1"


def comps(tm: float = 10.0, acc: float | None = 0.4) -> dict:
    def comp(key, value, mx, raw=None):
        return {"key": key, "label": key.replace("_", " ").title(), "value": value, "max": mx,
                "explanation": f"{key} {value}", "raw": raw or {}, "evidence": []}

    return {"technical_momentum": comp("technical_momentum", tm, 25),
            "acceleration": comp("acceleration", 5.0, 20, {"acc": acc}),
            "thesis_fit": comp("thesis_fit", 8.0, 15)}


def collision(strength: str | None, families: list[str]) -> dict:
    if strength is None:
        return {}
    return {"score": {"HIGH": 0.8, "MEDIUM": 0.55, "LOW": 0.1}[strength], "families": families, "window_days": 42,
            "span_days": 3.0, "signal_count": len(families), "summary": "x", "strength": strength}


def snap(s, ent, day: date, score: float, *, etype="company", version=V, reconstructed=False, tm=10.0, acc=0.4,
         col: str | None = None, families=("github",)):
    att = {"technical": 50.0, "attention": 20.0, "gap": 30.0, "flagged": False, "coverage": "partial",
           "sources": []}
    att.update({"reconstructed": True} if reconstructed else {"reconstructed_score": score})
    s.add(ScoreSnapshot(entity_type=etype, entity_id=ent.id, day=day, score=score, version=version,
                        components=comps(tm, acc), attention=att, collision=collision(col, list(families)),
                        is_demo=False))


def company(s, slug: str, first_seen=OLD) -> Company:
    c = Company(slug=slug, name=slug.title(), github_org=slug, first_seen_at=first_seen, kind="project")
    s.add(c)
    s.flush()
    return c


def person(s, handle: str, first_seen=OLD) -> Person:
    p = Person(slug=handle, name=handle.title(), github_handle=handle, first_seen_at=first_seen)
    s.add(p)
    s.flush()
    return p


def signal(s, ent, etype: str, source: str, detected: datetime, title: str) -> Signal:
    x = Signal(entity_id=ent.id, entity_type=etype, signal_type="repo_created", source_type=source,
               source_url=f"https://example.org/{uuid.uuid4().hex[:8]}", title=title, event_at=detected - timedelta(days=1),
               detected_at=detected, dedup_key=uuid.uuid4().hex)
    s.add(x)
    return x


def hist(s, day: date, anchor: uuid.UUID, slug: str, tier: str, score: float):
    s.add(FormationHistory(day=day, slug=slug, kind="team", anchor_type="company", anchor_id=anchor, title=slug,
                           theme="robotics", score=score, tier=tier, label="EMERGING TEAM", components={}))


def meet_row(s, week: str, p: Person, rank: int):
    s.add(MeetingRecommendation(week=week, person_id=p.id, rank=rank, priority="WATCH", score=50.0 - rank,
                                reasons={}, confidence="EARLY SIGNAL"))


@pytest.fixture()
def world(db):
    memo.clear()
    s = db
    for d, kind, link in ((B, "base", 0), (C, "update", 1)):
        s.add(IngestionRun(provider="snapshot", status="ok", params={"as_of": d.isoformat(), "kind": kind,
                                                                     "link": link, "files": 3 + link}))
    alpha, beta, gamma = company(s, "alpha"), company(s, "beta"), company(s, "gamma")
    delta, eps = company(s, "delta"), company(s, "eps")
    newco = company(s, "newco", first_seen=C_AT)
    # alpha +10 (re-observed via a new GitHub signal; technical momentum +4 -> momentum), LOW -> MEDIUM collision
    snap(s, alpha, B, 40, tm=10, col="LOW")
    snap(s, alpha, C, 50, tm=14, col="MEDIUM", families=("github", "arxiv"))
    signal(s, alpha, "company", "github", C_AT, "alpha shipped a repo")
    # beta -8 (re-observed via an observed metric on its repository), HIGH -> LOW collision (ended)
    snap(s, beta, B, 60, col="HIGH", families=("github", "arxiv", "hackernews"))
    snap(s, beta, C, 52, col="LOW")
    beta_repo = Repository(full_name="beta/core", owner="beta", company_id=beta.id, first_seen_at=OLD, stars=10)
    s.add(beta_repo)
    s.flush()
    s.add(MetricSnapshot(entity_type="repository", entity_id=beta_repo.id, metric="stars", day=C, value=12,
                         source="observed"))
    # gamma +2 (re-observed, below threshold)
    snap(s, gamma, B, 30)
    snap(s, gamma, C, 32)
    signal(s, gamma, "company", "hackernews", C_AT, "gamma on HN")
    # delta +10 but nothing observed in the window -> not compared
    snap(s, delta, B, 45)
    snap(s, delta, C, 55)
    signal(s, delta, "company", "github", B_AT, "delta old signal")  # detected on the base day: outside the window
    # eps: reconstructed baseline -> not comparable
    snap(s, eps, B, 20, reconstructed=True)
    snap(s, eps, C, 35)
    signal(s, eps, "company", "github", C_AT, "eps signal")
    # newco: new to Radar, HIGH collision on the current day only
    snap(s, newco, C, 70, col="HIGH", families=("github", "arxiv", "hackernews"))
    s.add(Repository(full_name="newco/robot", owner="newco", company_id=newco.id, first_seen_at=C_AT, stars=300,
                     gh_created_at=datetime(2026, 9, 1, tzinfo=UTC)))
    s.add(Job(company_id=alpha.id, external_id="j1", title="Robotics Engineer", first_seen_at=C_AT,
              posted_at=datetime(2026, 10, 1, tzinfo=UTC), url="https://jobs.example.org/j1"))
    # people / research
    old_p, new_p = person(s, "oldp"), person(s, "newp", first_seen=C_AT)
    snap(s, new_p, C, 44, etype="person")
    paper = ResearchPaper(arxiv_id="2610.00001", title="Dexterous hands", first_seen_at=C_AT,
                          published_at=datetime(2026, 10, 2, tzinfo=UTC))
    s.add(paper)
    s.flush()
    s.add(PaperAuthor(paper_id=paper.id, person_id=old_p.id, position=0))
    # formation history: A1 early->emerging (STRENGTHENED), A2 -5 (WEAKENED), A3 same (UNCHANGED),
    # A4 dropped, A5 new
    a = {k: uuid.uuid4() for k in ("a1", "a2", "a3", "a4", "a5")}
    a["a1"] = alpha.id
    hist(s, B, a["a1"], "team-alpha", "early", 45)
    hist(s, C, a["a1"], "team-alpha", "emerging", 52)
    hist(s, B, a["a2"], "team-a2", "emerging", 60)
    hist(s, C, a["a2"], "team-a2", "emerging", 55)
    hist(s, B, a["a3"], "team-a3", "high", 75)
    hist(s, C, a["a3"], "team-a3", "high", 76)
    hist(s, B, a["a4"], "team-a4", "early", 35)
    hist(s, C, a["a5"], "team-a5", "early", 33)
    s.add(FormationCandidate(slug="team-alpha", kind="team", anchor_type="company", anchor_id=alpha.id,
                             title="Formation signals around Alpha", theme="robotics", member_ids=[], score=52,
                             tier="emerging", label="EMERGING TEAM", components={}, convergence={},
                             first_detected_at=B_AT, latest_activity_at=C_AT, why_now=[], day=C))
    # meet: W38 [p1, p2, p3] -> W41 [p2, p1, p4]
    p1, p2, p3, p4 = (person(s, f"m{i}") for i in range(1, 5))
    for i, p in enumerate((p1, p2, p3), 1):
        meet_row(s, "2026-W38", p, i)
    for i, p in enumerate((p2, p1, p4), 1):
        meet_row(s, "2026-W41", p, i)
    s.commit()
    yield {"alpha": alpha.id, "beta": beta.id, "delta": delta.id, "eps": eps.id, "newco": newco.id, **a,
           "p1": p1.id, "p2": p2.id, "p3": p3.id, "p4": p4.id, "newp": new_p.id}
    memo.clear()


def test_points_and_default_pair(world, db):
    assert changes.snapshot_points(db) == [B, C]
    assert changes.default_pair(db) == (B, C)
    pts = changes.points_out(db)
    assert pts.latest == C and (pts.default_base, pts.default_current) == (B, C)
    assert [p.kind for p in pts.points] == ["base", "update"] and pts.points[1].files == 4


def test_new_to_radar_sections(world, db):
    r = changes.compare(db)
    assert r.available and (r.base_day, r.current_day) == (B, C) and r.is_snapshot
    assert r.new_companies.count == 1 and r.new_companies.items[0].slug == "newco"
    assert r.new_companies.items[0].score == 70
    assert [i.slug for i in r.new_people.items] == ["newp"]
    assert r.new_repositories.count == 1
    repo = r.new_repositories.items[0]
    assert repo.name == "newco/robot" and repo.parent.slug == "newco" and repo.created_at.date() == date(2026, 9, 1)
    assert r.new_papers.count == 1 and r.new_papers.items[0].created_at.date() == date(2026, 10, 2)
    assert r.new_research.count == 1 and "Oldp" in r.new_research.items[0].detail
    assert r.new_jobs.count == 1 and r.new_jobs.items[0].parent.slug == "alpha"
    fams = {f.family: f.count for f in r.new_signals_by_family}
    assert fams == {"github": 2, "community": 1} and r.new_signals_total == 3  # delta's base-day signal excluded


def test_score_changes_both_directions_and_exclusions(world, db):
    sc = changes.compare(db).score_changes
    assert sc.comparable and sc.base_version == sc.current_version == V
    assert [x.slug for x in sc.accelerated.items] == ["alpha"] and sc.accelerated.items[0].delta == 10
    assert [x.slug for x in sc.weakened.items] == ["beta"] and sc.weakened.items[0].delta == -8
    assert sc.accelerated.count == 1 and sc.weakened.count == 1
    assert sc.compared == 3 and sc.unchanged == 1  # alpha, beta, gamma
    assert sc.not_reobserved == 1  # delta
    assert sc.not_comparable == 1  # eps (reconstructed baseline)
    assert sc.no_baseline == 1  # newco
    assert sc.accelerated.items[0].components_delta["technical_momentum"] == 4.0


def test_momentum_and_collisions(world, db):
    r = changes.compare(db)
    assert [x.slug for x in r.momentum_acceleration.items] == ["alpha"]
    started = {x.slug: x for x in r.new_collisions.items}
    assert set(started) == {"alpha", "newco"} and r.new_collisions.count == 2
    assert started["newco"].new_entity and started["newco"].current_strength == "HIGH"
    assert started["newco"] == r.new_collisions.items[0]  # HIGH first
    assert started["alpha"].base_strength == "LOW" and started["alpha"].current_strength == "MEDIUM"
    assert [x.slug for x in r.collisions_ended.items] == ["beta"]


def test_formation_statuses(world, db):
    f = changes.compare(db).formation
    assert f.available and (f.base_day, f.current_day) == (B, C)
    assert f.counts == {"NEW": 1, "DROPPED": 1, "STRENGTHENED": 1, "WEAKENED": 1, "UNCHANGED": 1}
    assert [x.slug for x in f.new] == ["team-a5"]
    assert [x.slug for x in f.dropped] == ["team-a4"] and f.dropped[0].reason
    assert f.strengthened[0].slug == "team-alpha" and f.strengthened[0].current_tier == "emerging"
    assert f.weakened[0].slug == "team-a2" and f.weakened[0].current_score == 55


def test_meet_entered_left_moved(world, db):
    m = changes.compare(db).meet
    assert m.available and (m.base_week, m.current_week) == ("2026-W38", "2026-W41")
    assert [x.person.id for x in m.entered] == [str(world["p4"])]
    assert [x.person.id for x in m.left] == [str(world["p3"])]
    moved = {x.person.id: x.change for x in m.moved}
    assert moved == {str(world["p2"]): 1, str(world["p1"]): -1}


def test_caveats_explain_cohort_breadth_and_version(world, db):
    cav = " ".join(changes.compare(db).caveats)
    assert "cohort-relative" in cav and "new to Radar" in cav and V in cav and "not re-observed" in cav


def test_version_mismatch_disables_score_comparison(world, db):
    from sqlalchemy import update

    db.execute(update(ScoreSnapshot).where(ScoreSnapshot.day == C).values(version="2026.11-1"))
    db.commit()
    memo.clear()
    sc = changes.compare(db).score_changes
    assert not sc.comparable and "2026.10-1 → 2026.11-1" in sc.reason
    assert sc.compared == 0 and sc.accelerated.count == 0 and sc.weakened.count == 0


def test_company_since_snapshot(world, db):
    s = changes.company_since(db, world["alpha"])
    assert s.comparable and s.delta == 10 and (s.base_day, s.current_day) == (B, C)
    d = changes.company_since(db, world["delta"])
    assert not d.comparable and d.delta is None and "re-observed" in d.reason
    e = changes.company_since(db, world["eps"])
    assert not e.comparable and "reconstructed" in e.reason
    n = changes.company_since(db, world["newco"])
    assert not n.comparable and "new to Radar" in n.reason and n.current_score == 70


def test_api_changes_points_detail_and_formation_badge(world, client):
    r = client.get("/api/changes")
    assert r.status_code == 200
    body = r.json()
    assert body["base_day"] == "2026-09-20" and body["score_changes"]["weakened"]["items"][0]["slug"] == "beta"
    assert client.get("/api/changes", params={"base": "2026-09-20", "current": "2026-10-06"}).json() == body
    assert client.get("/api/changes", params={"base": "2026-10-06", "current": "2026-09-20"}).status_code == 422
    assert client.get("/api/changes", params={"current": "2026-09-20"}).status_code == 422  # nothing before it
    pts = client.get("/api/changes/points").json()
    assert [p["as_of"] for p in pts["points"]] == ["2026-09-20", "2026-10-06"]
    detail = client.get("/api/companies/alpha").json()
    assert detail["since_snapshot"]["delta"] == 10 and detail["since_snapshot"]["comparable"]
    feed = client.get("/api/formation").json()
    card = next(c for c in feed["emerging"] if c["slug"] == "team-alpha")
    assert card["change"] == {"status": "STRENGTHENED", "base_day": "2026-09-20", "base_score": 45.0,
                              "base_tier": "early", "current_score": 52.0, "threshold": 5.0}
    fd = client.get("/api/formation/team-alpha").json()
    assert fd["card"]["change"]["status"] == "STRENGTHENED"
    pd = client.get("/api/founders/newp").json()
    assert pd["since_snapshot"]["comparable"] is False


def test_single_point_is_unavailable(db):
    memo.clear()
    db.add(IngestionRun(provider="snapshot", status="ok", params={"as_of": C.isoformat()}))
    db.commit()
    r = changes.compare(db)
    assert not r.available and "Only one snapshot" in r.reason
    assert changes.company_since(db, uuid.uuid4()) is None and changes.formation_changes(db) == {}
