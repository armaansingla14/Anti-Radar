"""Integration tests: GET /api/home (services/home.py) on the seeded fictional dataset."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import func, select, update

from radar import clock
from radar.db import session_scope
from radar.models import Company, FormationCandidate, Person, User
from radar.schemas.api import EntityRef
from radar.schemas.feeds_v2 import ChangeItem
from radar.services import home as H
from radar.services import memo

DEMO = {"include_demo": "true"}
KINDS = {"formation", "person", "company", "collision", "strange", "research"}
CONF = {"HIGH CONFIDENCE", "MEDIUM CONFIDENCE", "EARLY SIGNAL"}


def test_home_contract_and_sections(seeded, client):
    r = client.get("/api/home", params=DEMO)
    assert r.status_code == 200
    h = r.json()
    s = h["summary"]
    assert s["data_as_of"] == "2026-10-06" and s["is_snapshot"] is False
    for k in ("possible_formations", "people_to_meet", "companies_accelerating", "signal_collisions",
              "strange_signals"):
        assert isinstance(s[k], int) and s[k] >= 0, k
    assert s["possible_formations"] >= 1 and s["people_to_meet"] >= 1 and s["strange_signals"] >= 1

    wc = h["what_changed"]
    assert 3 <= len(wc) <= 8
    assert {c["kind"] for c in wc} <= KINDS and len({c["kind"] for c in wc}) >= 3  # interleaved kinds
    assert {"formation", "person"} <= {c["kind"] for c in wc}
    for c in wc:
        assert c["headline"] and c["why_now"] and c["confidence"] in CONF, c
        assert c["entity"]["id"] and c["entity"]["name"]
        assert "I1" not in c["headline"] and "founded" not in c["headline"].lower()
    assert len({c["entity"]["id"] for c in wc}) == len(wc)  # one item per entity
    assert any(e["source_url"] for c in wc for e in c["evidence"])

    assert 1 <= len(h["formations"]) <= 3
    assert all("founded" not in f["label"].lower() for f in h["formations"])
    assert 1 <= len(h["people_to_meet"]) <= 5
    assert len(h["companies"]) <= 6 and len(h["strange_signals"]) <= 3
    assert h["companies_basis"] in ("accelerating", "top_ranked")
    assert h["demo_mode"] is True


def test_home_counts_match_sources(seeded, client):
    h = client.get("/api/home", params=DEMO).json()
    meet = client.get("/api/meet", params=DEMO).json()
    assert h["summary"]["people_to_meet"] == len(meet["people"])
    assert [p["person"]["id"] for p in h["people_to_meet"]] == [p["person"]["id"] for p in meet["people"]][:5]
    with session_scope() as s:
        lo = clock.now() - timedelta(days=H.RECENT_DAYS)
        n = s.scalar(select(func.count()).select_from(FormationCandidate)
                     .where(FormationCandidate.latest_activity_at >= lo))
    assert h["summary"]["possible_formations"] == n
    radar = client.get("/api/radar", params=DEMO).json()
    assert h["summary"]["strange_signals"] == radar["counts"]["strange_signals"]
    if h["companies_basis"] == "accelerating":
        assert len(h["companies"]) <= h["summary"]["companies_accelerating"]


def test_home_new_window_filters(seeded, client):
    assert client.get("/api/home", params={"new": "month"}).status_code == 422
    with session_scope() as s:
        s.execute(update(Company).where(Company.slug == "halcyon-dynamics")
                  .values(first_seen_at=clock.now() - timedelta(days=40)))
    all_ = client.get("/api/home", params=DEMO).json()
    week = client.get("/api/home", params={**DEMO, "new": "week"}).json()
    today = client.get("/api/home", params={**DEMO, "new": "today"}).json()
    assert week["new"] == "week" and today["new"] == "today"
    for k in ("people_to_meet", "companies_accelerating", "signal_collisions", "strange_signals"):
        assert today["summary"][k] <= week["summary"][k] <= all_["summary"][k], k
    assert "Halcyon Dynamics" not in {c["name"] for c in week["companies"]}
    cutoff = clock.now() - timedelta(days=7)
    with session_scope() as s:
        ids = {p["person"]["id"] for p in week["people_to_meet"]}
        for p in s.scalars(select(Person).where(Person.id.in_(ids))):
            assert p.first_seen_at >= cutoff


def test_home_excludes_synthetic_by_default(seeded, client):
    h = client.get("/api/home").json()
    assert h["demo_mode"] is False
    assert not any(c["is_demo"] for c in h["what_changed"])
    assert not any(c["is_demo"] for c in h["companies"]) and not any(f["is_demo"] for f in h["formations"])
    assert not any(p["person"]["is_demo"] for p in h["people_to_meet"])


def test_home_records_a_visit(seeded, client):
    client.get("/api/home", params=DEMO)
    with session_scope() as s:
        u = s.scalar(select(User).order_by(User.created_at).limit(1))
        assert u is not None and u.last_visit_at is not None


def test_home_is_fast_when_warm(seeded, client):
    client.get("/api/home", params=DEMO)  # cold: computes and stores the meet week, fills the memo
    with session_scope() as s:
        H.home_feed(s, include_demo=True)
        t = time.perf_counter()
        H.home_feed(s, include_demo=True)
        assert time.perf_counter() - t < 0.3
    t = time.perf_counter()
    assert client.get("/api/home", params=DEMO).status_code == 200
    assert time.perf_counter() - t < 0.5


def test_home_falls_back_honestly_without_acceleration(seeded, client, monkeypatch):
    from radar.services import presenters as P

    monkeypatch.setattr(P, "score_deltas", lambda *a, **k: {})
    monkeypatch.setattr(H, "acceleration_pct", lambda snap: None)
    memo.clear()
    with session_scope() as s:
        h = H.build_home(s, new=None, include_demo=True)
    assert h.summary.companies_accelerating == 0
    assert h.companies_basis == "top_ranked" and h.companies_note and h.companies
    assert not any(c.kind == "company" for c in h.what_changed)  # no "accelerating" claim


def _item(kind: str, eid: str, headline: str) -> ChangeItem:
    return ChangeItem(kind=kind, headline=headline, why_now="x", entity=EntityRef(type="company", id=eid, name=eid))


def test_select_changes_interleaves_and_dedupes():
    cands = [H.Cand(_item("formation", f"f{i}", f"F{i}"), 1.0 - i / 100) for i in range(5)]
    cands += [H.Cand(_item("person", "p1", "P1"), 0.5), H.Cand(_item("company", "f0", "dup"), 0.99)]
    cands += [H.Cand(_item("strange", "s1", "S1"), 0.3, ("p1",))]
    out = H.select_changes(cands, limit=4, per_kind=2)
    heads = [c.headline for c in out]
    # first pass: 2 formations + the person (dup entity and the strange item covering p1 skipped);
    # fill pass: the next formation; output ordered by rank
    assert heads == ["F0", "F1", "F2", "P1"]
    assert H.recency(None, clock.now()) == 0.25 and H.recency(clock.now(), clock.now()) == 1.0


def test_home_company_rows_use_stored_snapshot_collision(seeded, client):
    """Company rows carry the stored snapshot collision (families / strength / sentence) — the same
    data the WHAT CHANGED collision items and the company page use."""
    from radar.models import ScoreSnapshot

    h = client.get("/api/home", params=DEMO).json()
    assert h["companies"]
    with session_scope() as s:
        for card in h["companies"]:
            snap = s.scalars(select(ScoreSnapshot).where(ScoreSnapshot.entity_id == card["id"])
                             .order_by(ScoreSnapshot.day.desc()).limit(1)).first()
            stored = (snap.collision or {}) if snap else {}
            col = card["collision"]
            if not stored:
                assert col is None
                continue
            assert col["families"] == list(stored.get("families") or [])
            assert col["strength"] == H.collision_strength(stored)
            assert col["sentence"] and col["family_labels"] is not None
            page = client.get(f"/api/companies/{card['slug']}").json()["anti_score"]["collision"]
            assert page["families"] == col["families"] and page["strength"] == col["strength"]
    for item in h["what_changed"]:
        if item["kind"] == "collision" and item["entity"]["type"] == "company":
            row = next((c for c in h["companies"] if c["id"] == item["entity"]["id"]), None)
            if row is not None:
                assert len(row["collision"]["families"]) >= 2
