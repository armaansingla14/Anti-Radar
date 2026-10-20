"""Who Should We Meet (services/meet.py, /api/meet) and the meeting-brief endpoint, on direct fixtures.

People, score snapshots, signals and formation candidates are inserted directly so these tests pin
the ranking contract (order, penalties, diversity, exclusions, week stability) independently of
the scoring pipeline. TODAY_OVERRIDE=2026-10-06 (tests/conftest.py) → ISO week 2026-W41.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from radar.models import (
    Company,
    FormationCandidate,
    MeetingRecommendation,
    Person,
    RepoContributor,
    Repository,
    ScoreSnapshot,
    Signal,
    UserFeedback,
)
from radar.services import meet

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)
DAY = date(2026, 10, 6)
WEEK = "2026-W41"


def comps(*, tech: float = 10, thesis: float = 5, gap: float = 2, evidence: list[Signal] | None = None,
          theme: str = "robotics", topic: str = "robotics.manipulation") -> dict:
    ev = [{"title": s.title, "signal_id": str(s.id), "source_url": s.source_url, "source_type": s.source_type,
           "event_at": s.event_at.isoformat()} for s in (evidence or [])]
    return {
        "technical_output": {"key": "technical_output", "label": "Technical Output", "value": tech, "max": 25,
                             "explanation": f"Technical output measured ({tech}).", "raw": {}, "evidence": ev},
        "research": {"key": "research", "label": "Research", "value": 0, "max": 20, "explanation": "No papers.",
                     "raw": {}, "evidence": []},
        "pedigree": {"key": "pedigree", "label": "Pedigree", "value": 0, "max": 15, "explanation": "none",
                     "raw": {}, "evidence": []},
        "formation": {"key": "formation", "label": "Formation Signal", "value": 0, "max": 20, "explanation": "",
                      "raw": {}, "evidence": []},
        "thesis_fit": {"key": "thesis_fit", "label": "Thesis Fit", "value": thesis, "max": 10,
                       "explanation": f"Best fit: Robotics (match {thesis / 10:.2f}).",
                       "raw": {"theme": theme, "topic": topic}, "evidence": []},
        "attention_gap": {"key": "attention_gap", "label": "Attention Gap", "value": gap, "max": 10,
                          "explanation": "gap", "raw": {}, "evidence": []},
    }


def add_person(s, handle: str, score: float, *, followers: int = 10, dismissed: bool = False, name: str | None = None,
               families: tuple[str, ...] = ("github",), days_ago: int = 3, company: str | None = None,
               evidence_signals: bool = True, **kw) -> Person:
    p = Person(slug=handle.lower(), name=name or handle.title(), github_handle=handle, followers=followers,
               dismissed=dismissed, current_company=company, attrs={"self_reported": True,
                                                                    "self_reported_fields": ["company"]})
    s.add(p)
    s.flush()
    sigs = []
    if evidence_signals:
        for i, fam in enumerate(families):
            st = {"github": "github", "research": "arxiv", "community": "hackernews", "web": "website"}[fam]
            stype = {"github": "repo_created", "research": "paper_published", "community": "hn_show",
                     "web": "formation_language"}[fam]
            sigs.append(Signal(entity_id=p.id, entity_type="person", signal_type=stype, source_type=st,
                               source_url=f"https://example.org/{handle}/{fam}", title=f"{handle} {fam} event",
                               event_at=NOW - timedelta(days=days_ago + i), dedup_key=uuid.uuid4().hex))
        s.add_all(sigs)
        s.flush()
    s.add(ScoreSnapshot(entity_type="person", entity_id=p.id, day=DAY, score=score, version="test",
                        components=comps(evidence=sigs[:1], **kw), attention={"flagged": False}, collision={}))
    s.flush()
    return p


def add_candidate(s, slug: str, members: list[Person], score: float, *, anchor: Company | None = None
                  ) -> FormationCandidate:
    if anchor is None:
        anchor = Company(slug=slug, name=slug.title(), kind="project", github_org=slug)
        s.add(anchor)
        s.flush()
    c = FormationCandidate(
        slug=f"team-{slug}", kind="team", anchor_type="company", anchor_id=anchor.id,
        title=f"Formation signals around {anchor.name}", theme="robotics", subtopic="robotics.manipulation",
        member_ids=[m.id for m in members], score=score, tier="emerging" if score >= 45 else "early",
        label="FORMATION SIGNALS DETECTED", components={}, convergence={}, signal_families=["github"],
        independent_signals=2, first_detected_at=NOW - timedelta(days=2), latest_activity_at=NOW,
        why_now=[{"title": f"GitHub org {slug} created", "source_url": f"https://github.com/{slug}",
                  "source_type": "github", "event_at": (NOW - timedelta(days=9)).isoformat()}],
        day=DAY)
    s.add(c)
    s.flush()
    return c


@pytest.fixture()
def world(db):
    s = db
    ada = add_person(s, "ada", 70, families=("github", "research", "community"))
    ben = add_person(s, "ben", 60, families=("github", "research"))
    cat = add_person(s, "cat", 58, families=("github", "research"))  # same team as ada/ben → cut by diversity
    team = add_candidate(s, "gripco", [ada, ben, cat], 70)
    famous = add_person(s, "famous", 90, followers=50_000, families=("github", "research"))
    star = add_person(s, "star", 40, followers=60_000, families=("github",))
    add_candidate(s, "starlab", [star], 65)  # exceptional formation waives the obviousness penalty
    gone = add_person(s, "gone", 99, dismissed=True, families=("github", "research", "community"))
    bot = add_person(s, "claude", 95, name="Claude", families=("github", "research"))
    quiet = add_person(s, "quiet", 95, evidence_signals=False)
    brand_co = Company(slug="makerspet", name="Maker's Pet", kind="company", github_org="makerspet")
    s.add(brand_co)
    s.flush()
    brand = add_person(s, "makers-pet", 95, name="Maker's Pet", families=("github", "research"))
    liked = add_person(s, "liked", 30, families=("github",))
    plain = add_person(s, "plain", 30, families=("github",))
    s.add(UserFeedback(entity_type="person", entity_id=liked.id, label="strong_founder"))
    s.commit()
    return {k: v.id for k, v in dict(ada=ada, ben=ben, cat=cat, famous=famous, star=star, gone=gone, bot=bot,
                                      quiet=quiet, brand=brand, liked=liked, plain=plain, team=team).items()}


def _handles(feed: dict) -> list[str]:
    return [c["person"]["github_handle"] for c in feed["people"]]


# ── ranking ──────────────────────────────────────────────────────────────────


def test_week_ranking_order_penalties_diversity_and_exclusions(world, client):
    r = client.get("/api/meet")
    assert r.status_code == 200, r.text
    feed = r.json()
    assert feed["week"] == WEEK and feed["as_of"] == "2026-10-06"
    hs = _handles(feed)
    assert hs == ["ada", "ben", "famous", "star", "liked"]
    # obviousness: 50k followers costs 25 points (famous would rank 1st without it); a 60k-follower person
    # with an exceptional formation score (>= 60) is exempt. Founder Score leads the formula, so "famous"
    # still edges out "star", an unverified single-family person whose only weight is formation membership.
    # diversity: at most two people from the same formation candidate
    assert "cat" not in hs
    # dismissed, bots / AI agents, brand accounts and people without dated evidence never appear
    assert not {"gone", "claude", "makers-pet", "quiet"} & set(hs)
    top = feed["people"][0]
    assert top["rank"] == 1 and top["confidence"] == "HIGH CONFIDENCE"
    assert top["formation"]["slug"] == "team-gripco" and top["formation"]["label"] == "FORMATION SIGNALS DETECTED"
    assert top["evidence_count"] == 3 and len(top["why_now"]) >= 3
    assert top["affiliation_line"] == "Affiliation unknown"
    assert top["area"].startswith("Robotics")
    assert any("Founder Score 70/100" in w for w in top["why_person"])
    assert not any("founder of" in w.lower() for w in top["why_person"])  # no founder relation recorded
    by = {c["person"]["github_handle"]: c for c in feed["people"]}
    assert by["ben"]["confidence"] == "MEDIUM CONFIDENCE" and by["liked"]["confidence"] == "EARLY SIGNAL"
    assert not any("Obviousness penalty" in w for w in by["star"]["why_person"])
    assert by["ada"]["priority"] == "HIGH" and by["ben"]["priority"] == "MEDIUM"
    # unknown (no verified pedigree, Founder Score < 45, single family) → WATCH whatever the score
    assert by["star"]["priority"] == by["liked"]["priority"] == by["famous"]["priority"] == "WATCH"
    assert by["ada"]["score"] > by["ben"]["score"] > by["star"]["score"]


def test_obviousness_penalty_features(world, db):
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["famous"].features["obviousness"] == -meet.OBVIOUS_PENALTY and rows["famous"].obvious
    assert rows["star"].features["obviousness"] == 0 and rows["star"].obvious is None
    assert rows["famous"].score + meet.OBVIOUS_PENALTY > rows["star"].score


def test_feedback_boost_and_penalty(world, db):
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["liked"].features["feedback"] == 6 and rows["liked"].score > rows["plain"].score
    db.add(UserFeedback(entity_type="person", entity_id=world["liked"], label="too_obvious"))
    db.add(UserFeedback(entity_type="company", entity_id=db.get(FormationCandidate, world["team"]).anchor_id,
                        label="weak_team"))
    db.flush()
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["liked"].features["feedback"] == 0
    assert rows["ada"].features["feedback"] == -6  # weak_team on their formation anchor


def test_week_is_stable_until_refresh_and_history(world, client, db):
    first = client.get("/api/meet").json()
    # scores move after the week was stored: the week view does not reshuffle
    snap = db.scalar(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == world["cat"]))
    snap.score = 100
    db.commit()
    second = client.get("/api/meet?week=current").json()
    assert _handles(second) == _handles(first) and second["generated_at"] == first["generated_at"]
    assert db.query(MeetingRecommendation).filter_by(week=WEEK).count() == 5
    assert second["history_weeks"] == [WEEK]
    # explicit week path reads the same rows; an unknown past week is empty and never computed
    assert _handles(client.get(f"/api/meet?week={WEEK}").json()) == _handles(first)
    past = client.get("/api/meet?week=2026-W30").json()
    assert past["people"] == [] and past["generated_at"] is None
    assert client.get("/api/meet?week=2026-W99").status_code == 422
    assert client.get("/api/meet?week=last").status_code == 422
    # refresh recomputes the current week: cat (now 100) outranks ben within the team cap
    r = client.post("/api/meet/refresh")
    assert r.status_code == 200
    hs = _handles(r.json())
    assert "cat" in hs and "ben" not in hs


def test_dismissed_after_ranking_is_hidden(world, client, db):
    assert "ben" in _handles(client.get("/api/meet").json())
    db.get(Person, world["ben"]).dismissed = True
    db.commit()
    feed = client.get("/api/meet").json()
    assert "ben" not in _handles(feed)
    assert [c["rank"] for c in feed["people"]] == list(range(1, len(feed["people"]) + 1))


def test_compute_week_is_idempotent(world, db):
    a = meet.compute_week(db, WEEK)
    b = meet.compute_week(db, WEEK)
    assert [r.id for r in a] == [r.id for r in b]
    c = meet.compute_week(db, WEEK, force=True)
    assert [r.person_id for r in c] == [r.person_id for r in a] and {r.id for r in c}.isdisjoint({r.id for r in a})


# ── meeting brief endpoint ───────────────────────────────────────────────────


@pytest.fixture()
def builder(db):
    org = Company(slug="gripco", name="GripCo", kind="project", github_org="gripco")
    p = Person(slug="ada", name="Ada Lovelace", github_handle="ada", followers=12, current_company="GripCo",
               attrs={"self_reported": True, "self_reported_fields": ["company"]})
    db.add_all([org, p])
    db.flush()
    repo = Repository(full_name="gripco/hand", owner="gripco", owner_type="Organization", company_id=org.id,
                      description="Tactile hand", stars=120, gh_created_at=NOW - timedelta(days=20))
    db.add(repo)
    db.flush()
    db.add(RepoContributor(repository_id=repo.id, person_id=p.id, contributions=40))
    db.add(Signal(entity_id=org.id, entity_type="company", signal_type="org_created", source_type="github",
                  source_url="https://github.com/gripco", title="GitHub org gripco created",
                  event_at=NOW - timedelta(days=21), dedup_key=uuid.uuid4().hex))
    sig = Signal(entity_id=p.id, entity_type="person", signal_type="repo_created", source_type="github",
                 source_url="https://github.com/gripco/hand", title="Repository gripco/hand created",
                 event_at=NOW - timedelta(days=20), dedup_key=uuid.uuid4().hex)
    db.add(sig)
    db.flush()
    db.add(ScoreSnapshot(entity_type="person", entity_id=p.id, day=DAY, score=55, version="test",
                         components=comps(evidence=[sig]), attention={}, collision={}))
    add_candidate(db, "gripco", [p], 50, anchor=org)
    db.commit()
    return p.id


def test_meeting_brief_endpoint_cache_force_and_card_link(builder, client):
    r = client.post("/api/meeting-brief", json={"person_id": str(builder)})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["kind"] == "meeting_brief" and rep["entity_type"] == "person" and rep["generator"] == "template"
    assert len(rep["body"]["questions"]) == 5 and rep["sources"]
    assert rep["markdown"].startswith("# Meeting Brief — Ada Lovelace")
    again = client.post("/api/meeting-brief", json={"person_id": "ada"}).json()  # slug works; cached
    assert again["id"] == rep["id"]
    forced = client.post("/api/meeting-brief", json={"person_id": str(builder), "force": True}).json()
    assert forced["id"] != rep["id"]
    assert client.get(f"/api/reports/{forced['id']}").json()["kind"] == "meeting_brief"
    card = client.get("/api/meet").json()["people"][0]
    assert card["person"]["id"] == str(builder) and card["brief_report_id"] == forced["id"]
    assert client.post("/api/meeting-brief", json={"person_id": "nobody"}).status_code == 404


def test_meeting_brief_rate_limited(builder, client):
    codes = [client.post("/api/meeting-brief", json={"person_id": str(builder)}).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_meet_writes_need_admin_when_token_set(world, client, monkeypatch):
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "admin_token", "s3cret")
    assert client.post("/api/meet/refresh").status_code == 403
    assert client.post("/api/meeting-brief", json={"person_id": str(world["ada"])}).status_code == 403
    assert client.get("/api/meet").status_code == 200
    assert client.post("/api/meet/refresh", headers={"X-Radar-Admin": "s3cret"}).status_code == 200


# ── talent pool ──────────────────────────────────────────────────────────────


def test_talent_pool_person_with_new_project_outranks_unknown_contributor(db, client):
    talent = add_person(db, "kira", 45, families=("github",), days_ago=5)
    unknown = add_person(db, "uno", 45, families=("github",), days_ago=5)
    talent.attrs = {**talent.attrs, "lab_contributions": [
        {"org": "google-deepmind", "label": "Google DeepMind", "tier": 1, "repo": "mujoco", "commits": 412,
         "first_commit_at": "2024-02-01T00:00:00+00:00", "last_commit_at": "2025-07-01T00:00:00+00:00"}]}
    for p in (talent, unknown):
        db.add(Signal(entity_id=p.id, entity_type="person", signal_type="new_project", source_type="github",
                      source_url=f"https://github.com/{p.github_handle}/tactile-sim",
                      title=f"{p.github_handle} created {p.github_handle}/tactile-sim (new repository)",
                      event_at=NOW - timedelta(days=12), dedup_key=uuid.uuid4().hex,
                      meta={"repo": f"{p.github_handle}/tactile-sim", "stars": 25, "topics": ["robotics"]}))
        snap = db.scalar(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == p.id))
        c = dict(snap.components)
        c["formation"] = {**c["formation"], "raw": {"new_projects": [f"{p.github_handle}/tactile-sim"]}}
        snap.components = c
    db.commit()
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    # 25 stars = traction: the event counts at its quality strength (0.5..1 by stars)
    from radar.scoring.extract import assess_new_project

    q = assess_new_project("kira/tactile-sim", stars=25)
    assert q.counts and q.traction
    assert rows["kira"].features["talent"] == pytest.approx(meet.TALENT_POINTS * q.strength, abs=0.01)
    assert rows["uno"].features["talent"] == 0
    assert rows["kira"].score > rows["uno"].score
    feed = client.get("/api/meet").json()
    hs = _handles(feed)
    assert hs.index("kira") < hs.index("uno")
    card = feed["people"][hs.index("kira")]
    assert card["why_person"][0] == "412 commits to google-deepmind/mujoco (tier-1 lab repository, verified)"
    assert card["why_now"][0]["title"] == "kira created kira/tactile-sim (new repository)"
    assert card["person"]["headline"] == "Contributor to google-deepmind/mujoco · now building kira/tactile-sim"
    assert card["person"]["why_now"][0].startswith("Now building kira/tactile-sim")
    assert any("new_project" not in w and "kira created" in w for w in card["what_changed"])
    other = feed["people"][hs.index("uno")]
    assert other["why_person"][0].startswith("Founder Score") and "Contributor to" not in (
        other["person"]["headline"] or "")


# ── M7 rebalancing on real-data shapes (Oct 20) ──────────────────────────────


def _lab(p: Person, org: str, repo: str, commits: int) -> None:
    p.attrs = {**(p.attrs or {}), "lab_contributions": [
        {"org": org, "label": org, "tier": 1, "repo": repo, "commits": commits,
         "first_commit_at": "2024-02-01T00:00:00+00:00", "last_commit_at": "2026-07-01T00:00:00+00:00"}]}


def test_cofounder_of_an_already_public_company_gets_the_obviousness_penalty(db):
    kai = add_person(db, "kai", 62, families=("github",), followers=4000)
    kai.bio = "Core maintainer of @vllm-project. Co-Founder & Chief Scientist @Inferact ."
    _lab(kai, "vllm-project", "vllm", 471)
    peer = add_person(db, "peer", 62, families=("github",), followers=4000)
    peer.bio = "Co-founder @novaco"
    _lab(peer, "vllm-project", "vllm", 471)
    # Organization creation dates come from observed public memberships (anyone's).
    for p, login, created in ((kai, "Inferact", "2025-09-01T00:00:00Z"), (peer, "novaco", "2026-09-01T00:00:00Z")):
        p.attrs = {**p.attrs, "github_orgs": [{"login": login, "created_at": created}]}
    db.commit()
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["kai"].features["obviousness"] == -meet.OBVIOUS_PENALTY
    assert "founder of Inferact" in rows["kai"].obvious and "already public" in rows["kai"].obvious
    assert rows["peer"].features["obviousness"] == 0 and rows["peer"].score > rows["kai"].score
    assert any("Obviousness penalty" in w for w in meet.why_person(rows["kai"], []))


def test_young_org_member_employed_elsewhere_gets_half_the_event_strength(db):
    roger = add_person(db, "roger", 50, families=("github",), company="inferact")
    yin = add_person(db, "yin", 50, families=("github",))
    gw = Company(slug="smg", name="smg-project", kind="project", github_org="smg-project",
                 attrs={"gh_org_created_at": "2026-06-04T21:39:40Z"})
    db.add(gw)
    db.flush()
    for p, commits in ((roger, 3), (yin, 3)):
        _lab(p, "vllm-project", "vllm", 214)
        db.add(Signal(entity_id=p.id, entity_type="person", signal_type="joined_young_org", source_type="github",
                      source_url="https://github.com/smg-project", title=f"{p.github_handle} is active in smg-project",
                      event_at=NOW - timedelta(days=124), dedup_key=uuid.uuid4().hex,
                      meta={"org": "smg-project", "commits": commits}))
    roger.attrs = {**roger.attrs, "github_orgs": [{"login": "Inferact", "created_at": "2025-10-20T16:32:38Z"},
                                                  {"login": "smg-project", "created_at": "2026-06-04T21:39:40Z"}]}
    db.commit()
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["roger"].elsewhere and rows["roger"].elsewhere[0] == "Inferact"
    assert rows["yin"].elsewhere is None
    assert rows["roger"].features["talent"] == pytest.approx(rows["yin"].features["talent"] * meet.ELSEWHERE_SHARE,
                                                             abs=0.02)
    why = meet.why_person(rows["roger"], [])
    assert any(w.startswith("Profile lists Inferact as current company (self-reported)") for w in why)
    assert rows["yin"].score > rows["roger"].score


def test_unknown_single_family_contributor_is_watch_and_below_verified_talent(db):
    unknown = add_person(db, "maya", 30, families=("github",), days_ago=1)
    talent = add_person(db, "zakka", 49, families=("github",), days_ago=40)
    _lab(talent, "google-deepmind", "mujoco", 123)
    add_candidate(db, "deeplethe", [unknown], 35)  # single-family candidate (signal_families=["github"])
    db.commit()
    rows = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}
    assert rows["maya"].features["unverified"] == -meet.UNVERIFIED_PENALTY
    assert rows["maya"].gate is None and rows["maya"].priority == "WATCH"
    assert rows["zakka"].features["unverified"] == 0 and rows["zakka"].gate.startswith("verified commits")
    assert rows["zakka"].score > rows["maya"].score
    # A member of a formation candidate with >= 2 families is not "unverified" (breadth gate).
    fc = db.scalar(select(FormationCandidate).where(FormationCandidate.slug == "team-deeplethe"))
    fc.signal_families = ["github", "research"]
    db.commit()
    row = {r.person.github_handle: r for r in meet.rank_people(db, limit=20)}["maya"]
    assert row.features["unverified"] == 0 and row.gate == "formation candidate with 2 source families"
