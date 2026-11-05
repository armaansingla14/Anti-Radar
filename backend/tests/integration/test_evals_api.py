"""`/api/agents*`: overview shape, evaluations, failure ledger, write gates, cases."""

from __future__ import annotations

from radar.agent.analyst import investigate
from radar.evals.failures import seed_documented_failures
from radar.evals.metrics import evaluate_all
from radar.llm.provider import NullProvider
from tests.integration.test_analyst import acme  # noqa: F401 — fixture reuse


def test_overview_shape_when_empty(db, client, monkeypatch):
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", None)
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    r = client.get("/api/agents")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["evaluator_version"] == "eval-1"
    names = [a["name"] for a in d["agents"]]
    assert names[:5] == ["RADAR ANALYST", "MEETING BRIEF", "ANTI MEMO", "MORNING BRIEF", "DILIGENCE ANALYST"]
    m = d["agents"][0]["metrics"]
    assert m["success_rate"] == {"value": None, "n": 0, "reason": "no evaluated runs (run `radar eval`)"}
    assert m["latency_p50_ms"]["value"] is None and m["latency_p50_ms"]["reason"]
    assert {"citation_validity", "unsupported_claim_rate", "fallback_rate", "llm_telemetry"} <= set(d["methodology"])
    assert len(d["taxonomy"]) == 13 and all(t["definition"] for t in d["taxonomy"])
    assert d["regression"]["cases_total"] >= 15 and d["regression"]["never_run"] == d["regression"]["cases_total"]
    assert d["model_comparison"]["configured_models"] == [] and d["model_comparison"]["recommendations"] == []
    assert d["llm_telemetry"] == []


def test_overview_and_evaluations_after_a_run(db, acme, client):  # noqa: F811
    investigate(db, "company", acme.id, live_fetch=False, provider=NullProvider())
    evaluate_all(db)
    db.commit()
    a = client.get("/api/agents").json()["agents"][0]
    assert a["runs"] == 1 and a["metrics"]["success_rate"] == {"value": 1.0, "n": 1, "reason": None}
    assert a["metrics"]["citation_validity"]["value"] == 1.0 and a["by_generator"] == {"template": 1}
    assert a["metrics"]["latency_p50_ms"]["n"] == 1 and a["flags_by_category"]["PROMPT_INJECTION_ATTEMPT"] >= 1
    evs = client.get("/api/agents/evaluations", params={"agent": "analyst"}).json()
    assert len(evs) == 1 and evs[0]["entity_slug"] == "acme-robotics" and evs[0]["report_url"].startswith("/reports/")
    assert evs[0]["flags"] and evs[0]["review_status"] == "AUTOMATIC"
    assert client.get("/api/agents/evaluations", params={"agent": "memo"}).json() == []
    assert client.get("/api/agents/evaluations", params={"agent": "nobody"}).status_code == 422
    assert client.get("/api/agents/evaluations", params={"limit": 0}).status_code == 422


def test_failures_list_filter_detail_create_review(db, client):
    seed_documented_failures(db)
    db.commit()
    rows = client.get("/api/agents/failures").json()
    assert [f["number"] for f in rows] == list(range(1, 12)) and rows[0]["display"] == "FAILURE 0001"
    assert len(client.get("/api/agents/failures", params={"status": "CONFIRMED"}).json()) == 3
    assert len(client.get("/api/agents/failures", params={"category": "SOURCE_QUALITY"}).json()) == 2
    assert len(client.get("/api/agents/failures", params={"agent": "meet"}).json()) == 3
    assert client.get("/api/agents/failures", params={"category": "NOPE"}).status_code == 422
    assert client.get("/api/agents/failures/4").json()["source_ref"].endswith("§6.4")
    assert client.get("/api/agents/failures/999").status_code == 404

    r = client.post("/api/agents/failures", json={"agent": "analyst", "claim_text": "Joined NVIDIA (talk only)",
                                                  "category": "AFFILIATION_INFERENCE", "severity": "HIGH"})
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["number"] == 12 and f["agent"] == "RADAR ANALYST" and f["origin"] == "reviewer" and f["status"] == "OPEN"
    assert client.post("/api/agents/failures", json={"agent": "analyst", "claim_text": "x", "category": "NOPE",
                                                     "severity": "HIGH"}).status_code == 422
    r = client.post("/api/agents/failures/11/review", json={"status": "CONFIRMED", "note": "verified by hand"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED" and r.json()["reviews"][0]["note"] == "verified by hand"
    detail = client.get("/api/agents/failures/11").json()
    assert detail["status"] == "CONFIRMED" and len(detail["reviews"]) == 1
    assert client.post("/api/agents/failures/11/review", json={"status": "MAYBE", "note": "x"}).status_code == 422
    assert client.post("/api/agents/failures/11/review", json={"status": "FIXED", "note": ""}).status_code == 422
    assert client.post("/api/agents/failures/999/review", json={"status": "FIXED", "note": "x"}).status_code == 404


def test_writes_refused_on_snapshot_and_without_admin_token(db, client, monkeypatch):
    from radar.settings import get_settings

    seed_documented_failures(db)
    db.commit()
    body = {"agent": "analyst", "claim_text": "x", "category": "UNSUPPORTED_CLAIM", "severity": "LOW"}
    monkeypatch.setattr(get_settings(), "demo_snapshot", True)
    r = client.post("/api/agents/failures", json=body)
    assert r.status_code == 403 and r.json()["detail"] == "snapshot_read_only"
    r = client.post("/api/agents/failures/1/review", json={"status": "FIXED", "note": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "snapshot_read_only"
    assert client.get("/api/agents").status_code == 200
    monkeypatch.setattr(get_settings(), "demo_snapshot", False)
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret")
    r = client.post("/api/agents/failures", json=body)
    assert r.status_code == 403 and r.json()["detail"] == "read_only"
    assert client.post("/api/agents/failures", json=body, headers={"X-Radar-Admin": "s3cret"}).status_code == 201


def test_cases_list_with_latest_result(db, client):
    from radar.evals.cases import load_cases
    from radar.evals.runner import run_cases, store_outcomes

    cases = load_cases()
    got = client.get("/api/agents/cases").json()
    assert [c["id"] for c in got] == sorted(c.id for c in cases) and all(c["latest"] is None for c in got)
    pure = [c for c in cases if c.task in ("claim_checks", "bot_filter")]
    store_outcomes(db, run_cases(None, pure))
    db.commit()
    got = {c["id"]: c for c in client.get("/api/agents/cases").json()}
    assert got["funding-001"]["latest"]["passed"] is True and got["funding-001"]["latest"]["provider"] == "template"
    assert got["affiliation-001"]["latest"] is None and got["affiliation-001"]["min_citations"] == 3
    reg = client.get("/api/agents").json()["regression"]
    assert reg["passing"] == len(pure) and reg["failing"] == 0 and reg["never_run"] == len(cases) - len(pure)
