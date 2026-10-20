"""Talent pool: verified frontier-lab contributors -> ingest, relationships, talent signals, ops step, CLI.

HTTP is mocked with respx from tests/fixtures/github/talent.json (fictional people/orgs); "today" is 2026-10-06.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import respx
from sqlalchemy import select
from typer.testing import CliRunner

from radar import clock, ops
from radar.db import session_scope
from radar.models import Company, CompanyPerson, IngestionRun, Person, Relationship, RepoContributor, Repository, Signal
from radar.pipeline import relationships as rel
from radar.pipeline.ingest import ingest_batch
from radar.providers.base import (
    CompanyRecord,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    gh_org_ref,
    gh_user_ref,
    repo_ref,
)
from radar.providers.github import GitHubProvider, TalentSource
from radar.providers.http import HttpClient, HttpStats
from tests.unit.test_github_provider import _talent_router

ACME = TalentSource(org="acme-lab", label="Acme Lab", tier=1, theme_hint=["ai"], repos=["core", "sim"])


def _provider() -> GitHubProvider:
    p = GitHubProvider(client=HttpClient(min_interval=0, max_retries=0), token="test-token", sleep=lambda s: None)
    p.search_interval = 0
    return p


def _pool_batch(**kw) -> ProviderBatch:
    with respx.mock(assert_all_called=False) as router:
        _talent_router(router, **kw)
        return _provider().talent_pool([ACME], contributors_per_repo=5)


def _activity_batch() -> ProviderBatch:
    with respx.mock(assert_all_called=False) as router:
        _talent_router(router)
        return _provider().recent_activity(["Ada", "ben", "cy"], skip_orgs=["acme-lab"], max_calls=40)


def _person(db, login: str) -> Person:
    db.expire_all()
    return db.scalars(select(Person).where(Person.github_handle.ilike(login))).one()


def _signals(db, kind: str) -> list[Signal]:
    db.expire_all()
    return list(db.scalars(select(Signal).where(Signal.signal_type == kind)))


def test_talent_pool_ingest_persists_verified_history_without_employment(db):
    ingest_batch(db, _pool_batch())
    db.commit()
    ada, ben = _person(db, "ada"), _person(db, "ben")
    [core] = [e for e in ada.attrs["lab_contributions"] if e["repo"] == "acme-lab/core"]
    assert core["commits"] == 240 and core["label"] == "Acme Lab" and core["tier"] == 1
    assert core["first_commit_at"].startswith("2021-03-07") and ada.attrs["talent_pool_since"]
    assert {e["repo"] for e in ben.attrs["lab_contributions"]} == {"acme-lab/core", "acme-lab/sim"}
    assert ada.current_company == "acme-lab"  # self-reported profile field, kept as typed
    # Lab repos are not owned by a tracked company: no membership / employment is derived from commits.
    assert db.scalars(select(Company)).all() == []
    assert db.scalars(select(CompanyPerson)).all() == []
    repo = db.scalars(select(Repository).where(Repository.full_name == "acme-lab/core")).one()
    assert repo.attrs["talent_source"]["label"] == "Acme Lab" and repo.company_id is None
    rc = db.scalars(select(RepoContributor).where(RepoContributor.person_id == ada.id)).one()
    assert rc.contributions == 240 and rc.first_commit_at == datetime(2021, 3, 7, tzinfo=UTC)

    # A later refresh with only one repo keeps the other repo's history (merge, not overwrite).
    later = ProviderBatch(provider="github", people=[PersonRecord(
        ref=gh_user_ref("ben"), name="ben", github_handle="ben",
        attrs={"lab_contributions": [{"org": "acme-lab", "label": "Acme Lab", "tier": 1, "repo": "acme-lab/core",
                                      "commits": 35, "first_commit_at": None, "last_commit_at": None}],
               "talent_pool_observed_at": "2026-10-06T00:00:00+00:00"})])
    ingest_batch(db, later)
    db.commit()
    ben = _person(db, "ben")
    by_repo = {e["repo"]: e for e in ben.attrs["lab_contributions"]}
    assert by_repo["acme-lab/core"]["commits"] == 35 and by_repo["acme-lab/core"]["first_commit_at"].startswith("2023")
    assert by_repo["acme-lab/sim"]["commits"] == 77
    assert ben.name == "Ben Ng"  # a minimal refresh record never overwrites the profile name


def test_relationships_lab_evidence_and_prior_collaboration(db):
    ingest_batch(db, _pool_batch())
    db.commit()
    out = rel.build(db)
    db.commit()
    assert out["talent_pool"] == 3 and out["lab_alumni_cluster"] == 0
    ada, ben = _person(db, "ada"), _person(db, "ben")
    ct = db.scalars(select(Relationship).where(Relationship.kind == "CONTRIBUTED_TO",
                                               Relationship.src_id == ada.id)).one()
    assert ct.epistemic == "VERIFIED"
    ev = ct.evidence[0]
    assert ev["detail"].startswith("240 commits to acme-lab/core") and "not employment" in ev["detail"]
    assert ev["lab"] == "Acme Lab" and ev["lab_tier"] == 1
    cw = db.scalars(select(Relationship).where(Relationship.kind == "CONTRIBUTED_WITH")).all()
    pair = next(e for e in cw if {e.src_id, e.dst_id} == {ada.id, ben.id})
    assert pair.evidence[0]["prior_collaboration"] and pair.evidence[0]["lab"] == "Acme Lab"
    assert pair.first_seen_at == datetime(2023, 5, 7, tzinfo=UTC)  # overlap of the two commit spans
    assert "overlapping 2023-05-07" in pair.evidence[0]["detail"]
    assert not db.scalars(select(Relationship).where(Relationship.kind.in_(["MEMBER_OF", "WORKED_WITH"]))).all()


def test_recent_activity_ingest_new_project_joined_org_and_checked_marker(db):
    ingest_batch(db, _pool_batch())
    stats = ingest_batch(db, _activity_batch())
    db.commit()
    assert not stats.errors, stats.errors
    ada = _person(db, "ada")
    [np_] = [s for s in _signals(db, "new_project") if s.meta["repo"] == "Ada/tactile-policy"]
    assert np_.entity_id == ada.id and np_.epistemic == "FACT" and np_.event_at == datetime(2026, 9, 1, tzinfo=UTC)
    joined = {s.entity_id: s for s in _signals(db, "joined_young_org")}
    assert joined[ada.id].meta["commits"] == 12 and joined[ada.id].meta["org"] == "newco"
    newco = db.scalars(select(Company).where(Company.github_org == "newco")).one()
    cp = db.scalars(select(CompanyPerson).where(CompanyPerson.company_id == newco.id)).all()
    assert {c.person_id for c in cp} == {ada.id, _person(db, "ben").id}
    assert ada.attrs["activity_checked_at"]
    orgs = {o["login"]: o for o in ada.attrs["github_orgs"]}
    assert orgs["newco"]["created_at"] == "2026-08-01T00:00:00Z"
    # Re-ingesting the same activity creates no duplicate events.
    again = ingest_batch(db, _activity_batch())
    db.commit()
    assert again.signals_created == 0
    rel.build(db)
    db.commit()
    mem = db.scalars(select(Relationship).where(Relationship.kind == "MEMBER_OF", Relationship.src_id == ada.id)).one()
    assert mem.epistemic == "VERIFIED"  # public org membership page, not a self-reported profile


def _young_org_with(db, contributors: dict[str, int], *, login: str = "newco", created: str = "2026-08-01"):
    ingest_batch(db, ProviderBatch(
        provider="github",
        companies=[CompanyRecord(ref=gh_org_ref(login), name=login.capitalize(), github_org=login, kind="project",
                                 founded_date=date.fromisoformat(created),
                                 attrs={"gh_org_created_at": f"{created}T00:00:00+00:00"})],
        repositories=[RepoRecord(ref=repo_ref(f"{login}/engine"), full_name=f"{login}/engine", owner=login,
                                 owner_type="Organization", owner_company=gh_org_ref(login), stars=30,
                                 description="Tactile robot hand control",
                                 gh_created_at=datetime.fromisoformat(f"{created}T00:00:00+00:00"))],
        relations=[Relation(kind="contributor", src=gh_user_ref(lg), dst=repo_ref(f"{login}/engine"),
                            attrs={"contributions": n, "first_commit_at": "2026-08-09T00:00:00+00:00"})
                   for lg, n in contributors.items()]))
    db.commit()


def test_lab_alumni_cluster_and_commit_based_joined_young_org(db):
    ingest_batch(db, _pool_batch())
    db.commit()
    _young_org_with(db, {"Ada": 12, "ben": 5, "cy": 1})
    out = rel.build(db)
    db.commit()
    assert out["lab_alumni_cluster"] == 1
    [cl] = _signals(db, "lab_alumni_cluster")
    newco = db.scalars(select(Company).where(Company.github_org == "newco")).one()
    assert cl.entity_type == "company" and cl.entity_id == newco.id and cl.epistemic == "FACT"
    assert [m["login"] for m in cl.meta["members"]] == ["Ada", "ben"]  # cy has 1 commit here: not counted
    assert cl.meta["lab_orgs"] == ["acme-lab"] and cl.meta["members"][0]["labs"][0]["repo"] == "acme-lab/core"
    assert "not employment" in cl.description and "worked at" not in cl.description.lower()
    assert cl.event_at == datetime(2026, 8, 9, tzinfo=UTC)  # when the second alum's commits started
    joined = _signals(db, "joined_young_org")
    assert {s.entity_id for s in joined} == {_person(db, "ada").id, _person(db, "ben").id}
    assert all(s.meta["public_member"] is False and s.meta["org"] == "newco" for s in joined)
    # Idempotent: a rebuild emits nothing new; a third alum is a new event.
    out2 = rel.build(db)
    db.commit()
    assert out2["lab_alumni_cluster"] == 1 and len(_signals(db, "lab_alumni_cluster")) == 1
    assert len(_signals(db, "joined_young_org")) == 2
    _young_org_with(db, {"cy": 9})
    rel.build(db)
    db.commit()
    assert len(_signals(db, "lab_alumni_cluster")) == 2


def test_lab_alumni_cluster_on_a_young_personal_repo_counts_the_owner(db):
    ingest_batch(db, _pool_batch())
    ingest_batch(db, ProviderBatch(
        provider="github",
        repositories=[RepoRecord(ref=repo_ref("Ada/tactile-policy"), full_name="Ada/tactile-policy", owner="Ada",
                                 owner_type="User", stars=4, gh_created_at=datetime(2026, 9, 1, tzinfo=UTC))],
        relations=[Relation(kind="contributor", src=gh_user_ref("Ada"), dst=repo_ref("Ada/tactile-policy"),
                            attrs={"owner": True}),
                   Relation(kind="contributor", src=gh_user_ref("cy"), dst=repo_ref("Ada/tactile-policy"),
                            attrs={"contributions": 6})]))
    db.commit()
    rel.build(db)
    db.commit()
    [cl] = _signals(db, "lab_alumni_cluster")
    assert cl.entity_type == "repository" and {m["login"] for m in cl.meta["members"]} == {"Ada", "cy"}
    assert next(m for m in cl.meta["members"] if m["login"] == "Ada")["owner"] is True


def test_lab_repos_and_old_orgs_never_form_clusters(db):
    ingest_batch(db, _pool_batch())
    db.commit()
    _young_org_with(db, {"Ada": 12, "ben": 5}, login="acme-lab", created="2026-09-01")  # a lab org itself
    _young_org_with(db, {"Ada": 12, "ben": 5}, login="oldorg", created="2019-01-01")
    rel.build(db)
    db.commit()
    assert _signals(db, "lab_alumni_cluster") == []


# ── ops: rotation, activity targets, sweep step, CLI ─────────────────────────


class _FakeClient:
    stats = HttpStats()


class _FakeGH:
    pool_calls: list = []
    activity_calls: list = []

    def __init__(self, *a, **kw) -> None:
        self.client = _FakeClient()

    def talent_pool(self, sources, **kw):
        _FakeGH.pool_calls.append(([s.org for s in sources], kw))
        return ProviderBatch(provider="github", people=[PersonRecord(
            ref=gh_user_ref(f"{s.org}-dev"), name=f"{s.org}-dev", github_handle=f"{s.org}-dev",
            attrs={"lab_contributions": [{"org": s.org, "label": s.label, "tier": s.tier, "repo": f"{s.org}/x",
                                          "commits": 20}]}) for s in sources])

    def recent_activity(self, logins, **kw):
        _FakeGH.activity_calls.append((list(logins), kw))
        return ProviderBatch(provider="github", people=[PersonRecord(
            ref=gh_user_ref(lg), name=lg, github_handle=lg, attrs={"activity_checked_at": clock.now().isoformat()})
            for lg in logins])


@pytest.fixture()
def fake_gh(monkeypatch):
    import radar.providers.github as gh

    _FakeGH.pool_calls, _FakeGH.activity_calls = [], []
    monkeypatch.setattr(gh, "GitHubProvider", _FakeGH)
    return _FakeGH


def test_talent_step_rotates_orgs_and_completes_a_cycle(db, fake_gh):
    cfg = ops.talent_config()
    n, per = len(cfg.sources), cfg.limits.orgs_per_run
    seen: list[str] = []
    for _ in range(-(-n // per)):
        res = ops.ingest_talent(activity=False)
        assert len(res["pool_orgs"]) <= per
        seen += res["pool_orgs"]
    assert sorted(seen) == sorted(s.org for s in cfg.sources)  # one full cycle, no repeats
    assert ops.ingest_talent(activity=False)["pool_orgs"] == []  # all refreshed within refresh_days
    assert fake_gh.pool_calls[0][1]["min_commits"] == cfg.limits.min_commits
    # Runs older than refresh_days no longer count.
    db.expire_all()
    for r in db.scalars(select(IngestionRun).where(IngestionRun.provider == "github")):
        r.started_at = clock.wall_now() - timedelta(days=cfg.limits.refresh_days + 1)
    db.commit()
    assert ops.talent_due_orgs(cfg) == [s.org for s in cfg.sources][:per]


def _talent_person(db, login: str, checked: datetime | None = None, **kw) -> None:
    attrs = {"lab_contributions": [{"repo": "openai/codex", "commits": 30}]}
    if checked is not None:
        attrs["activity_checked_at"] = checked.isoformat()
    db.add(Person(id=uuid.uuid4(), slug=login.lower(), name=login, github_handle=login, prior_affiliations=[],
                  domains=[], attrs=attrs, **kw))


def test_activity_targets_oldest_checked_first_capped_and_skips_recent(db):
    now = clock.now()
    _talent_person(db, "never")
    _talent_person(db, "old", now - timedelta(days=5))
    _talent_person(db, "older", now - timedelta(days=9))
    _talent_person(db, "fresh", now - timedelta(hours=2))
    _talent_person(db, "gone", dismissed=True)
    _talent_person(db, "copilot-swe-agent")
    db.add(Person(id=uuid.uuid4(), slug="nolab", name="nolab", github_handle="nolab", prior_affiliations=[],
                  domains=[], attrs={}))
    db.commit()
    assert ops.talent_activity_targets(10) == ["never", "older", "old"]
    assert ops.talent_activity_targets(2) == ["never", "older"]
    assert ops.talent_activity_targets(10, recheck_hours=0) == ["never", "older", "old", "fresh"]


def test_talent_sweep_step_records_runs_and_marks_people_checked(db, fake_gh):
    _talent_person(db, "never")
    db.commit()
    out = ops.sweep(["talent"])
    assert out["talent"]["status"] == "ok" and out["talent"]["result"]["activity_people"] >= 1
    assert out["talent"]["result"]["pool_orgs"]
    runs = {(r.params or {}).get("talent") for r in db.scalars(select(IngestionRun))}
    assert {"pool", "activity"} <= runs
    acts = fake_gh.activity_calls[0]
    assert "never" in acts[0] and "openai" in acts[1]["skip_orgs"] and acts[1]["window_days"] == 120
    db.expire_all()
    assert _person(db, "never").attrs["activity_checked_at"]


def test_cli_ingest_talent(db, fake_gh):
    from radar.cli import app

    r = CliRunner().invoke(app, ["ingest", "talent", "--org", "openai", "--no-activity"])
    assert r.exit_code == 0, r.output
    assert fake_gh.pool_calls == [(["openai"], fake_gh.pool_calls[0][1])] and not fake_gh.activity_calls
    with session_scope() as s:
        assert s.scalars(select(Person).where(Person.github_handle == "openai-dev")).one().attrs["lab_contributions"]
