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


# ── every mutating route is gated (enumerated from the app, so new routes can't be missed) ──

# Known mutating routes. The gate test below covers *every* non-GET route it discovers; this list only
# guards against the discovery silently finding nothing (e.g. after a FastAPI internals change).
KNOWN_WRITES = {
    ("POST", "/api/feedback"), ("POST", "/api/ingest/github"), ("POST", "/api/ingest/url"),
    ("POST", "/api/ingest/x"), ("POST", "/api/investigate"), ("POST", "/api/meet/refresh"),
    ("POST", "/api/meeting-brief"), ("POST", "/api/memo"), ("POST", "/api/watchlist"),
    ("POST", "/api/watchlist/seen"), ("DELETE", "/api/watchlist/{ref}"),
}


def _walk_routes(routes, prefix=""):
    from fastapi.routing import APIRoute

    for r in routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):  # FastAPI >= 0.140 wraps included routers
            yield from _walk_routes(r.original_router.routes, prefix + r.include_context.prefix)
        elif hasattr(r, "routes"):
            yield from _walk_routes(r.routes, prefix + getattr(r, "path", ""))


def mutating_routes(app) -> set[tuple[str, str]]:
    out = {(m, path) for path, r in _walk_routes(app.routes) for m in r.methods if m not in ("GET", "HEAD", "OPTIONS")}
    # Cross-check with the OpenAPI schema (public API) so neither discovery path can hide a route.
    for path, ops in app.openapi()["paths"].items():
        out |= {(m.upper(), path) for m in ops if m.upper() not in ("GET", "HEAD", "OPTIONS")}
    return out


def _concrete(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "zz-not-a-real-id", path)


# A body that fails validation everywhere: proves the request got past the gate without running a
# handler (no GitHub quota, LLM budget or DB writes are spent).
INVALID_BODY = {"__gate_probe__": True, "repo": 7, "url": 7, "person_id": 7, "entity_id": 7, "label": 7}


def test_every_mutating_route_is_admin_gated(db, client, prod_like):
    routes = mutating_routes(client.app)
    assert KNOWN_WRITES <= routes, f"route discovery missed: {KNOWN_WRITES - routes}"
    assert all(path.startswith("/api/") for _m, path in routes), "a write outside /api/ bypasses the gate"
    for method, path in sorted(routes):
        url = _concrete(path)
        for headers in ({}, {"X-Radar-Admin": "wrong-token-of-similar-length-xx"}, {"X-Radar-Admin": ""}):
            r = client.request(method, url, json=INVALID_BODY, headers=headers)
            assert r.status_code == 403 and r.json() == {"detail": "read_only"}, (method, path, headers, r.text)
        ok = client.request(method, url, json=INVALID_BODY, headers={"X-Radar-Admin": TOKEN})
        assert ok.status_code != 403 and ok.json().get("detail") != "read_only", (method, path, ok.text)
        assert ok.status_code in (200, 201, 204, 400, 404, 409, 422), (method, path, ok.status_code, ok.text)


def test_gate_ignores_method_and_path_tricks(db, client, prod_like):
    # lower-case / unusual methods are still non-safe; trailing slashes and double slashes stay under /api
    assert client.request("PUT", "/api/watchlist/seen", json={}).status_code == 403
    assert client.request("PATCH", "/api/feedback", json={}).status_code == 403
    assert client.post("/api/watchlist/seen/", json={}).status_code == 403
    assert client.post("/api//watchlist/seen", json={}).status_code in (403, 404)
    assert client.post("/api/feedback", json={}, headers={"X-HTTP-Method-Override": "GET"}).status_code == 403


EXPENSIVE = {("POST", "/api/investigate"), ("POST", "/api/memo"), ("POST", "/api/meeting-brief"),
             ("POST", "/api/meet/refresh"), ("POST", "/api/ingest/github"), ("POST", "/api/ingest/url"),
             ("POST", "/api/ingest/x"), ("GET", "/api/search")}


def test_expensive_routes_are_rate_limited(client):
    limited = set()
    for path, r in _walk_routes(client.app.routes):
        deps = [getattr(d.call, "__qualname__", "") for d in r.dependant.dependencies]
        if any(q.startswith("rate_limit.") for q in deps):
            limited |= {(m, path) for m in r.methods}
    assert EXPENSIVE <= limited, f"missing rate limits: {EXPENSIVE - limited}"


def test_search_rate_limit_answers_429(db, client, monkeypatch):
    from radar.api import ratelimit
    from radar.api.routes.search import SEARCH_PER_MINUTE

    monkeypatch.setattr(ratelimit, "_now", lambda: 1000.0)  # no refill during the burst

    codes = [client.get("/api/search", params={"q": "robotics"}).status_code for _ in range(SEARCH_PER_MINUTE + 1)]
    assert codes[:SEARCH_PER_MINUTE] == [200] * SEARCH_PER_MINUTE and codes[-1] == 429


def test_rate_limit_keys_on_forwarded_client_ip_only_when_trusted(monkeypatch):
    from starlette.requests import Request

    from radar.api.ratelimit import client_id
    from radar.settings import get_settings

    scope = {"type": "http", "headers": [(b"x-radar-client-ip", b"203.0.113.7, 10.0.0.1")], "client": ("10.1.1.1", 1)}
    req = Request(scope)
    monkeypatch.setattr(get_settings(), "trust_proxy_client_ip", False)
    assert client_id(req) == "10.1.1.1"
    monkeypatch.setattr(get_settings(), "trust_proxy_client_ip", True)
    assert client_id(req) == "203.0.113.7"
