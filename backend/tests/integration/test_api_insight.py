"""Thesis map, search, status, retrospective and founder graph endpoints (seeded fictional dataset)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from radar.db import session_scope
from radar.models import Company, IngestionRun, MetricSnapshot, Repository, Signal


def _cell(tm: dict, key: str) -> dict:
    for theme in tm["themes"]:
        for c in theme["cells"]:
            if c["key"] == key:
                return c
    raise AssertionError(f"cell {key} missing")


# ── thesis map ───────────────────────────────────────────────────────────────


def test_thesis_map_covers_every_subcategory(seeded, client, thesis):
    r = client.get("/api/thesis")
    assert r.status_code == 200
    tm = r.json()
    assert [t["key"] for t in tm["themes"]] == list(thesis.themes)
    for theme in tm["themes"]:
        assert [c["key"] for c in theme["cells"]] == [s.key for s in thesis.themes[theme["key"]].subcategories.values()]
        for c in theme["cells"]:
            assert 0 <= c["momentum"] <= 100 and (c["attention"] is None or 0 <= c["attention"] <= 100)
            assert c["momentum_trend"] in {"up2", "up", "flat", "down"}

    tactile = _cell(tm, "robotics.tactile")
    assert tactile["companies"] >= 1 and tactile["emerging_companies"] >= 1
    assert tactile["founders"] >= 1 and tactile["hiring_roles_30d"] >= 1
    assert tactile["top_entities"][0]["name"] == "Halcyon Dynamics"
    assert tactile["attention_gap"] is (tactile["momentum"] >= 60 and tactile["attention"] <= 40)
    # an empty cell is allowed and well-formed
    empty = _cell(tm, "software.devtools")
    assert empty["companies"] == 0 and empty["momentum"] == 0 and empty["top_entities"] == []
    robotics = next(t for t in tm["themes"] if t["key"] == "robotics")
    software = next(t for t in tm["themes"] if t["key"] == "software")
    assert robotics["momentum"] > software["momentum"]


def test_thesis_map_is_deterministic(seeded, client):
    a = client.get("/api/thesis").json()
    b = client.get("/api/thesis").json()
    a.pop("generated_at"), b.pop("generated_at")
    assert a == b


# ── search ───────────────────────────────────────────────────────────────────


def test_search_tactile_robotics_finds_halcyon(seeded, client):
    r = client.get("/api/search", params={"q": "tactile robotics"})
    assert r.status_code == 200
    body = r.json()
    assert body["parser"] == "deterministic"
    assert body["filters"]["topics"] == ["robotics.tactile"]
    assert body["results"][0]["name"] == "Halcyon Dynamics"
    top = body["results"][0]
    assert top["is_demo"] is True and top["score"] is not None
    assert any(m.startswith("Thesis: Robotics · Tactile sensing") for m in top["match_reasons"])
    assert body["total"] >= len(body["results"])


def test_search_founders_who_left_coriolis(seeded, client):
    body = client.get("/api/search", params={"q": "founders who left Coriolis Aerospace"}).json()
    names = [r["name"] for r in body["results"]]
    assert "Sana Qureshi" in names
    assert "Jonah Feld" not in names  # Coriolis alum, but no departure signal
    assert all(r["entity_type"] == "person" for r in body["results"])
    sana = next(r for r in body["results"] if r["name"] == "Sana Qureshi")
    assert "Prior affiliation: Coriolis Aerospace" in sana["match_reasons"]
    assert any(m.startswith("Signal:") for m in sana["match_reasons"])
    assert "left: Coriolis Aerospace" in body["interpretation"]


def test_search_spec_examples(seeded, client):
    body = client.get("/api/search", params={"q": "Robotics founders who left major AI labs in the last six months"}).json()
    assert [r["name"] for r in body["results"]][:1] == ["Mira Okafor"]

    body = client.get("/api/search", params={"q": "Companies with high technical momentum but low social attention"}).json()
    names = [r["name"] for r in body["results"]]
    assert names and set(names) <= {"demo-sq-propulsion", "Kestrel Autonomy", "Halcyon Dynamics", "Tessellate Compute", "Perihelion Fusion"}
    assert "Tessellate Compute" not in names and "Loomwork" not in names
    assert all(any(m.startswith("Attention percentile") for m in r["match_reasons"]) for r in body["results"])

    body = client.get("/api/search", params={"q": "Companies working on inference outside NVIDIA CUDA"}).json()
    assert [r["name"] for r in body["results"]] == ["Tessellate Compute"]

    body = client.get("/api/search", params={"q": "Interesting founders in autonomous defense"}).json()
    assert {r["name"] for r in body["results"]} >= {"Tomas Iwu"}

    body = client.get("/api/search", params={"q": "Research papers that could become companies"}).json()
    assert body["results"] and all(any(m.startswith("Paper:") for m in r["match_reasons"]) for r in body["results"])
    assert {r["entity_type"] for r in body["results"]} <= {"person", "company"}


def test_search_validation_and_dismissed(seeded, client):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "x" * 501}).status_code == 422
    assert client.get("/api/search", params={"q": "robotics", "limit": 0}).status_code == 422
    with session_scope() as s:
        s.query(Company).filter(Company.slug == "halcyon-dynamics").update({"dismissed": True})
    names = [r["name"] for r in client.get("/api/search", params={"q": "tactile robotics"}).json()["results"]]
    assert "Halcyon Dynamics" not in names


def test_search_is_fast(seeded, client):
    client.get("/api/search", params={"q": "tactile robotics"})
    t = time.perf_counter()
    client.get("/api/search", params={"q": "Robotics founders who left major AI labs in the last six months"})
    assert time.perf_counter() - t < 0.3


# ── status ───────────────────────────────────────────────────────────────────


def test_status_lists_providers_llm_and_db(seeded, client, monkeypatch):
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "x_bearer_token", None)
    r = client.get("/api/status")
    assert r.status_code == 200
    st = r.json()
    by = {p["provider"]: p for p in st["providers"]}
    assert {"github", "hackernews", "arxiv", "website", "x"} <= set(by)
    assert by["x"]["state"] == "disabled"
    assert by["x"]["reason"].startswith("API access not configured")
    assert by["hackernews"]["state"] == "never_run"
    assert "demo" not in by  # the fictional dataset is reported as SYNTHETIC data, never as a source
    assert st["llm"]["provider"] == "none" and st["llm"]["enabled"] is False
    assert set(st["llm"]["models"]) == {"fast", "standard", "deep"}
    db = st["database"]
    assert db["companies"] == 7 and db["signals"] > 0
    # the fictional demo metric series is not RADAR observation history
    assert db["snapshot_days"] == 0 and db["observed_days"] == 0
    with session_scope() as s:
        s.add(MetricSnapshot(entity_type="repository", entity_id=uuid.uuid4(), metric="stars",
                             day=datetime.now(UTC).date(), value=10, source="observed"))
    assert client.get("/api/status").json()["database"]["observed_days"] == 1
    assert st["score_version"] and st["last_scored_at"]
    assert "sk-" not in r.text  # never leak secrets


def test_status_provider_states(seeded, client, monkeypatch):
    import radar.services.status as status_mod

    monkeypatch.setattr(status_mod, "_github_authenticated", lambda: False)
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(IngestionRun(provider="hackernews", status="ok", started_at=now - timedelta(hours=1),
                           finished_at=now - timedelta(hours=1), records_fetched=40, signals_created=5,
                           latency_ms_p50=120.0))
        s.add(IngestionRun(provider="arxiv", status="error", started_at=now - timedelta(hours=2),
                           finished_at=now - timedelta(hours=2), errors=["HTTP 503 from export.arxiv.org"]))
        s.add(IngestionRun(provider="website", status="ok", started_at=now - timedelta(hours=72),
                           finished_at=now - timedelta(hours=72)))
        s.add(IngestionRun(provider="github", status="ok", started_at=now - timedelta(hours=1),
                           finished_at=now - timedelta(hours=1), rate_limit_remaining=42))
    by = {p["provider"]: p for p in client.get("/api/status").json()["providers"]}
    assert by["hackernews"]["state"] == "healthy"
    assert by["hackernews"]["records_collected"] == 40 and by["hackernews"]["runs_24h"] == 1
    assert by["hackernews"]["latency_ms_p50"] == 120.0
    assert by["arxiv"]["state"] == "error" and by["arxiv"]["errors"] == ["HTTP 503 from export.arxiv.org"]
    assert by["website"]["state"] == "degraded" and "stale" in by["website"]["reason"]
    assert by["github"]["state"] == "degraded" and "unauthenticated — 60 req/h" in by["github"]["reason"]
    assert by["github"]["rate_limit_remaining"] == 42


def test_status_snapshot_mode_is_relative_to_capture(seeded, client, monkeypatch):
    from radar import clock

    captured = datetime(2026, 9, 1, 12, tzinfo=UTC)
    with session_scope() as s:
        s.add(IngestionRun(provider="hackernews", status="ok", started_at=captured, finished_at=captured,
                           records_fetched=10))
    monkeypatch.setattr(clock, "_as_of", captured.date())
    from radar.db import session_scope as ss
    from radar.services.status import system_status

    with ss() as s:
        st = system_status(s)
    hn = next(p for p in st.providers if p.provider == "hackernews")
    assert hn.state == "healthy"
    assert "Snapshot replay — captured 2026-09-01" in hn.reason
    assert hn.records_collected == 10


# ── retrospective ────────────────────────────────────────────────────────────


def test_retrospective_disclaimer_and_entries(seeded, client):
    from radar.retro import RETRO

    # Simulate one ingested retro repo owned by a company with long star history.
    today = datetime.now(UTC).date()
    with session_scope() as s:
        c = Company(slug="vllm-project", name="vLLM", kind="project", github_org="vllm-project")
        s.add(c)
        s.flush()
        repo = Repository(full_name="vllm-project/vllm", owner="vllm-project", company_id=c.id, stars=5000,
                          attrs={"star_history": "complete"})
        s.add(repo)
        s.flush()
        for i in range(0, 120, 3):
            s.add(MetricSnapshot(entity_type="repository", entity_id=repo.id, metric="stars",
                                 day=today - timedelta(days=120 - i), value=1000 + i * 30, source="backfill"))
        s.add(Signal(entity_id=repo.id, entity_type="repository", signal_type="release_published",
                     source_type="github", title="Released v0.9", event_at=datetime.now(UTC) - timedelta(days=3),
                     dedup_key="retro-test-1"))
    r = client.get("/api/retrospective")
    assert r.status_code == 200
    body = r.json()
    assert body["disclaimer"].startswith("Retrospective signal analysis")
    assert "not a backtest" in body["disclaimer"]
    assert len(body["entries"]) == len(RETRO)
    vllm = next(e for e in body["entries"] if e["name"] == "vLLM")
    assert vllm["history_available"] is True
    assert vllm["observable_signals"][0]["title"] == "Released v0.9"
    assert vllm["signal_families"] == ["github"]
    assert vllm["series"][0]["metric"] == "stars" and vllm["series"][0]["source"] == "backfill"
    assert "Daily star history back to" in vllm["history_note"]
    missing = next(e for e in body["entries"] if e["name"] == "ollama/ollama")
    assert missing["history_available"] is False
    assert missing["history_note"] == "Not yet ingested — run radar ingest github --repo ollama/ollama"
    assert body["takeaways"][0] == f"1 of {len(RETRO)} retrospective projects are ingested."


def test_framework_reading_sentences():
    from radar.services.retro import framework_reading

    comps = {"technical_momentum": {"value": 22, "max": 25}, "acceleration": {"value": 4, "max": 20},
             "attention_gap": {"value": 1, "max": 10}}
    s = framework_reading(comps, {"attention": 92, "flagged": False})
    assert s.startswith("High technical momentum (22/25)")
    assert "attention gap 1/10 — the project is already widely known" in s and "too obvious" in s
    assert framework_reading(None, None).startswith("Not scored yet")


# ── graph ────────────────────────────────────────────────────────────────────


def test_graph_centered_on_halcyon(seeded, client):
    with session_scope() as s:
        hid = str(s.query(Company.id).filter(Company.slug == "halcyon-dynamics").scalar())
    r = client.get("/api/graph", params={"entity_id": hid, "depth": 2})
    assert r.status_code == 200
    g = r.json()
    nodes = {n["id"]: n for n in g["nodes"]}
    labels = {n["label"]: n for n in g["nodes"]}
    assert "Mira Okafor" in labels and "Halcyon Dynamics" in labels
    assert "org:Brightline Robotics Institute" in nodes and nodes["org:Brightline Robotics Institute"]["type"] == "org"
    mira = labels["Mira Okafor"]["id"]
    kinds = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert (mira, hid, "founder") in kinds
    assert (mira, "org:Brightline Robotics Institute", "prior_affiliation") in kinds
    assert any(k == "code_for" for _, _, k in kinds)
    assert any(k == "contributor" for _, _, k in kinds)
    assert all(e["source"] in nodes and e["target"] in nodes for e in g["edges"])
    assert labels["Halcyon Dynamics"]["score"] is not None and labels["Halcyon Dynamics"]["is_demo"] is True


def test_graph_default_slug_org_and_errors(seeded, client):
    g = client.get("/api/graph").json()
    assert 0 < len(g["nodes"]) <= 120
    assert {"person", "company", "repository"} <= {n["type"] for n in g["nodes"]}
    by_slug = client.get("/api/graph", params={"entity_id": "halcyon-dynamics"}).json()
    assert any(n["label"] == "Mira Okafor" for n in by_slug["nodes"])
    org = client.get("/api/graph", params={"entity_id": "org:Coriolis Aerospace"}).json()
    assert {"Sana Qureshi", "Marcus Bell", "Ilse Varga"} <= {n["label"] for n in org["nodes"]}
    assert client.get("/api/graph", params={"entity_id": "does-not-exist"}).status_code == 404
    assert client.get("/api/graph", params={"depth": 3}).status_code == 422


def test_insight_endpoints_fast(seeded, client):
    for path in ("/api/thesis", "/api/status", "/api/retrospective", "/api/graph"):
        client.get(path)
        t = time.perf_counter()
        assert client.get(path).status_code == 200
        assert time.perf_counter() - t < 0.3, path
