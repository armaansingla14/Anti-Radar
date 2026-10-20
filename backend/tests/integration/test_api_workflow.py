"""Watchlist, feedback, investigate (SSE), memo and ingest endpoints (seeded fictional dataset)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update

from radar import clock
from radar.db import session_scope
from radar.models import (
    Company,
    EntityTopic,
    Person,
    PreferenceWeight,
    ResearchReport,
    Topic,
    UserAction,
    UserFeedback,
    WatchlistItem,
)


def _as_live_history() -> None:
    """Simulate daily live scoring runs (deltas are never computed live-vs-reconstructed)."""
    with session_scope() as s:
        s.execute(text("UPDATE score_snapshots SET attention = attention - 'reconstructed'"))


def _id(model, slug: str) -> str:
    with session_scope() as s:
        return str(s.scalar(select(model.id).where(model.slug == slug)))


def _events(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):]) for line in text.splitlines() if line.startswith("data: ")]


# ── watchlist ────────────────────────────────────────────────────────────────


def test_watchlist_add_list_idempotent(seeded, client):
    assert client.get("/api/watchlist").json() == {"name": "My watchlist", "items": [], "changes_since_last_visit": 0}
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/watchlist", json={"entity_type": "company", "entity_id": hid, "note": "tactile"})
    assert r.status_code == 200
    item = r.json()
    assert item["name"] == "Halcyon Dynamics" and item["slug"] == "halcyon-dynamics" and item["is_demo"] is True
    assert item["score"] is not None and item["note"] == "tactile" and item["changes"] == []
    again = client.post("/api/watchlist", json={"entity_type": "company", "entity_id": "halcyon-dynamics"}).json()
    assert again["id"] == item["id"] and again["note"] == "tactile"
    client.post("/api/watchlist", json={"entity_type": "person", "entity_id": "sana-qureshi"})
    client.post("/api/watchlist", json={"entity_type": "topic", "topic_key": "robotics.tactile"})
    wl = client.get("/api/watchlist").json()
    assert len(wl["items"]) == 3
    assert {i["entity_type"] for i in wl["items"]} == {"company", "person", "topic"}
    # the shared presenters see the watch state
    radar_cards = client.get("/api/companies", params={"include_demo": "true"}).json()
    assert next(c for c in radar_cards if c["slug"] == "halcyon-dynamics")["watched"] is True
    with session_scope() as s:
        assert s.scalar(select(UserAction).where(UserAction.action == "watch").limit(1)) is not None


def test_watchlist_validation(seeded, client):
    assert client.post("/api/watchlist", json={"entity_type": "company", "entity_id": "nope"}).status_code == 404
    assert client.post("/api/watchlist", json={"entity_type": "topic", "topic_key": "robotics.nope"}).status_code == 404
    assert client.post("/api/watchlist", json={"entity_type": "company"}).status_code == 404
    assert client.post("/api/watchlist", json={"entity_type": "galaxy", "entity_id": "x"}).status_code == 422
    assert client.delete("/api/watchlist/not-a-uuid").status_code == 404
    assert client.delete("/api/watchlist/00000000-0000-0000-0000-000000000000").status_code == 404


def test_watchlist_changes_since_last_visit(seeded, client):
    _as_live_history()
    client.post("/api/watchlist", json={"entity_type": "company", "entity_id": "halcyon-dynamics"})
    client.post("/api/watchlist", json={"entity_type": "person", "entity_id": "sana-qureshi"})
    with session_scope() as s:  # pretend the analyst last looked 20 days ago
        s.execute(update(WatchlistItem).values(last_seen_at=clock.now() - timedelta(days=20)))
    wl = client.get("/api/watchlist").json()
    hal = next(i for i in wl["items"] if i["slug"] == "halcyon-dynamics")
    sana = next(i for i in wl["items"] if i["slug"] == "sana-qureshi")
    assert "3 new engineering roles" in hal["changes"]
    assert any(c.startswith("GitHub") for c in hal["changes"])
    assert any(c.startswith("Score ↑") for c in hal["changes"])
    assert any("Coriolis Aerospace" in c for c in sana["changes"])
    assert wl["changes_since_last_visit"] == len(hal["changes"]) + len(sana["changes"])
    assert client.post("/api/watchlist/seen").json()["ok"] is True
    after = client.get("/api/watchlist").json()
    assert after["changes_since_last_visit"] == 0
    assert all(i["changes"] == [] for i in after["items"])


def test_watchlist_topic_movement_is_cleared_by_mark_seen(seeded, client):
    """Topic 'moved' lines are changes since last visit like every other line: mark-seen clears them."""
    _as_live_history()
    with session_scope() as s:
        keys = list(dict.fromkeys(s.scalars(select(Topic.key).join(EntityTopic, EntityTopic.topic_id == Topic.id)
                                            .where(EntityTopic.entity_type == "company"))))
    for k in keys:
        assert client.post("/api/watchlist", json={"entity_type": "topic", "topic_key": k}).status_code == 200
    with session_scope() as s:
        s.execute(update(WatchlistItem).values(last_seen_at=clock.now() - timedelta(days=20)))
    wl = client.get("/api/watchlist").json()
    moved = [c for i in wl["items"] for c in i["changes"] if "moved" in c]
    assert moved and all("since" in c and "this week" not in c for c in moved)
    assert client.post("/api/watchlist/seen").json()["ok"] is True
    after = client.get("/api/watchlist").json()
    assert after["changes_since_last_visit"] == 0
    assert all(i["changes"] == [] for i in after["items"])


def test_watchlist_delete_by_item_or_entity_id(seeded, client):
    hid = _id(Company, "halcyon-dynamics")
    item = client.post("/api/watchlist", json={"entity_type": "company", "entity_id": hid}).json()
    sana = client.post("/api/watchlist", json={"entity_type": "person", "entity_id": "sana-qureshi"}).json()
    assert client.delete(f"/api/watchlist/{item['id']}").status_code == 200  # by item id
    assert client.delete(f"/api/watchlist/{sana['entity_id']}").status_code == 200  # by entity id
    assert client.get("/api/watchlist").json()["items"] == []
    assert client.delete(f"/api/watchlist/{hid}").status_code == 404
    with session_scope() as s:
        assert len(list(s.scalars(select(UserAction).where(UserAction.action == "unwatch")))) == 2


# ── feedback ─────────────────────────────────────────────────────────────────


def test_feedback_useful_adjusts_weights(seeded, client):
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": hid, "label": "useful",
                                           "note": "great team"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["dismissed"] is False
    assert body["changed"]["thesis:robotics"] == pytest.approx(1.05)
    assert body["weights"]["thesis:robotics"] == pytest.approx(1.05)
    assert any(k.startswith("signal:") for k in body["changed"])
    with session_scope() as s:
        fb = s.scalar(select(UserFeedback))
        assert fb.label == "useful" and fb.note == "great team"
        assert "robotics" in fb.context["themes"] and fb.context["signal_types"]
        assert s.get(PreferenceWeight, "thesis:robotics").n == 1


def test_feedback_dismiss_hides_entity(seeded, client):
    slugs = [c["slug"] for c in client.get("/api/companies", params={"include_demo": "true"}).json()]
    assert "loomwork" in slugs
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": "loomwork", "label": "dismiss"})
    assert r.status_code == 200 and r.json()["dismissed"] is True
    with session_scope() as s:
        assert s.scalar(select(Company.dismissed).where(Company.slug == "loomwork")) is True
        assert s.scalar(select(UserAction).where(UserAction.action == "dismiss")) is not None
    assert "loomwork" not in [c["slug"] for c in client.get("/api/companies", params={"include_demo": "true"}).json()]
    # not_interesting lowers weights but does not dismiss
    r = client.post("/api/feedback", json={"entity_type": "person", "entity_id": "sana-qureshi",
                                           "label": "not_interesting"})
    assert r.json()["dismissed"] is False
    with session_scope() as s:
        assert s.scalar(select(Person.dismissed).where(Person.slug == "sana-qureshi")) is False


def test_feedback_validation(seeded, client):
    assert client.post("/api/feedback", json={"entity_type": "company", "entity_id": "nope",
                                              "label": "useful"}).status_code == 404
    assert client.post("/api/feedback", json={"entity_type": "company", "entity_id": "loomwork",
                                              "label": "meh"}).status_code == 422
    assert client.post("/api/feedback", json={"entity_type": "company", "entity_id": "loomwork", "label": "useful",
                                              "note": "x" * 2001}).status_code == 422


# ── investigate (SSE) ────────────────────────────────────────────────────────


def test_investigate_streams_template_brief(seeded, client):
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/investigate", json={"entity_type": "company", "entity_id": hid, "live_fetch": False})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert events[0] == {"type": "step", "label": "Investigation started"}
    steps = [e for e in events if e["type"] == "step"]
    assert len(steps) >= 4 and all(e.get("label") for e in steps)
    assert any("template" in e["label"] for e in steps)
    done = events[-1]
    assert done["type"] == "done" and done["status"] == "complete" and done["generator"] == "template"
    assert not any(e["type"] == "error" for e in events)
    with session_scope() as s:
        rep = s.get(ResearchReport, uuid.UUID(done["report_id"]))
        assert rep.kind == "brief" and rep.generator == "template" and rep.is_demo is True
    latest = client.get("/api/investigate/latest", params={"entity_id": "halcyon-dynamics"})
    assert latest.status_code == 200
    rep = latest.json()
    assert rep["id"] == done["report_id"] and rep["entity_slug"] == "halcyon-dynamics"
    assert rep["markdown"].startswith("# Anti Brief — Halcyon Dynamics") and rep["sources"]


def test_investigate_errors(seeded, client, monkeypatch):
    r = client.post("/api/investigate", json={"entity_type": "company", "entity_id": "nope"})
    assert r.status_code == 404
    assert client.post("/api/investigate", json={"entity_type": "paper", "entity_id": "x"}).status_code == 422
    assert client.get("/api/investigate/latest", params={"entity_id": "kestrel-autonomy"}).status_code == 404
    assert client.get("/api/investigate/latest", params={"entity_id": "nope"}).status_code == 404

    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr("radar.agent.analyst.investigate", boom)
    r = client.post("/api/investigate", json={"entity_type": "person", "entity_id": "sana-qureshi"})
    events = _events(r.text)
    assert events[-1]["type"] == "error"
    assert "secret internal detail" not in r.text and "Traceback" not in r.text


def test_investigate_rate_limited(seeded, client, monkeypatch):
    monkeypatch.setattr("radar.agent.analyst.investigate",
                        lambda s, t, i, **k: SimpleNamespace(id="x", entity_id=None, generator="template"))
    codes = [client.post("/api/investigate", json={"entity_type": "company", "entity_id": "loomwork"}).status_code
             for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


# ── memo ─────────────────────────────────────────────────────────────────────


def test_memo_returns_markdown(seeded, client):
    r = client.post("/api/memo", json={"entity_type": "company", "entity_id": "halcyon-dynamics"})
    assert r.status_code == 200
    memo = r.json()
    assert memo["kind"] == "memo" and memo["generator"] == "template" and memo["is_demo"] is True
    assert memo["markdown"].startswith("# Anti Memo — Halcyon Dynamics")
    assert "Anti Score" in memo["markdown"] and memo["entity_slug"] == "halcyon-dynamics"
    assert client.post("/api/memo", json={"entity_type": "company", "entity_id": "nope"}).status_code == 404
    bad = client.post("/api/memo", json={"entity_type": "company", "entity_id": "halcyon-dynamics",
                                         "report_id": "00000000-0000-0000-0000-000000000000"})
    assert bad.status_code == 404


# ── ingest ───────────────────────────────────────────────────────────────────


def _fake_run(provider: str, **kw):
    return SimpleNamespace(provider=provider, status=kw.get("status", "ok"), records_fetched=3, records_accepted=3,
                           records_rejected=0, signals_created=2, errors=kw.get("errors", []))


@pytest.fixture()
def no_network(monkeypatch):
    calls: dict[str, list] = {"url": [], "github": [], "x": [], "recompute": 0}
    from radar import ops

    def ingest_url(url, company_name=None, max_pages=6):
        calls["url"].append((url, company_name))
        return _fake_run("website"), None

    def ingest_github(**kw):
        calls["github"].append(kw)
        return _fake_run("github"), None

    def ingest_x_manual(url, pasted_text=None, entity_ref=None):
        calls["x"].append((url, pasted_text, entity_ref))
        if not pasted_text:
            return _fake_run("x", status="error"), None, {"error": "could not retrieve the post via oEmbed and no text was provided"}
        return _fake_run("x"), None, {"text_source": "user_provided", "handle": "halcyondyn_demo", "status_id": "1"}

    def recompute(*_a, **_k):
        calls["recompute"] += 1
        return {}

    monkeypatch.setattr(ops, "ingest_url", ingest_url)
    monkeypatch.setattr(ops, "ingest_github", ingest_github)
    monkeypatch.setattr(ops, "ingest_x_manual", ingest_x_manual)
    monkeypatch.setattr(ops, "recompute", recompute)
    return calls


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://127.0.0.1/admin", "file:///etc/passwd",
                                 "http://localhost/", "http://10.1.2.3/", "http://[::1]/", "http://2130706433/",
                                 "http://169.254.169.254/latest/meta-data", "https://user:pw@example.com/",
                                 "gopher://example.com/"])
def test_ingest_url_rejects_unsafe(seeded, client, no_network, url):
    r = client.post("/api/ingest/url", json={"url": url})
    assert r.status_code == 422, url
    assert no_network["url"] == []


def test_ingest_url_happy_path(seeded, client, no_network):
    r = client.post("/api/ingest/url", json={"url": "halcyon-dynamics.example/about#team", "company_name": "Halcyon"})
    assert r.status_code == 200
    body = r.json()
    assert no_network["url"] == [("https://halcyon-dynamics.example/about", "Halcyon")]
    assert no_network["recompute"] == 1 and body["recomputed"] is True
    assert body["run"]["provider"] == "website" and body["run"]["signals_created"] == 2


def test_ingest_github(seeded, client, no_network):
    r = client.post("/api/ingest/github", json={"repo": "demo-halcyon/tactile-vla"})
    assert r.status_code == 200
    assert r.json()["entity"]["slug"] == "halcyon-dynamics" and r.json()["entity"]["type"] == "company"
    assert no_network["github"] == [{"repo": "demo-halcyon/tactile-vla"}]
    r = client.post("/api/ingest/github", json={"org": "demo-kestrel"})
    assert r.json()["entity"]["name"] == "Kestrel Autonomy"
    r = client.post("/api/ingest/github", json={"query": "tactile sensing", "limit": 5})
    assert r.status_code == 200 and r.json()["entity"] is None
    assert no_network["github"][-1]["queries"] == ["tactile sensing"]
    assert client.post("/api/ingest/github", json={}).status_code == 422
    assert client.post("/api/ingest/github", json={"repo": "a/b", "org": "c"}).status_code == 422
    assert client.post("/api/ingest/github", json={"repo": "not a repo"}).status_code == 422


def test_ingest_x_manual(seeded, client, no_network):
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/ingest/x", json={"url": "https://x.com/halcyondyn_demo/status/1840000000000000000",
                                           "pasted_text": "We're hiring founding engineers",
                                           "entity_type": "company", "entity_id": hid})
    assert r.status_code == 200
    body = r.json()
    assert body["info"]["text_source"] == "user_provided"
    assert body["entity"]["slug"] == "halcyon-dynamics"
    ref = no_network["x"][0][2]
    assert ref.type == "company" and ref.key in {"domain:halcyon-dynamics.example", "github_org:demo-halcyon"}
    # no oEmbed text and nothing pasted -> nothing fabricated
    r = client.post("/api/ingest/x", json={"url": "https://x.com/someone/status/1840000000000000001"})
    assert r.status_code == 422 and "no text was provided" in r.json()["detail"]
    assert client.post("/api/ingest/x", json={"url": "https://evil.example/status/1"}).status_code == 422
    assert client.post("/api/ingest/x", json={"url": "https://x.com/a/status/1", "entity_type": "company",
                                              "entity_id": "nope"}).status_code == 404
