"""Admin write gate, /api/session, /api/ready and the DB-free /api/health."""

from __future__ import annotations

import pytest

from radar.settings import get_settings

TOKEN = "t0ken-for-tests-0123456789"


@pytest.fixture()
def prod_like(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_token", TOKEN)
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "demo_snapshot", False)
    return s


def test_writes_need_admin_header(db, client, prod_like):
    r = client.post("/api/watchlist/seen", json={})
    assert r.status_code == 403 and r.json() == {"detail": "read_only"}
    assert client.delete("/api/watchlist/whatever").status_code == 403
    assert client.post("/api/watchlist/seen", json={}, headers={"X-Radar-Admin": "wrong"}).status_code == 403
    ok = client.post("/api/watchlist/seen", json={}, headers={"X-Radar-Admin": TOKEN})
    assert ok.status_code == 200


def test_gate_runs_before_validation_and_expensive_routes(db, client, prod_like):
    # never reaches the handler: no GitHub quota or LLM budget spent, even with a bad body
    assert client.post("/api/ingest/github", json={"repo": "a/b"}).status_code == 403
    assert client.post("/api/investigate", json={}).json() == {"detail": "read_only"}


def test_reads_and_preflight_stay_open(db, client, prod_like):
    assert client.get("/api/watchlist").status_code == 200
    assert client.get("/api/health").status_code == 200
    pre = client.options("/api/feedback", headers={"Origin": "http://localhost:3000",
                                                    "Access-Control-Request-Method": "POST",
                                                    "Access-Control-Request-Headers": "x-radar-admin,content-type"})
    assert pre.status_code == 200
    assert "x-radar-admin" in pre.headers.get("access-control-allow-headers", "").lower()


def test_demo_without_token_allows_writes_but_not_ingest(db, client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_token", None)
    monkeypatch.setattr(s, "app_env", "demo")
    monkeypatch.setattr(s, "demo_snapshot", True)
    assert client.post("/api/watchlist/seen", json={}).status_code == 200
    r = client.post("/api/ingest/github", json={"repo": "a/b"})
    assert r.status_code == 403 and r.json() == {"detail": "snapshot_read_only"}


def test_development_without_token_is_open(db, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", None)
    monkeypatch.setattr(get_settings(), "demo_snapshot", False)
    assert client.post("/api/watchlist/seen", json={}).status_code == 200


def test_session_endpoint(db, client, prod_like):
    body = client.get("/api/session").json()
    assert body["app_env"] == "production"
    assert body["read_only"] is True and body["admin"] is False
    assert set(body) == {"app_env", "read_only", "admin", "demo_snapshot", "demo_synthetic", "snapshot_as_of"}
    admin = client.get("/api/session", headers={"X-Radar-Admin": TOKEN})
    assert admin.json()["read_only"] is False and admin.json()["admin"] is True
    assert admin.headers["cache-control"] == "no-store"


def test_session_without_token(db, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", None)
    body = client.get("/api/session", headers={"X-Radar-Admin": "anything"}).json()
    assert body["read_only"] is False and body["admin"] is False


def test_ready_checks_database(db, client):
    r = client.get("/api/ready")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ready_503_and_health_db_free_when_db_down(db, client, monkeypatch):
    import radar.db as dbmod

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dbmod, "engine", boom)
    r = client.get("/api/ready")
    assert r.status_code == 503 and r.json() == {"ok": False, "database": "unavailable"}
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["ok"] is True


def test_status_db_size_data_age_and_no_synthetic_provider(seeded, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_synthetic", False)
    st = client.get("/api/status").json()
    keys = [p["provider"] for p in st["providers"]]
    assert keys == ["github", "hackernews", "arxiv", "website", "jobs", "x"]
    assert st["database"]["size_bytes"] > 0 and st["database"]["size_pretty"]
    assert "data_as_of" in st["database"]
    assert st["database"]["synthetic"] is None and st["demo_mode"] is False
    x = next(p for p in st["providers"] if p["provider"] == "x")
    if not get_settings().x_bearer_token:
        assert x["state"] == "disabled" and x["reason"].startswith("API access not configured")
    assert "state" in st["llm"]

    monkeypatch.setattr(get_settings(), "demo_synthetic", True)
    st = client.get("/api/status").json()
    assert st["database"]["synthetic"]["label"] == "SYNTHETIC"
    assert st["database"]["synthetic"]["companies"] > 0
    assert "demo" not in [p["provider"] for p in st["providers"]]


def test_demo_snapshot_pins_clock_from_manifest(db, monkeypatch):
    from radar import clock
    from radar.api.main import pin_snapshot_clock

    monkeypatch.setattr(clock, "_as_of", None)
    day = pin_snapshot_clock()
    assert day is not None and clock.as_of() == day
