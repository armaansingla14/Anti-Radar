"""Diligence and review API: workspace, status, claims, reviews, write refusals on the snapshot backend."""

from __future__ import annotations

import json

import pytest

from radar.diligence.capture import load
from radar.diligence.collect import CollectResult
from radar.diligence.run import run_diligence
from radar.models import Company
from radar.settings import get_settings
from tests.fixtures.diligence.helpers import SAMPLE_CAPTURE


@pytest.fixture()
def open_writes(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", None)
    monkeypatch.setattr(get_settings(), "demo_snapshot", False)


@pytest.fixture()
def seeded_run(db, open_writes):
    c = Company(slug="quillserve-fixture", name="Quillserve", website="https://quillserve.example",
                domain="quillserve.example", github_org="quillserve-fixture", thesis_tags=[])
    other = Company(slug="unrun-co", name="Unrun Co", website="https://unrun.example", thesis_tags=[])
    hidden = Company(slug="no-links", name="No Links", thesis_tags=[])
    db.add_all([c, other, hidden])
    db.flush()
    run = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    assert run.status == "complete"
    db.commit()
    return c, run


def test_index_lists_workspaces_and_candidates(client, seeded_run):
    body = client.get("/api/diligence").json()
    assert [w["company"]["slug"] for w in body["workspaces"]] == ["quillserve-fixture"]
    w = body["workspaces"][0]
    assert w["status"] == "NEEDS_REVIEW" and w["run"]["summary"]["claims"] == 12 and w["run"]["mode"] == "replay"
    assert [c["company"]["slug"] for c in body["candidates"]] == ["unrun-co"]  # needs a website or GitHub org


def test_workspace_payload(client, seeded_run):
    c, run = seeded_run
    r = client.get("/api/diligence/quillserve-fixture")
    assert r.status_code == 200
    ws = r.json()
    assert ws["company"]["id"] == str(c.id) and ws["status"]["status"] == "NEEDS_REVIEW"
    assert ws["header"]["sources_analyzed"] == 29 and ws["header"]["date"] == "2026-10-06"
    assert len(ws["claims"]) == 12 and ws["claims"][0]["ordinal"] == 1
    plan = ws["claims"][0]["test_plan"]
    assert plan is None or "procedure" in plan  # full plans are in the workspace (no per-claim request)
    assert {u["key"] for u in ws["underwriting"]} >= {"CLAIM_VERIFICATION", "SECURITY", "OPEN_SOURCE_HEALTH"}
    assert all(u["target_id"] == f"{run.id}:{u['key']}" for u in ws["underwriting"])
    assert ws["contradictions"] and ws["questions"] and ws["sources"] and ws["unknowns"]
    assert ws["run"]["trace_summary"]["requests"] == 29 and ws["report_id"] == str(run.report_id)
    assert ws["definitions"]["status_labels"]["CONTRADICTED"] == "EVIDENCE CONFLICT"
    raw = r.text
    assert "Ignore all previous instructions" not in raw  # artifact text is never returned
    assert len(raw) < 400_000
    assert client.get(f"/api/diligence/{c.id}").json()["company"]["slug"] == "quillserve-fixture"
    assert json.loads(raw) == client.get("/api/diligence/quillserve-fixture").json()  # stable


def test_not_run_and_unknown(client, seeded_run):
    r = client.get("/api/diligence/unrun-co")
    assert r.status_code == 200 and r.json()["status"]["status"] == "NOT_RUN" and r.json()["claims"] == []
    assert client.get("/api/diligence/nope").status_code == 404
    assert client.get("/api/diligence/nope/status").status_code == 404
    assert client.get("/api/diligence/unrun-co/claims/1").status_code == 404


def test_claim_detail_and_status(client, seeded_run):
    claims = client.get("/api/diligence/quillserve-fixture").json()["claims"]
    perf = next(c for c in claims if c["claim_type"] == "PERFORMANCE")
    d = client.get(f"/api/diligence/quillserve-fixture/claims/{perf['ordinal']}").json()
    assert d["test_plan"]["execution"]["status"] == "not_executed" and d["test_plan"]["baseline"]["name"] == "vLLM"
    assert d["contradictory_evidence"] and d["sources"] and d["status_reason"].startswith("R")
    assert client.get("/api/diligence/quillserve-fixture/claims/99").status_code == 404
    st = client.get("/api/diligence/quillserve-fixture/status").json()
    assert st["status"] == "NEEDS_REVIEW" and st["pending_review"]


def test_reviews_round_trip_and_clear_needs_review(client, seeded_run):
    st = client.get("/api/diligence/quillserve-fixture/status").json()
    for p in st["pending_review"]:
        r = client.post("/api/reviews", json={"target_type": p["target_type"], "target_id": p["target_id"],
                                              "decision": "QUESTION", "note": "ask founders", "reviewer": "ana"})
        assert r.status_code == 201, r.text
        assert r.json()["decision"] == "QUESTION" and r.json()["reviewer"] == "ana"
    assert client.get("/api/diligence/quillserve-fixture/status").json()["status"] == "COMPLETE"
    claim_target = next(p for p in st["pending_review"] if p["target_type"] == "claim")
    got = client.get("/api/reviews", params={"target_type": "claim", "target_id": claim_target["target_id"]}).json()
    assert len(got) == 1 and got[0]["note"] == "ask founders"
    ws = client.get("/api/diligence/quillserve-fixture").json()
    assert any(c["reviews"] for c in ws["claims"]) and any(u["reviews"] for u in ws["underwriting"])


def test_review_validation(client, seeded_run):
    assert client.post("/api/reviews", json={"target_type": "claim", "target_id": "x", "decision": "MAYBE"}
                       ).status_code == 422
    assert client.post("/api/reviews", json={"target_type": "claim", "target_id": "8b0c3c7e-0000-0000-0000-000000000000",
                                             "decision": "ACCEPT"}).status_code == 404
    _c, run = seeded_run
    assert client.post("/api/reviews", json={"target_type": "underwriting", "target_id": f"{run.id}:NOT_A_KEY",
                                             "decision": "ACCEPT"}).status_code == 404
    assert client.post("/api/reviews", json={"target_type": "report", "target_id": str(run.report_id),
                                             "decision": "ACCEPT", "extra": 1}).status_code == 422


def test_reports_listing_accepts_diligence(client, seeded_run):
    rows = client.get("/api/reports", params={"kind": "diligence"}).json()
    assert len(rows) == 1 and rows[0]["kind"] == "diligence"
    md = client.get(f"/api/reports/{rows[0]['id']}/markdown")
    assert md.status_code == 200 and "anti-diligence-quillserve-fixture-" in md.headers["content-disposition"]


def test_writes_refused_on_snapshot_backend(client, seeded_run, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_snapshot", True)
    assert client.post("/api/diligence/quillserve-fixture/run").json() == {"detail": "snapshot_read_only"}
    r = client.post("/api/reviews", json={"target_type": "report", "target_id": str(seeded_run[1].report_id),
                                          "decision": "ACCEPT"})
    assert r.status_code == 403 and r.json() == {"detail": "snapshot_read_only"}
    assert client.get("/api/diligence/quillserve-fixture").status_code == 200  # reads stay open


def test_run_endpoint_live_with_offline_collection(client, seeded_run, monkeypatch):
    cap = load(SAMPLE_CAPTURE)

    def fake_collect(session, company, *, budget_s, fetcher=None):
        return CollectResult(artifacts=cap.artifacts, trace=cap.trace, facts=cap.facts, collected_at=cap.collected_at)

    monkeypatch.setattr("radar.diligence.run.collect", fake_collect)
    r = client.post("/api/diligence/unrun-co/run", json={"budget_s": 20})
    assert r.status_code == 200 and r.json()["status"] in ("NEEDS_REVIEW", "COMPLETE")
    assert client.post("/api/diligence/unrun-co/run", json={"budget_s": 5000}).status_code == 422
    assert client.post("/api/diligence/nope/run").status_code == 404
