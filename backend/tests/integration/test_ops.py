"""ops: sweep steps + job recording, young-org step, persisted morning brief, LLM topic upsert, CLI."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from radar import clock, ops
from radar.db import session_scope
from radar.models import Company, EntityTopic, IngestionRun, ResearchReport, Topic
from radar.providers.base import CompanyRecord, ProviderBatch, gh_org_ref
from radar.providers.http import HttpStats


def _runs(db, provider: str) -> list[IngestionRun]:
    db.expire_all()
    return list(db.scalars(select(IngestionRun).where(IngestionRun.provider == provider)))


# ── duplicate-topic bug ─────────────────────────────────────────────────────


def test_refine_topics_with_llm_upserts_instead_of_duplicating(db, monkeypatch):
    topic = Topic(id=uuid.uuid4(), key="robotics.tactile", theme="robotics", label="Tactile")
    company = Company(id=uuid.uuid4(), slug="tactica", name="Tactica", kind="company", thesis_tags=[], attrs={})
    db.add_all([topic, company])
    db.flush()
    db.add(EntityTopic(entity_type="company", entity_id=company.id, topic_id=topic.id, score=0.5,
                       method="keyword", evidence=["tactile"]))
    db.commit()
    import radar.llm.search_parse as sp

    monkeypatch.setattr(sp, "llm_refine_topics", lambda text, keys: [(keys[0], 0.92)])
    assert ops.refine_topics_with_llm() == 1
    db.expire_all()
    [et] = db.scalars(select(EntityTopic)).all()
    assert et.method == "llm" and et.score == pytest.approx(0.92) and "llm-adjudicated" in et.evidence
    assert ops.refine_topics_with_llm() == 0  # llm rows are not re-adjudicated


def test_recompute_with_llm_topics_commits(db, monkeypatch):
    import radar.llm.search_parse as sp

    monkeypatch.setattr(sp, "llm_refine_topics", lambda text, keys: [(keys[0], 0.9)])
    from radar.pipeline.ingest import ingest_batch

    ingest_batch(db, ProviderBatch(provider="github", companies=[CompanyRecord(
        ref=gh_org_ref("tactica"), name="Tactica", github_org="tactica", website="https://tactica.dev",
        description="tactile sensing skin for robot hands and haptic grippers")]))
    db.commit()
    out = ops.recompute(use_llm_topics=True)
    assert "scored" in out
    db.expire_all()
    rows = db.execute(select(EntityTopic.entity_id, EntityTopic.topic_id, EntityTopic.method)).all()
    assert any(m == "llm" for *_, m in rows)
    assert len({(e, t) for e, t, _ in rows}) == len(rows)  # never two rows for one (entity, topic)


# ── young orgs step ─────────────────────────────────────────────────────────


class _FakeClient:
    stats = HttpStats()


class _FakeGitHub:
    calls: list = []

    def __init__(self) -> None:
        self.client = _FakeClient()

    def discover_young_orgs(self, terms, *, created_within_days=150, max_orgs=40, **kw):
        _FakeGitHub.calls.append((list(terms), created_within_days, max_orgs))
        return ProviderBatch(provider="github", companies=[
            CompanyRecord(ref=gh_org_ref("formingco"), name="formingco", github_org="formingco",
                          attrs={"gh_org_created_at": "2026-09-01"})])


class _OldGitHub:
    def __init__(self) -> None:
        self.client = _FakeClient()


def test_orgs_step_calls_discover_young_orgs_and_creates_projects(db, monkeypatch):
    import radar.providers.github as gh

    _FakeGitHub.calls = []
    monkeypatch.setattr(gh, "GitHubProvider", _FakeGitHub)
    res = ops.run_step("orgs")
    assert res["status"] == "ok"
    [(terms, days, max_orgs)] = _FakeGitHub.calls
    assert len(terms) == ops.ORGS_QUERIES_PER_RUN and days == 150 and max_orgs == 40
    [run] = _runs(db, "github")
    assert run.params["discovery"] == "young_orgs"
    assert db.scalars(select(Company)).one().kind == "project"


def test_orgs_step_is_guarded_when_provider_lacks_discover_young_orgs(db, monkeypatch):
    import radar.providers.github as gh

    monkeypatch.setattr(gh, "GitHubProvider", _OldGitHub)
    res = ops.run_step("orgs")
    assert res["status"] == "error" and res["errors"] == 1


def test_rotating_subset_covers_all_terms():
    items = [f"t{i}" for i in range(25)]
    seen = set()
    for d in range(10):
        sub = ops.rotating_subset(items, 10, date(2026, 10, 1 + d))
        assert len(sub) == 10 and len(set(sub)) == 10
        seen |= set(sub)
    assert seen == set(items)
    assert ops.rotating_subset(items[:3], 10, date(2026, 10, 1)) == items[:3]


# ── sweep ordering + job recording ──────────────────────────────────────────


def test_sweep_runs_steps_in_canonical_order_and_records_jobs(db, monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(ops, "_step_ingest", lambda st: order.append(st) or _fake_result(st))
    monkeypatch.setattr(ops, "recompute", lambda: order.append("score") or {"scored": 1})
    monkeypatch.setattr(ops, "persist_morning_brief", lambda: order.append("brief") or {"date": "2026-10-06"})
    out = ops.sweep(["brief", "hn", "score", "github"])
    assert order == ["hn", "github", "score", "brief"] and list(out) == order
    assert out["score"]["status"] == "ok" and out["score"]["result"] == {"scored": 1}
    assert len(_runs(db, "job:score")) == 1 and len(_runs(db, "job:brief")) == 1
    order.clear()
    ops.sweep()
    assert order == list(ops.SWEEP_STEPS)
    with pytest.raises(ValueError):
        ops.sweep(["bogus"])


def test_failed_job_is_recorded_and_sweep_continues(db, monkeypatch):
    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(ops, "recompute", boom)
    monkeypatch.setattr(ops, "persist_morning_brief", lambda: {"ok": True})
    out = ops.sweep(["score", "brief"])
    assert out["score"]["status"] == "error" and "nope" in out["score"]["error"]
    assert out["brief"]["status"] == "ok"
    [run] = _runs(db, "job:score")
    assert run.status == "error" and run.errors


def test_job_runs_never_count_as_live_data():
    assert not clock.is_live_run("job:score", {})
    assert clock.is_live_run("github", {}) and not clock.is_live_run("snapshot", {})


class _Run:
    status, signals_created, errors = "ok", 0, []


def _fake_result(step: str):
    return [(_Run(), None)] if step == "web" else (_Run(), None)


# ── persisted morning brief ─────────────────────────────────────────────────


def test_persist_morning_brief_is_one_per_day(db):
    a = ops.persist_morning_brief()
    b = ops.persist_morning_brief()
    assert a["date"] == clock.today().isoformat() and not a["replaced"] and b["replaced"] and a["id"] == b["id"]
    db.expire_all()
    [r] = db.scalars(select(ResearchReport).where(ResearchReport.kind == "morning_brief")).all()
    assert r.body["date"] == clock.today().isoformat() and r.markdown and "what_changed" in r.body
    c = ops.persist_morning_brief(date(2026, 10, 5))
    assert not c["replaced"]
    assert db.scalar(select(func.count()).select_from(ResearchReport)) == 2


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_sweep_only_and_unknown_step(db, monkeypatch):
    from radar.cli import app

    monkeypatch.setattr(ops, "persist_morning_brief", lambda: {"date": "2026-10-06", "replaced": False})
    runner = CliRunner()
    r = runner.invoke(app, ["sweep", "--only", "brief"])
    assert r.exit_code == 0, r.output
    assert "brief" in r.output
    r = runner.invoke(app, ["sweep", "--only", "nope"])
    assert r.exit_code != 0
    monkeypatch.setattr(ops, "persist_morning_brief", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = runner.invoke(app, ["sweep", "--only", "brief"])
    assert r.exit_code == 1  # every requested step failed


def test_cli_seed_demo_flags_exist():
    from radar.cli import app

    r = CliRunner().invoke(app, ["seed-demo", "--help"])
    assert r.exit_code == 0 and "--no-fictional" in r.output and "--if-empty" in r.output


def test_session_scope_import_smoke():
    with session_scope() as s:
        assert s is not None
