"""ops: sweep steps + job recording, young-org step, persisted morning brief, LLM topic upsert, CLI."""

from __future__ import annotations

import re
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
    assert len(terms) == ops.ORGS_QUERIES_PER_RUN and days == 180 and max_orgs == 60
    [run] = _runs(db, "github")
    assert run.params["discovery"] == "young_orgs" and run.params["rotation"] == 0
    assert db.scalars(select(Company)).one().kind == "project"


def test_orgs_step_is_guarded_when_provider_lacks_discover_young_orgs(db, monkeypatch):
    import radar.providers.github as gh

    monkeypatch.setattr(gh, "GitHubProvider", _OldGitHub)
    res = ops.run_step("orgs")
    assert res["status"] == "error" and res["errors"] == 1


def test_orgs_step_rotates_through_all_thesis_queries_across_runs(db, monkeypatch):
    import radar.providers.github as gh
    from radar.thesis.loader import get_thesis

    _FakeGitHub.calls = []
    monkeypatch.setattr(gh, "GitHubProvider", _FakeGitHub)
    queries = list(get_thesis().discovery_queries)
    n_runs = -(-len(queries) // ops.ORGS_QUERIES_PER_RUN)
    for _ in range(n_runs):
        ops.run_step("orgs")
    seen = [t for terms, *_ in _FakeGitHub.calls for t in terms]
    assert set(seen) == set(queries)  # every query searched within ⌈n/10⌉ runs (same day)
    assert _FakeGitHub.calls[0][0] != _FakeGitHub.calls[1][0]
    assert [r.params["rotation"] for r in sorted(_runs(db, "github"), key=lambda r: r.started_at)] == list(
        range(n_runs))


def test_rotation_window_wraps():
    items = [f"t{i}" for i in range(25)]
    assert ops.rotation_window(items, 10, 0) == items[:10]
    assert ops.rotation_window(items, 10, 2) == items[20:] + items[:5]
    assert ops.rotation_window(items[:3], 10, 7) == items[:3]


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
    if hasattr(ops, "ensure_meet_week"):
        monkeypatch.setattr(ops, "ensure_meet_week", lambda: order.append("meet") or {"week": "2026-W41"})
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


def test_sweep_step_order_and_cli_step_list_in_sync():
    from radar import cli

    assert ops.SWEEP_STEPS == ("hn", "arxiv", "github", "orgs", "talent", "enrich", "web", "stats", "probe", "x",
                               "score", "meet", "brief")
    assert cli._ALL_STEPS == ops.ALL_STEPS
    assert [s for s in ops.ALL_STEPS if s in ops.SWEEP_STEPS] == list(ops.SWEEP_STEPS)


def test_job_runs_never_count_as_live_data():
    assert not clock.is_live_run("job:score", {})
    assert clock.is_live_run("github", {}) and not clock.is_live_run("snapshot", {})


class _Run:
    status, signals_created, errors = "ok", 0, []


def _fake_result(step: str):
    if step == "enrich":
        return {"anchors": 0, "runs": [(_Run(), None)]}
    if step == "talent":
        return {"runs": [(_Run(), None)], "pool_orgs": [], "activity_people": 0}
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

    # Rich renders help with colour and terminal-width wrapping (forced on under GitHub Actions): normalise both.
    env = {"COLUMNS": "200", "NO_COLOR": "1", "FORCE_COLOR": None, "GITHUB_ACTIONS": None}
    r = CliRunner().invoke(app, ["seed-demo", "--help"], env=env)
    out = re.sub(r"\x1b\[[0-9;]*m", "", r.output)
    assert r.exit_code == 0 and "--no-fictional" in out and "--if-empty" in out


def test_session_scope_import_smoke():
    with session_scope() as s:
        assert s is not None


# ── enrich: multi-source evidence for formation anchors ─────────────────────


def _seed_young_org(db, login="formingco", website="formingco.ai", created="2026-09-01", kind="project",
                    repo="engine"):
    from radar.pipeline.ingest import ingest_batch
    from radar.providers.base import RepoRecord, repo_ref

    ingest_batch(db, ProviderBatch(provider="github", companies=[CompanyRecord(
        ref=gh_org_ref(login), name=login.capitalize(), github_org=login, website=website, kind=kind,
        founded_date=date.fromisoformat(created), attrs={"gh_org_created_at": f"{created}T00:00:00+00:00"})],
        repositories=[RepoRecord(ref=repo_ref(f"{login}/{repo}"), full_name=f"{login}/{repo}", owner=login,
                                 owner_company=gh_org_ref(login), stars=40)]))
    db.commit()


class _Stats:
    stats = HttpStats()


class _FakeWeb:
    calls: list = []

    def __init__(self, *a, **kw) -> None:
        self.client = _Stats()

    def crawl(self, url, *, company_name=None, max_pages=6):
        from radar.providers.base import NormalizedSignal, domain_ref

        _FakeWeb.calls.append((url, company_name))
        ref = domain_ref("formingco.ai")
        return ProviderBatch(provider="website", signals=[NormalizedSignal(
            entity=ref, signal_type="hiring_active", source_type="website", source_url="https://formingco.ai/careers",
            title="2 open technical roles", event_at=clock.now(), normalized_value=0.3)])


class _FakeHN:
    calls: list = []

    def __init__(self, *a, **kw) -> None:
        self.client = _Stats()

    def org_attention(self, entity, terms, *, github_owner=None, domain=None, days=180, metric_days=90):
        from datetime import UTC, datetime

        from radar.providers.base import MetricPoint, NormalizedSignal

        _FakeHN.calls.append((entity.key, list(terms), github_owner, domain))
        return ProviderBatch(provider="hackernews", metrics=[
            MetricPoint(entity=entity, metric="hn_probe_mentions", day=clock.today(), value=1)],
            signals=[NormalizedSignal(
                entity=entity, signal_type="hn_show", source_type="hackernews",
                source_url="https://news.ycombinator.com/item?id=1", title="Show HN: FormingCo",
                event_at=datetime(2026, 9, 20, tzinfo=UTC), normalized_value=0.5,
                metadata={"link": "https://github.com/formingco/engine"})])


class _FakeArxiv:
    calls: list = []

    def __init__(self, *a, **kw) -> None:
        self.client = _Stats()

    def search_code_for(self, owner, phrases, *, max_results=10, days=21):
        from datetime import UTC, datetime

        from radar.providers.base import NormalizedSignal, PaperRecord, Relation, RepoRecord, paper_ref, repo_ref

        _FakeArxiv.calls.append((owner, list(phrases)))
        pref, rref = paper_ref("2609.00001"), repo_ref(f"{owner}/policy")
        return ProviderBatch(
            provider="arxiv",
            papers=[PaperRecord(ref=pref, arxiv_id="2609.00001", title="A policy", url="https://arxiv.org/abs/2609.00001",
                                published_at=datetime(2026, 9, 10, tzinfo=UTC),
                                code_urls=[f"https://github.com/{owner}/policy"])],
            repositories=[RepoRecord(ref=rref, full_name=f"{owner}/policy", owner=owner, attrs={"stub": True})],
            relations=[Relation(kind="code_for", src=pref, dst=rref, source_url="https://arxiv.org/abs/2609.00001")],
            signals=[NormalizedSignal(entity=rref, signal_type="paper_with_code", source_type="arxiv",
                                      source_url="https://arxiv.org/abs/2609.00001", title="Code for arXiv paper",
                                      event_at=datetime(2026, 9, 10, tzinfo=UTC), normalized_value=0.6,
                                      metadata={"verified_link": True})])


@pytest.fixture()
def fake_sources(monkeypatch):
    import radar.providers.arxiv as ax
    import radar.providers.hackernews as hn
    import radar.providers.website as web

    for cls in (_FakeWeb, _FakeHN, _FakeArxiv):
        cls.calls = []
    monkeypatch.setattr(web, "WebsiteProvider", _FakeWeb)
    monkeypatch.setattr(hn, "HackerNewsProvider", _FakeHN)
    monkeypatch.setattr(ax, "ArxivProvider", _FakeArxiv)


def test_enrichment_targets_young_orgs_only(db):
    _seed_young_org(db)
    _seed_young_org(db, login="oldcorp", website="https://oldcorp.com", created="2024-01-01", kind="company")
    _seed_young_org(db, login="unilab", website="https://cs.stanford.edu/unilab", created="2026-09-15",
                    repo="sim")
    targets = {t["login"]: t for t in ops.enrichment_targets()}
    assert set(targets) == {"formingco", "unilab"}  # oldcorp is not young
    t = targets["formingco"]
    assert t["domain"] == "formingco.ai" and t["website"] == "https://formingco.ai"
    assert t["top_repo"] == "formingco/engine" and not t["candidate"]
    assert targets["unilab"]["website"] is None  # academic sites are not crawled


def test_enrich_step_records_web_hn_arxiv_evidence_on_the_anchor(db, fake_sources):
    from radar.models import MetricSnapshot, Repository, Signal

    _seed_young_org(db)
    res = ops.run_step("enrich")
    assert res["status"] == "ok", res
    assert res["anchors"] == 1 and res["sites"] == 1 and res["signals"] == 3
    assert _FakeWeb.calls == [("https://formingco.ai", "Formingco")]
    [(key, terms, owner, dom)] = _FakeHN.calls
    assert key == "github_org:formingco" and terms == ["formingco.ai", "formingco/engine", "formingco"]
    assert (owner, dom) == ("formingco", "formingco.ai")
    assert _FakeArxiv.calls == [("formingco", ["github.com/formingco", "engine"])]
    for prov in ("website", "hackernews", "arxiv"):
        assert len(_runs(db, prov)) == 1
    db.expire_all()
    company = db.scalars(select(Company).where(Company.github_org == "formingco")).one()
    repos = {r.full_name: r for r in db.scalars(select(Repository))}
    assert repos["formingco/policy"].company_id == company.id  # code repo attached to the org owning it
    sigs = {(s.signal_type, s.source_type): s for s in db.scalars(select(Signal))}
    assert sigs[("hiring_active", "website")].entity_id == company.id
    assert sigs[("hn_show", "hackernews")].entity_id == company.id
    assert sigs[("paper_with_code", "arxiv")].entity_id == repos["formingco/policy"].id
    assert db.scalar(select(MetricSnapshot.value).where(MetricSnapshot.metric == "hn_probe_mentions",
                                                        MetricSnapshot.entity_id == company.id)) == 1
    # A second run the same day does not re-crawl the site (recently fetched) and creates no duplicates.
    from radar.models import WebPage

    db.add(WebPage(company_id=company.id, url="https://formingco.ai", page_kind="home",
                   last_fetched_at=clock.wall_now()))
    db.commit()
    res2 = ops.run_step("enrich")
    assert res2["sites"] == 0 and res2["result"]["sites_skipped_recent"] == 1 and res2["signals"] == 0


def test_enrich_prefers_formation_candidates_and_caps_budget(db, fake_sources):
    from radar.models import FormationCandidate

    _seed_young_org(db)
    _seed_young_org(db, login="oldcorp", website="https://oldcorp.com", created="2024-01-01", kind="company")
    old = db.scalars(select(Company).where(Company.github_org == "oldcorp")).one()
    db.add(FormationCandidate(id=uuid.uuid4(), slug="oldcorp", kind="team", anchor_type="company", anchor_id=old.id,
                              title="x", score=40, tier="early", label="FORMATION SIGNALS DETECTED", components={},
                              convergence={}, why_now={}, independent_signals=1, day=clock.today()))
    db.commit()
    assert [t["login"] for t in ops.enrichment_targets()] == ["oldcorp", "formingco"]
    assert [t["login"] for t in ops.enrichment_targets(limit=1)] == ["oldcorp"]
    res = ops.enrich_formation_anchors(limit=1)
    assert res["anchors"] == 1 and res["candidates"] == 1


def test_stats_step_retries_missing_stats_and_is_guarded(db, monkeypatch):
    import radar.providers.github as gh
    from radar.providers.base import MetricPoint, repo_ref

    _seed_young_org(db)
    calls: list = []

    class _GH:
        def __init__(self) -> None:
            self.client = _Stats()

        def retry_stats(self, full_names):
            calls.append(list(full_names))
            return ProviderBatch(provider="github", metrics=[
                MetricPoint(entity=repo_ref(n), metric=m, day=clock.today(), value=3)
                for n in full_names for m in ("commits_week", "contributors")])

    monkeypatch.setattr(gh, "GitHubProvider", _GH)
    assert ops.young_org_repos_missing_stats() == ["formingco/engine"]
    res = ops.run_step("stats")
    assert res["status"] == "ok" and calls == [["formingco/engine"]]
    assert ops.young_org_repos_missing_stats() == []
    assert ops.run_step("stats") == {"status": "ok", "signals": 0, "errors": 0, "repos": 0} and len(calls) == 1

    monkeypatch.setattr(gh, "GitHubProvider", _OldGitHub)
    res = ops.run_step("stats")
    assert res["status"] == "error"


# ── enrich: public GitHub org memberships of anchor members ──────────────────


class _FakeGH:
    calls: list = []

    def __init__(self, *a, **kw) -> None:
        self.client = _Stats()

    def fetch_public_orgs(self, logins, *, max_calls=150):
        from radar.providers.base import Relation, gh_user_ref
        from radar.providers.github import PUBLIC_ORGS_ATTR

        _FakeGH.calls.append(list(logins))
        orgs = {"ada": [{"login": "openai", "name": "openai"}], "ben": []}
        return ProviderBatch(provider="github", relations=[
            Relation(kind="member", src=gh_user_ref(lg), dst=gh_user_ref(lg),
                     attrs={PUBLIC_ORGS_ATTR: orgs[lg], "observed_at": "2026-10-06T09:00:00+00:00"},
                     source_url=f"https://github.com/{lg}") for lg in logins if lg in orgs])


def test_enrich_records_public_org_memberships_with_provenance(db, fake_sources, monkeypatch):
    import radar.providers.github as gh
    from radar.models import FieldProvenance, Person, RepoContributor, Repository

    _FakeGH.calls = []
    monkeypatch.setattr(gh, "GitHubProvider", _FakeGH)
    _seed_young_org(db)
    r = db.scalars(select(Repository).where(Repository.full_name == "formingco/engine")).one()
    for handle, n in (("ada", 30), ("ben", 5), ("claude", 50), ("drive-by", 1)):
        p = Person(slug=handle, name=handle.title(), github_handle=handle)
        db.add(p)
        db.flush()
        db.add(RepoContributor(repository_id=r.id, person_id=p.id, contributions=n))
    db.commit()
    res = ops.run_step("enrich")
    assert res["status"] == "ok", res
    assert _FakeGH.calls == [["ada", "ben"]]  # bots and 1-commit drive-bys are not looked up
    assert res["result"]["people_orgs_checked"] == 2
    db.expire_all()
    people = {p.github_handle: p for p in db.scalars(select(Person))}
    ada = people["ada"].attrs
    assert [o["login"] for o in ada["github_orgs"]] == ["openai"]
    assert ada["github_orgs"][0]["observed_at"] == "2026-10-06T09:00:00+00:00"
    assert ada["github_orgs_checked_at"] == "2026-10-06T09:00:00+00:00"
    assert people["ben"].attrs["github_orgs"] == [] and people["ben"].attrs["github_orgs_checked_at"]
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.field == "github_orgs")).all()
    assert len(prov) == 1
    pr = prov[0]
    assert pr.entity_id == people["ada"].id and pr.value == ["openai"] and pr.epistemic == "FACT"
    assert pr.source_url == "https://github.com/ada" and pr.note == "Public GitHub organization membership"
    assert "github_orgs" not in {c.slug for c in db.scalars(select(Company))}  # no company rows for orgs
    assert db.scalar(select(func.count()).select_from(Company)) == 1
    # Checked people are not re-requested within the recheck window.
    _FakeGH.calls = []
    ops.run_step("enrich")
    assert _FakeGH.calls == []
