"""Formation radar API (/api/formation) and relationship-backed founder graph, on small direct fixtures.

Candidates and relationships are inserted directly (not computed) so these tests pin the
presentation contract independently of scoring/formation.py and pipeline/relationships.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event

from radar.db import engine, session_scope
from radar.models import (
    Company,
    CompanyPerson,
    FormationCandidate,
    Person,
    Relationship,
    RepoContributor,
    Repository,
    Signal,
)

NOW = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)  # TODAY_OVERRIDE in tests/conftest.py


def _ev(title: str, url: str, days_ago: int = 10) -> dict:
    return {"title": title, "detail": None, "source_url": url, "source_type": "github",
            "event_at": (NOW - timedelta(days=days_ago)).isoformat()}


def _comp(key: str, value: float, mx: float, url: str) -> dict:
    return {"key": key, "label": key.replace("_", " ").title(), "value": value, "max": mx,
            "explanation": f"{key} measured", "raw": {"n": 1}, "evidence": [_ev(f"{key} evidence", url)]}


def _rel(a: Person | Company | Repository, at: str, b, bt: str, kind: str, epistemic: str = "VERIFIED",
         url: str = "https://github.com/x", days_ago: int = 400, weight: float = 1.0) -> Relationship:
    src, dst, st, dt = a, b, at, bt
    if at == bt == "person" and str(b.id) < str(a.id):
        src, dst, st, dt = b, a, bt, at
    return Relationship(
        src_type=st, src_id=src.id, dst_type=dt, dst_id=dst.id, kind=kind, weight=weight, epistemic=epistemic,
        first_seen_at=NOW - timedelta(days=days_ago), last_seen_at=NOW - timedelta(days=days_ago // 2),
        evidence=[{"title": f"{kind} evidence", "detail": "raw strings kept", "source_url": url,
                   "source_type": "github", "event_at": (NOW - timedelta(days=days_ago)).isoformat()}],
        is_demo=False)


def _signal(entity, etype: str, title: str, days_ago: int, url: str, stype: str = "org_created") -> Signal:
    return Signal(entity_id=entity.id, entity_type=etype, signal_type=stype, source_type="github", source_url=url,
                  title=title, event_at=NOW - timedelta(days=days_ago), dedup_key=uuid.uuid4().hex,
                  epistemic="FACT", is_demo=False)


def _candidate(slug: str, anchor, anchor_type: str, members: list[Person], *, score: float, tier: str,
               kind: str = "team", label: str = "EMERGING TEAM", theme: str | None = "robotics",
               subtopic: str | None = "robotics.tactile", is_demo: bool = False,
               rel_ids: list[str] | None = None, conv: float = 0.7) -> FormationCandidate:
    return FormationCandidate(
        slug=slug, kind=kind, anchor_type=anchor_type, anchor_id=anchor.id, title=slug.replace("-", " ").title(),
        theme=theme, subtopic=subtopic, member_ids=[m.id for m in members], score=score, tier=tier, label=label,
        components={
            "novelty": _comp("novelty", 6, 10, "https://github.com/n"),
            "founder_quality": _comp("founder_quality", 20, 25, "https://github.com/fq"),
            "team_convergence": _comp("team_convergence", 18, 25, "https://github.com/tc"),
            "formation_evidence": _comp("formation_evidence", 15, 20, "https://github.com/fe"),
            "technical_activity": _comp("technical_activity", 12, 20, "https://github.com/ta"),
        },
        convergence={"score": conv, "explanation": "2 prior relationships; first shared org.",
                     "relationship_ids": rel_ids or [], "prior_relationships": 2, "families": ["github", "arxiv"]},
        signal_families=["github", "arxiv"], independent_signals=3,
        first_detected_at=NOW - timedelta(days=5), latest_activity_at=NOW - timedelta(days=1),
        why_now=[_ev("GitHub org created 12 days ago", "https://github.com/acme-robo", 12)],
        is_demo=is_demo, day=date(2026, 10, 6))


@pytest.fixture()
def world(db):
    s = db
    org = Company(slug="acme-robo", name="acme-robo", kind="project", github_org="acme-robo")
    solo_anchor = Person(slug="solo", name="Solo Builder", github_handle="solo",
                         prior_affiliations=["Google DeepMind"])
    a = Person(slug="ana", name="Ana Ruiz", github_handle="ana", prior_affiliations=["Tesla", "NVIDIA Corp"],
               current_company="@acme-robo")
    b = Person(slug="ben", name="Ben Ko", github_handle="ben", prior_affiliations=["NVIDIA"],
               attrs={"affiliations_normalized": {"current_company": None, "prior_affiliations": ["NVIDIA", "SpaceX"]}})
    outsider = Person(slug="olaf", name="Olaf Out", github_handle="olaf")
    demo_co = Company(slug="demo-co", name="Demo Co", is_demo=True)
    s.add_all([org, solo_anchor, a, b, outsider, demo_co])
    s.flush()
    repo = Repository(full_name="acme-robo/hand", owner="acme-robo", owner_type="Organization", company_id=org.id,
                      stars=40, gh_created_at=NOW - timedelta(days=12))
    other_repo = Repository(full_name="olaf/misc", owner="olaf", owner_type="User", stars=3)
    s.add_all([repo, other_repo])
    s.flush()
    s.add_all([RepoContributor(repository_id=repo.id, person_id=a.id, contributions=30),
               RepoContributor(repository_id=repo.id, person_id=b.id, contributions=12),
               RepoContributor(repository_id=other_repo.id, person_id=outsider.id, contributions=5)])
    rels = [
        _rel(a, "person", b, "person", "COAUTHORED_WITH", url="https://arxiv.org/abs/2401.00001"),
        _rel(a, "person", b, "person", "WORKED_WITH", epistemic="INFERRED", url="https://github.com/ana"),
        _rel(a, "person", org, "company", "MEMBER_OF", url="https://github.com/acme-robo", days_ago=12),
        _rel(b, "person", repo, "repository", "CONTRIBUTED_TO", url="https://github.com/acme-robo/hand", days_ago=10),
        _rel(a, "person", outsider, "person", "WORKED_WITH", epistemic="INFERRED", url="https://github.com/olaf"),
        _rel(outsider, "person", other_repo, "repository", "CONTRIBUTED_TO", url="https://github.com/olaf/misc"),
    ]
    s.add_all(rels)
    s.add_all([
        _signal(org, "company", "GitHub org acme-robo created", 12, "https://github.com/acme-robo"),
        _signal(a, "person", "Ana changed bio", 20, "https://github.com/ana", "bio_change"),
        _signal(repo, "repository", "Repo acme-robo/hand created", 11, "https://github.com/acme-robo/hand",
                "repo_created"),
        _signal(b, "person", "Ben old paper", 400, "https://arxiv.org/abs/old", "paper_published"),
        _signal(outsider, "person", "Olaf unrelated", 3, "https://github.com/olaf", "bio_change"),
    ])
    s.flush()
    team = _candidate("acme-robo-team", org, "company", [a, b], score=81.4, tier="high",
                      rel_ids=[str(rels[0].id)], conv=0.72)
    solo = _candidate("solo-builder", solo_anchor, "person", [solo_anchor], score=48, tier="emerging", kind="person",
                      label="FORMATION SIGNALS DETECTED", theme="energy", subtopic=None, conv=0.0)
    early = _candidate("early-one", outsider, "person", [outsider], score=31, tier="early", kind="person",
                       label="FORMATION SIGNALS DETECTED", theme=None, subtopic=None, conv=0.1)
    demo = _candidate("demo-team", demo_co, "company", [], score=99, tier="high", is_demo=True)
    s.add_all([team, solo, early, demo])
    s.commit()
    return {"team": team.id, "solo": solo.id, "org": org.id, "a": a.id, "b": b.id, "outsider": outsider.id,
            "repo": repo.id, "rels": [r.id for r in rels]}


# ── feed ─────────────────────────────────────────────────────────────────────


def test_feed_groups_tiers_and_hides_demo_by_default(world, client):
    r = client.get("/api/formation")
    assert r.status_code == 200
    f = r.json()
    assert f["as_of"] == "2026-10-06" and f["is_snapshot"] is False
    assert f["counts"] == {"high": 1, "emerging": 1, "early": 1, "total": 3}
    assert [c["slug"] for c in f["high"]] == ["acme-robo-team"]
    assert [c["slug"] for c in f["emerging"]] == ["solo-builder"]
    assert [c["slug"] for c in f["early"]] == ["early-one"]

    card = f["high"][0]
    assert card["score"] == 81 and card["label"] == "EMERGING TEAM" and card["kind"] == "team"
    assert card["theme_label"] == "Robotics" and card["subtopic_label"]
    # stable component order regardless of JSONB key order, evidence attached
    assert [c["key"] for c in card["components"]] == ["founder_quality", "team_convergence", "technical_activity",
                                                      "formation_evidence", "novelty"]
    assert all(c["evidence"] and c["explanation"] for c in card["components"])
    assert {m["name"] for m in card["members"]} == {"Ana Ruiz", "Ben Ko"}
    ana = next(m for m in card["members"] if m["slug"] == "ana")
    assert ana["affiliations"][0] == "acme-robo" and "Tesla" in ana["affiliations"]
    # union of normalized prior orgs (Ben's resolver-normalized list wins over his raw strings)
    prev = card["previous_affiliations"]
    assert "Tesla" in prev and "NVIDIA" in prev and "SpaceX" in prev
    assert len(prev) == len(set(prev)) and "NVIDIA Corp" not in prev
    assert card["previous_affiliations_status"] == "INFERRED"
    assert card["previous_affiliations_note"] == "Self-reported on GitHub profile"
    assert card["anchor"] == {"type": "company", "id": str(world["org"]), "slug": "acme-robo", "name": "acme-robo"}
    assert card["independent_signals"] == 3 and card["families"] == ["github", "arxiv"]
    assert card["why_now"][0]["source_url"] == "https://github.com/acme-robo"
    assert card["data_age"] is not None and card["is_demo"] is False

    solo = f["emerging"][0]
    assert solo["kind"] == "person" and solo["anchor"]["type"] == "person" and solo["subtopic_label"] is None
    assert "founded" not in str(f).lower()


def test_feed_filters_and_validation(world, client):
    demo = client.get("/api/formation", params={"include_demo": "true"}).json()
    assert demo["counts"]["high"] == 2 and demo["high"][0]["slug"] == "demo-team" and demo["high"][0]["is_demo"]
    only = client.get("/api/formation", params={"tier": "emerging"}).json()
    assert only["high"] == [] and only["early"] == [] and len(only["emerging"]) == 1
    assert only["counts"]["total"] == 3  # counts ignore the tier filter
    rob = client.get("/api/formation", params={"theme": "robotics"}).json()
    assert rob["counts"] == {"high": 1, "emerging": 0, "early": 0, "total": 1}
    sub = client.get("/api/formation", params={"theme": "robotics.tactile"}).json()
    assert sub["counts"]["total"] == 1
    assert client.get("/api/formation", params={"theme": "astrology"}).status_code == 422
    assert client.get("/api/formation", params={"tier": "legendary"}).status_code == 422


def test_feed_is_batched(world, client):
    """Query count does not grow with the number of candidates (no N+1)."""
    def count() -> int:
        n = 0

        def on(*_a, **_k):
            nonlocal n
            n += 1
        event.listen(engine(), "before_cursor_execute", on)
        try:
            assert client.get("/api/formation").status_code == 200
        finally:
            event.remove(engine(), "before_cursor_execute", on)
        return n

    base = count()
    with session_scope() as s:
        for i in range(12):
            p = Person(slug=f"extra-{i}", name=f"Extra {i}", prior_affiliations=["MIT"])
            s.add(p)
            s.flush()
            s.add(_candidate(f"extra-{i}", p, "person", [p], score=40 + i, tier="emerging", kind="person"))
    assert count() <= base + 1


# ── detail ───────────────────────────────────────────────────────────────────


def test_detail_report(world, client):
    r = client.get("/api/formation/acme-robo-team")
    assert r.status_code == 200
    d = r.json()
    assert d["card"]["slug"] == "acme-robo-team" and d["card"]["data_age"]["families"]
    by_id = client.get(f"/api/formation/{world['team']}").json()
    assert by_id["card"]["id"] == d["card"]["id"]

    conv = d["convergence"]
    assert conv["score"] == 0.72 and conv["strength"] == "HIGH" and conv["prior_relationships"] == 2

    kinds = [(x["kind"], x["src"]["name"], x["dst"]["name"]) for x in d["relationships"]]
    assert ("MEMBER_OF", "Ana Ruiz", "acme-robo") in kinds
    assert ("CONTRIBUTED_TO", "Ben Ko", "acme-robo/hand") in kinds
    assert {"COAUTHORED_WITH", "WORKED_WITH"} <= {k for k, *_ in kinds}
    assert all("Olaf Out" not in (s, t) for _, s, t in kinds)  # outsider ties are not part of the report
    assert kinds[0][0] == "MEMBER_OF"  # structural ties first
    worked = next(x for x in d["relationships"] if x["kind"] == "WORKED_WITH")
    assert worked["epistemic"] == "INFERRED" and worked["evidence"][0]["detail"] == "raw strings kept"
    assert worked["first_seen_at"] < worked["last_seen_at"]

    titles = [t["title"] for t in d["timeline"]]
    assert titles == ["Repo acme-robo/hand created", "GitHub org acme-robo created", "Ana changed bio"]
    assert "Ben old paper" not in titles and "Olaf unrelated" not in titles  # 180d window, members only

    assert [x["full_name"] for x in d["repositories"]] == ["acme-robo/hand"]
    urls = {s["url"] for s in d["sources"]}
    assert {"https://github.com/acme-robo", "https://arxiv.org/abs/2401.00001", "https://github.com/fq",
            "https://github.com/acme-robo/hand"} <= urls
    assert len(urls) == len(d["sources"])
    assert d["jobs"] == [] and d["papers"] == []


def test_person_candidate_detail_and_errors(world, client):
    d = client.get("/api/formation/solo-builder").json()
    assert d["card"]["kind"] == "person" and d["convergence"]["strength"] == "LOW"
    assert d["relationships"] == [] and d["card"]["previous_affiliations"] == ["Google DeepMind"]
    assert client.get("/api/formation/nope").status_code == 404
    assert client.get(f"/api/formation/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/formation/bad%20slug").status_code == 422


def test_score_breakdown(world, client):
    r = client.get("/api/formation/acme-robo-team/score")
    assert r.status_code == 200
    s = r.json()
    assert s["total"] == 81 and s["computed_on"] == "2026-10-06"
    assert sum(c["value"] for c in s["components"]) == 71
    assert s["components"][0]["key"] == "founder_quality" and s["components"][0]["evidence"]
    assert client.get("/api/formation/nope/score").status_code == 404


# ── graph reads the relationships table ──────────────────────────────────────


def test_graph_uses_relationships_when_present(world, client):
    g = client.get("/api/graph", params={"entity_id": "ana"}).json()
    nodes = {n["id"]: n for n in g["nodes"]}
    kinds = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    a, b, o = str(world["a"]), str(world["b"]), str(world["outsider"])
    pa, pb = sorted([a, b])
    assert (pa, pb, "coauthored_with") in kinds and (pa, pb, "worked_with") in kinds
    assert (a, str(world["org"]), "member") in kinds
    assert o in nodes  # peer relationship pulls the outsider in
    # Ana's RepoContributor row has no CONTRIBUTED_TO relationship: the table is the source of truth
    assert (a, str(world["repo"]), "contributor") not in kinds
    assert all(e["source"] in nodes and e["target"] in nodes for e in g["edges"])


def test_graph_falls_back_for_entities_without_relationships(world, client):
    with session_scope() as s:
        c = Company(slug="newco", name="NewCo")
        p = Person(slug="newbie", name="New Bie")
        s.add_all([c, p])
        s.flush()
        s.add(CompanyPerson(company_id=c.id, person_id=p.id, is_founder=True, role="CEO"))
        cid, pid = str(c.id), str(p.id)
    g = client.get("/api/graph", params={"entity_id": "newco"}).json()
    assert (pid, cid, "founder") in {(e["source"], e["target"], e["kind"]) for e in g["edges"]}


def test_graph_derives_everything_when_table_empty(world, client):
    with session_scope() as s:
        s.query(Relationship).delete()
    g = client.get("/api/graph", params={"entity_id": "ana"}).json()
    kinds = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert (str(world["a"]), str(world["repo"]), "contributor") in kinds
    assert not any(k in ("coauthored_with", "worked_with", "member") for *_, k in kinds)
