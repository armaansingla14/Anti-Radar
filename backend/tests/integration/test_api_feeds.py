"""Integration tests: /api/radar, /api/daily, /api/brief (+ markdown) on the seeded fictional dataset."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from radar import clock
from radar.db import session_scope
from radar.models import Company, Signal, SignalEntity, User, Watchlist, WatchlistItem
from radar.services.feed import change_boost

DEMO = {"include_demo": "true"}


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


# ── /api/radar ───────────────────────────────────────────────────────────────


def test_radar_ranks_halcyon_first_with_evidence_and_demo_labels(seeded, client):
    r = client.get("/api/radar", params=DEMO)
    assert r.status_code == 200
    feed = r.json()
    cards = feed["cards"]
    assert cards and cards[0]["name"] == "Halcyon Dynamics"
    assert [c["rank"] for c in cards] == list(range(1, len(cards) + 1))
    assert len(cards) <= 12
    for c in cards:
        assert c["entity_type"] == "company"
        assert c["is_demo"] is True and "DEMO" in c["data_note"]
        assert c["why_surfaced"], f"{c['name']} has no why_surfaced evidence"
    halcyon = cards[0]
    assert any(e["signal_id"] and e["source_url"] for e in halcyon["why_surfaced"])
    assert halcyon["is_new"] is True and halcyon["sparkline"] and halcyon["thesis_tags"]
    assert halcyon["delta_7d"] is not None and halcyon["delta_7d"] > 0

    founders = feed["founders"]
    assert 0 < len(founders) <= 8
    assert all(f["entity_type"] == "person" and f["score"] >= 30 for f in founders)
    assert founders[0]["name"] == "Elias Brandt"

    assert 0 < len(feed["strange_signals"]) <= 5
    assert all(s["evidence"] and s["is_demo"] for s in feed["strange_signals"])
    shifts = feed["thesis_shifts"]
    assert len(shifts) <= 4  # shifts need >= 2 companies moving; a 7-company demo set may have none
    assert all(s["detail"] for s in shifts)
    assert {s["direction"] for s in shifts} <= {"heating", "cooling"}
    assert all(s["detail"] and s["momentum"] != 0 for s in shifts)

    counts = feed["counts"]
    assert counts["strange_signals"] >= len(feed["strange_signals"])
    assert counts["new_entities_7d"] >= 2  # Halcyon + demo-sq-propulsion (+ people)
    assert counts["emerging_companies"] >= 2
    assert counts["founders"] >= 1  # Elias Brandt scores >= 50
    assert "demo" in feed["data_freshness"]
    assert isinstance(feed["demo_mode"], bool)


def test_radar_updates_last_visit_for_default_user(seeded, client):
    with session_scope() as s:
        assert s.scalar(select(User)) is None
    before = clock.now() - timedelta(seconds=5)
    assert client.get("/api/radar").status_code == 200
    with session_scope() as s:
        users = s.scalars(select(User)).all()
        assert len(users) == 1 and users[0].name == "Anti Fund"
        assert users[0].last_visit_at is not None and users[0].last_visit_at >= before
    assert client.get("/api/radar").status_code == 200
    with session_scope() as s:
        assert len(s.scalars(select(User)).all()) == 1


def test_radar_filters_new_only_theme_and_demo(seeded, client):
    new = client.get("/api/radar", params={**DEMO, "new_only": "true"}).json()
    names = {c["name"] for c in new["cards"]}
    assert "Halcyon Dynamics" in names and "Tessellate Compute" not in names
    assert all(c["is_new"] for c in new["cards"] + new["founders"])

    energy = client.get("/api/radar", params={**DEMO, "theme": "energy"}).json()
    assert [c["name"] for c in energy["cards"]] == ["Perihelion Fusion"]
    assert all(s["theme"] == "energy" for s in energy["thesis_shifts"])

    assert client.get("/api/radar", params={"theme": "astrology"}).status_code == 422
    no_demo = client.get("/api/radar", params={"include_demo": "false"}).json()
    assert no_demo["cards"] == [] and no_demo["founders"] == [] and no_demo["strange_signals"] == []


def test_change_boost_is_bounded_and_documented():
    now = datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert change_boost(None, None, now) == 0
    assert change_boost(-3, None, now) == 0
    assert change_boost(100, now, now) == 2 + 3 + 4  # max boost 9
    assert change_boost(None, now - timedelta(days=8), now) == 0
    assert 0 < change_boost(1, now - timedelta(days=3), now) < 5


# ── /api/daily ───────────────────────────────────────────────────────────────


def test_daily_sections_populated(seeded, client):
    r = client.get("/api/daily", params=DEMO)
    assert r.status_code == 200
    d = r.json()
    since = _ts(d["since"])
    assert abs((clock.now() - timedelta(hours=24) - since).total_seconds()) < 120

    assert d["companies"] and len(d["companies"]) <= 5
    assert "Halcyon Dynamics" in {c["name"] for c in d["companies"]}
    assert "Tessellate Compute" not in {c["name"] for c in d["companies"]}  # score fell, no new signals
    assert d["founders"] and len(d["founders"]) <= 5
    assert d["founders"][0]["name"] == "Elias Brandt"

    papers = d["papers"]
    assert 0 < len(papers) <= 3
    assert all(p["why_it_matters"] and p["score"] is not None and p["is_demo"] for p in papers)
    assert [p["score"] for p in papers] == sorted((p["score"] for p in papers), reverse=True)
    assert any(e["type"] == "person" for p in papers for e in p["linked_entities"])

    assert 0 < len(d["strange_signals"]) <= 3
    new_names = {e["name"] for e in d["new_entities"]}
    assert {"Halcyon Dynamics", "Sana Qureshi"} <= new_names
    assert d["summary"]["generator"] == "template" and d["summary"]["text"]
    assert d["watchlist_changes"] == []


def test_daily_since_param_and_last_visit(seeded, client):
    assert client.get("/api/daily", params={"since": "2099-01-01T00:00:00Z"}).status_code == 422
    assert client.get("/api/daily", params={"since": "not-a-date"}).status_code == 422
    explicit = (clock.now() - timedelta(days=3)).replace(microsecond=0)
    d = client.get("/api/daily", params={**DEMO, "since": explicit.isoformat()}).json()
    assert _ts(d["since"]) == explicit

    last = clock.now() - timedelta(days=5)
    with session_scope() as s:
        s.add(User(name="Anti Fund", last_visit_at=last))
    d = client.get("/api/daily", params=DEMO).json()
    assert abs((_ts(d["since"]) - last).total_seconds()) < 1  # older visit widens the window


def test_daily_watchlist_changes(seeded, client):
    with session_scope() as s:
        halcyon = s.scalar(select(Company).where(Company.slug == "halcyon-dynamics"))
        u = User(name="Anti Fund")
        s.add(u)
        s.flush()
        wl = Watchlist(user_id=u.id, name="Default")
        s.add(wl)
        s.flush()
        s.add(WatchlistItem(watchlist_id=wl.id, entity_type="company", entity_id=halcyon.id,
                            last_seen_at=clock.now() - timedelta(days=10)))
    d = client.get("/api/daily", params=DEMO).json()
    changes = d["watchlist_changes"]
    assert len(changes) == 1 and changes[0]["slug"] == "halcyon-dynamics"
    text = " | ".join(changes[0]["changes"])
    assert "new engineering role" in text
    assert "GitHub stars +" in text and "(30d)" in text
    assert changes[0]["is_demo"] is True


# ── /api/brief ───────────────────────────────────────────────────────────────


def test_brief_items_are_backed_by_evidence(seeded, client):
    r = client.get("/api/brief", params=DEMO)
    assert r.status_code == 200
    b = r.json()
    items = b["what_changed"]
    assert 3 <= len(items) <= 5
    assert [i["n"] for i in items] == list(range(1, len(items) + 1))
    with session_scope() as s:
        for it in items:
            assert it["evidence"] and it["entity_refs"], it
            ids = [uuid.UUID(e) for e in it["evidence"]]
            sigs = s.scalars(select(Signal).where(Signal.id.in_(ids))).all()
            assert len(sigs) == len(ids), f"unknown evidence ids in {it['text']}"
            # every evidence signal concerns one of the referenced entities (directly, via link, or repo)
            ref_ids = {uuid.UUID(e["id"]) for e in it["entity_refs"]}
            linked = set(s.scalars(select(SignalEntity.signal_id).where(SignalEntity.entity_id.in_(ref_ids))))
            from radar.models import Repository

            repo_ids = set(s.scalars(select(Repository.id).where(Repository.company_id.in_(ref_ids))))
            for sig in sigs:
                assert sig.entity_id in ref_ids | repo_ids or sig.id in linked, (it["text"], sig.title)

    assert any("Halcyon Dynamics" in i["text"] for i in items)
    assert len(b["founders_to_know"]) <= 3 and b["founders_to_know"]
    assert b["companies_to_watch"][0]["name"] == "Halcyon Dynamics" and len(b["companies_to_watch"]) <= 3
    assert 0 < len(b["research"]) <= 3
    assert b["strange_signal"] and b["strange_signal"]["headline"]
    assert b["narrative"]["generator"] == "template" == b["generator"]
    md = b["markdown"]
    assert md.startswith("# ANTI RADAR MORNING BRIEF — OCTOBER 6, 2026")
    assert "## What changed" in md and "](/companies/halcyon-dynamics)" in md and "`DEMO`" in md


def test_brief_markdown_download(seeded, client):
    r = client.get("/api/brief/markdown", params=DEMO)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"] and ".md" in r.headers["content-disposition"]
    assert r.text.startswith("# ANTI RADAR MORNING BRIEF")


def test_morning_brief_service_contract(seeded):
    """The CLI imports radar.services.brief.morning_brief(session)."""
    from radar.services.brief import morning_brief

    with session_scope() as s:
        b = morning_brief(s)
    assert b.date == clock.today() and b.markdown and b.generator in ("llm", "template", "cached")


def test_feed_endpoints_are_fast(seeded, client):
    import time

    client.get("/api/radar", params=DEMO)  # warm
    for path in ("/api/radar", "/api/daily", "/api/brief"):
        t = time.perf_counter()
        assert client.get(path, params=DEMO).status_code == 200
        assert time.perf_counter() - t < 1.0, path  # generous bound for CI; ~100-250ms locally
