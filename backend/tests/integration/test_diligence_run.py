"""End-to-end diligence runs from the fixture capture: persistence, determinism, capture round trip, status."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from radar.diligence import capture as cap_mod
from radar.diligence.collect import CollectResult
from radar.diligence.run import DiligenceError, diligence_status, run_diligence
from radar.models import Company, DiligenceArtifact, DiligenceClaim, DiligenceRun, ResearchReport, ReviewDecision
from tests.fixtures.diligence.helpers import SAMPLE_CAPTURE


def _company(db, **kw) -> Company:
    c = Company(slug="quillserve-fixture", name="Quillserve", website="https://quillserve.example",
                domain="quillserve.example", github_org="quillserve-fixture", thesis_tags=[], **kw)
    db.add(c)
    db.flush()
    return c


def _count(db, model, run_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.run_id == run_id))


def test_replay_persists_artifacts_claims_report_and_summary(db):
    c = _company(db)
    run = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    assert run.status == "complete", run.error
    assert run.mode == "replay" and run.method_version.startswith("dil-")
    assert run.collected_at == datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert _count(db, DiligenceArtifact, run.id) == run.sources_analyzed == 29
    claims = list(db.scalars(select(DiligenceClaim).where(DiligenceClaim.run_id == run.id).order_by(DiligenceClaim.ordinal)))
    assert [c.ordinal for c in claims] == list(range(1, len(claims) + 1)) and len(claims) == 12
    stars = next(c for c in claims if "10k GitHub stars" in c.claim_text)
    assert stars.verification_status == "CONTRADICTED" and stars.status_reason.startswith("R1:")
    assert stars.contradictory_evidence and stars.source_id is not None and stars.company_id == c.id
    assert all(cl.epistemic == "COMPANY_CLAIM" and cl.extracted_by == "rules" for cl in claims)
    assert any(cl.test_plan and cl.test_plan["execution"]["status"] == "not_executed" for cl in claims)

    rep = db.get(ResearchReport, run.report_id)
    assert rep.kind == "diligence" and rep.entity_type == "company" and rep.entity_id == c.id
    assert rep.title == "Technical Diligence — Quillserve" and rep.generator == "rules" and rep.model is None
    assert rep.sources == rep.body["sources"] and rep.sources[0]["id"] == "S1"
    assert rep.markdown.startswith("# Technical Diligence — Quillserve")
    assert any(t["type"] == "timing" for t in rep.trace)
    assert run.summary["by_status"]["CONTRADICTED"] == 1 and run.summary["claims"] == 12
    assert 0 < run.evidence_coverage < 1
    assert any(t.get("type") == "facts" for t in run.trace)


def test_replay_is_deterministic(db):
    c = _company(db)
    r1 = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    r2 = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    b1, b2 = db.get(ResearchReport, r1.report_id), db.get(ResearchReport, r2.report_id)
    assert b1.body == b2.body and b1.markdown == b2.markdown and r1.summary == r2.summary


def test_replay_and_replay_all_never_touch_the_network(db, monkeypatch, tmp_path):
    from radar.providers import http

    def boom(*a, **k):
        raise AssertionError("network used during replay")

    monkeypatch.setattr(http.HttpClient, "request", boom)
    _company(db)
    run = cap_mod.replay(db, SAMPLE_CAPTURE)
    assert run.status == "complete"
    shutil.copy(SAMPLE_CAPTURE, tmp_path / "quillserve-fixture.json.gz")
    (tmp_path / "unknown-co.json.gz").write_bytes((tmp_path / "quillserve-fixture.json.gz").read_bytes())
    results = cap_mod.replay_all(db, directory=tmp_path)
    assert [r["status"] for r in results] == ["complete", "complete"]  # both files name the same fixture company
    bad = tmp_path / "broken.json.gz"
    bad.write_bytes(b"not gzip")
    assert any(r["status"] == "skipped" for r in cap_mod.replay_all(db, directory=tmp_path))


def test_export_run_round_trip_is_byte_stable(db, tmp_path):
    c = _company(db)
    run = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    p1 = cap_mod.export_run(db, run.id, tmp_path / "a.json.gz")
    p2 = cap_mod.export_run(db, run.id, tmp_path / "b.json.gz")
    assert p1.read_bytes() == p2.read_bytes()
    original, exported = cap_mod.load(SAMPLE_CAPTURE), cap_mod.load(p1)
    assert [a.to_dict() for a in exported.artifacts] == [a.to_dict() for a in original.artifacts]
    assert exported.facts == original.facts and exported.collected_at == original.collected_at


def test_live_run_with_offline_collection_and_capture(db, monkeypatch, tmp_path):
    c = _company(db)
    cap = cap_mod.load(SAMPLE_CAPTURE)

    def fake_collect(session, company, *, budget_s, fetcher=None):
        assert company.id == c.id and budget_s == 30
        return CollectResult(artifacts=cap.artifacts, trace=cap.trace, facts=cap.facts, collected_at=cap.collected_at,
                             partial=True, errors=["hackernews: TimeoutError"])

    monkeypatch.setattr("radar.diligence.run.collect", fake_collect)
    out = tmp_path / "live.json.gz"
    run = run_diligence(db, c.slug, live=True, budget_s=30, capture=True, capture_path=out)
    assert run.status == "complete" and run.mode == "live" and run.summary["partial"] is True
    assert out.exists() and len(cap_mod.load(out).artifacts) == 29
    assert any(t.get("type") == "errors" for t in run.trace)


def test_failure_is_recorded_without_partial_rows(db, monkeypatch):
    c = _company(db)

    def broken(*a, **k):
        raise RuntimeError("analysis exploded")

    monkeypatch.setattr("radar.diligence.run.analyze", broken)
    run = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    assert run.status == "error" and "analysis exploded" in run.error and run.report_id is None
    assert _count(db, DiligenceArtifact, run.id) == 0 and _count(db, DiligenceClaim, run.id) == 0
    assert diligence_status(db, c.id)["status"] == "NOT_RUN"
    assert "analysis exploded" in diligence_status(db, c.id)["last_error"]


def test_bad_references_raise(db, tmp_path):
    with pytest.raises(DiligenceError):
        run_diligence(db, "no-such-company", live=False, capture_path=SAMPLE_CAPTURE)
    c = _company(db)
    with pytest.raises(DiligenceError):
        run_diligence(db, c, live=False, capture_path=tmp_path / "missing.json.gz")


def test_status_lifecycle(db):
    c = _company(db)
    assert diligence_status(db, c.id)["status"] == "NOT_RUN"
    running = DiligenceRun(company_id=c.id, status="running", mode="live", method_version="x",
                           started_at=datetime.now(timezone.utc))
    db.add(running)
    db.flush()
    assert diligence_status(db, c.id)["status"] == "IN_PROGRESS"
    running.started_at = datetime.now(timezone.utc) - timedelta(hours=2)  # abandoned
    db.flush()
    assert diligence_status(db, c.id)["status"] == "NOT_RUN"

    run = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    st = diligence_status(db, c.id)
    assert st["status"] == "NEEDS_REVIEW" and st["run_id"] == str(run.id)
    kinds = {(p["target_type"], p["target_id"].split(":")[-1]) for p in st["pending_review"]}
    assert ("underwriting", "OPEN_SOURCE_HEALTH") in kinds and ("underwriting", "SECURITY") in kinds
    assert any(t == "claim" for t, _ in kinds)
    assert ("underwriting", "CLAIM_VERIFICATION") not in kinds  # evidence-scale categories never need review
    for p in st["pending_review"]:
        db.add(ReviewDecision(target_type=p["target_type"], target_id=p["target_id"], decision="ACCEPT"))
    db.flush()
    st = diligence_status(db, c.id)
    assert st["status"] == "COMPLETE" and st["pending_review"] == [] and st["counts"]["CONTRADICTED"] == 1


def test_collect_orchestration_offline_end_to_end(db):
    """Every stage of collect() against a stub (website, GitHub, registries, OSV, HN) plus radar DB papers."""
    from radar.diligence.collect import GH_API, collect
    from radar.models import ResearchPaper
    from tests.fixtures.diligence.helpers import stub_fetcher

    c = Company(slug="acme-fixture", name="Acmefix", website="https://acme.example/", thesis_tags=[])
    db.add(c)
    db.add(ResearchPaper(arxiv_id="2609.00001", title="Paged kernels", abstract="Acmefix kernels for MI300X.",
                         code_urls=["https://github.com/acme-fixture/engine"]))
    db.flush()
    fn = "acme-fixture/engine"
    routes = {
        "https://acme.example/": '<html><body><p>Acmefix is 3x faster than vLLM 0.8 on H100.</p>'
                                 '<a href="https://github.com/acme-fixture">GitHub</a><a href="/docs">Docs</a></body></html>',
        "https://acme.example/docs": "<html><body><p>Acmefix supports AMD MI300X.</p></body></html>",
        f"{GH_API}/orgs/acme-fixture/repos": [{"full_name": fn, "stargazers_count": 10, "fork": False}],
        f"{GH_API}/repos/{fn}": {"full_name": fn, "default_branch": "main", "license": {"spdx_id": "MIT"},
                                 "stargazers_count": 10},
        f"{GH_API}/repos/{fn}/git/trees/main": {"tree": [{"path": "requirements.txt", "type": "blob"}]},
        f"{GH_API}/repos/{fn}/contents/requirements.txt": (200, "requests==2.19.0\n", "text/plain"),
        "https://api.osv.dev/v1/vulns/GHSA-1": {"aliases": [], "summary": "x"},
        "https://hn.algolia.com/api/v1/search": {"hits": []},
    }
    f, stub = stub_fetcher(routes, post={"https://api.osv.dev/v1/querybatch": {"results": [{"vulns": [{"id": "GHSA-1"}]}]}})
    res = collect(db, c, fetcher=f)
    keys = {a.key for a in res.artifacts}
    assert {"web:https://acme.example/", "web:https://acme.example/docs", f"repo:{fn}", f"file:{fn}:requirements.txt",
            "osv:batch", "paper:2609.00001"} <= keys
    assert res.facts.github_org == "acme-fixture"  # discovered from the site's own GitHub link
    assert [a.key for a in res.artifacts] == sorted(keys)
    stages = [t["stage"] for t in res.trace if t.get("type") == "stage"]
    assert stages == ["website", "github", "registries", "osv", "hackernews", "papers"]
    assert all(t.get("url") and "ms" in t for t in res.trace if t.get("type") == "fetch")
    assert res.errors == []
