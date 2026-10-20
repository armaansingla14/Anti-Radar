"""Ingestion: idempotency, snapshots, change detection, relations, runner, stage."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from radar import clock
from radar.db import session_scope
from radar.models import (
    Company,
    CompanyPerson,
    FieldProvenance,
    IngestionRun,
    Job,
    MetricSnapshot,
    Person,
    RepoContributor,
    Repository,
    Signal,
    SignalEntity,
    Source,
    WebPage,
)
from radar.pipeline.ingest import formation_phrases_in, ingest_batch
from radar.pipeline.runner import run_provider
from radar.pipeline.stage import apply_stage_estimates, estimate_stage
from radar.providers.base import (
    CompanyRecord,
    JobRecord,
    MetricPoint,
    NormalizedSignal,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    domain_ref,
    gh_org_ref,
    gh_user_ref,
    repo_ref,
)
from radar.providers.http import HttpStats

EVENT = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)


def _count(db, model, *where) -> int:
    q = select(func.count()).select_from(model)
    for w in where:
        q = q.where(w)
    return db.scalar(q)


def _signals(db, signal_type: str) -> list[Signal]:
    return list(db.scalars(select(Signal).where(Signal.signal_type == signal_type)))


def _github_batch(stars: int = 10, org_created: str = "2025-06-01") -> ProviderBatch:
    org, repo, user = gh_org_ref("tactica"), repo_ref("tactica/skin"), gh_user_ref("ada")
    return ProviderBatch(
        provider="github",
        companies=[CompanyRecord(ref=org, name="Tactica", github_org="tactica", website="https://tactica.dev",
                                 description="Tactile skins for robot hands", attrs={"created_at": org_created})],
        repositories=[RepoRecord(ref=repo, full_name="tactica/skin", owner="tactica", owner_type="Organization",
                                 description="Tactile sensing skin", topics=["tactile", "robotics"], stars=stars,
                                 owner_company=org)],
        people=[PersonRecord(ref=user, name="Ada Lovelace", github_handle="ada", bio="Robotics engineer",
                             current_company="@tactica")],
        relations=[Relation(kind="contributor", src=user, dst=repo, attrs={"contributions": 120})],
        metrics=[MetricPoint(entity=repo, metric="stars", day=date(2026, 10, 6), value=stars)],
        signals=[NormalizedSignal(entity=repo, signal_type="repo_created", source_type="github",
                                  source_url="https://github.com/tactica/skin", title="New repo tactica/skin",
                                  event_at=EVENT, related=[user])],
        sources=[{"url": "https://github.com/tactica/skin", "source_type": "github", "title": "tactica/skin",
                  "content_hash": "abc"}],
    )


def test_idempotent_reingest_updates_counters_without_duplicates(db):
    s1 = ingest_batch(db, _github_batch(stars=10))
    assert s1.entities_created == 3 and s1.signals_created == 1 and s1.rejected == 0, s1.errors
    s2 = ingest_batch(db, _github_batch(stars=15))
    assert s2.entities_created == 0 and s2.signals_created == 0 and s2.signals_duplicate == 1
    assert s2.entities_updated == 1  # only the repo changed
    assert _count(db, Company) == 1 and _count(db, Person) == 1 and _count(db, Repository) == 1
    assert _count(db, Signal) == 1 and _count(db, Source) == 1 and _count(db, RepoContributor) == 1
    repo = db.scalars(select(Repository)).one()
    assert repo.stars == 15
    assert db.scalar(select(MetricSnapshot.value)) == 15
    company = db.scalars(select(Company)).one()
    assert repo.company_id == company.id and company.domain == "tactica.dev"
    sig = db.scalars(select(Signal)).one()
    assert sig.source_id is not None
    links = {(se.entity_type, se.relation) for se in db.scalars(select(SignalEntity))}
    assert links == {("person", "related"), ("company", "repo_owner")}
    prov_fields = set(db.scalars(select(FieldProvenance.field).where(FieldProvenance.entity_id == company.id)))
    assert {"name", "description", "website", "domain", "github_org"} <= prov_fields


def test_young_org_contributor_becomes_member_but_old_org_does_not(db):
    ingest_batch(db, _github_batch(org_created="2025-06-01"))
    cp = db.scalars(select(CompanyPerson)).one()
    assert cp.role == "contributor" and cp.confidence == pytest.approx(0.7) and not cp.is_founder

    ingest_batch(db, ProviderBatch(
        provider="github",
        companies=[CompanyRecord(ref=gh_org_ref("bigco"), name="BigCo", github_org="bigco",
                                 attrs={"created_at": "2012-01-01T00:00:00Z"})],
        repositories=[RepoRecord(ref=repo_ref("bigco/lib"), full_name="bigco/lib", owner="bigco",
                                 owner_company=gh_org_ref("bigco"))],
        relations=[Relation(kind="contributor", src=gh_user_ref("ada"), dst=repo_ref("bigco/lib"))],
    ))
    assert _count(db, CompanyPerson) == 1


def test_founder_relation_is_not_downgraded_by_contributor(db):
    ingest_batch(db, ProviderBatch(provider="website", relations=[
        Relation(kind="founder", src=gh_user_ref("ada"), dst=gh_org_ref("tactica"), attrs={"role": "CEO"})]))
    ingest_batch(db, _github_batch())
    cp = db.scalars(select(CompanyPerson)).one()
    assert cp.is_founder and cp.role == "CEO" and cp.confidence == 1.0


def test_metric_observed_overrides_backfill_but_not_vice_versa(db):
    ref = repo_ref("tactica/skin")
    d = date(2026, 10, 1)

    def put(value, source):
        return ingest_batch(db, ProviderBatch(provider="github", metrics=[
            MetricPoint(entity=ref, metric="stars", day=d, value=value, source=source)]))

    put(5, "backfill")
    put(7, "observed")
    put(9, "backfill")
    assert db.execute(select(MetricSnapshot.value, MetricSnapshot.source)).one() == (7, "observed")
    put(8, "observed")
    assert db.scalar(select(MetricSnapshot.value)) == 8
    ingest_batch(db, ProviderBatch(provider="demo", metrics=[
        MetricPoint(entity=ref, metric="stars", day=d, value=999, source="observed")]), is_demo=True)
    value, is_demo = db.execute(select(MetricSnapshot.value, MetricSnapshot.is_demo)).one()
    assert value == 8 and not is_demo  # demo never overwrites real observations


def _careers(chash: str) -> ProviderBatch:
    return ProviderBatch(provider="website", sources=[{
        "url": "https://tactica.dev/careers", "source_type": "website", "title": "Careers",
        "content_hash": chash, "page_kind": "careers", "text": "Join us"}])


def test_website_change_detection_emits_once(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("tactica.dev"), name="Tactica", website="https://tactica.dev")]))
    assert ingest_batch(db, _careers("h1")).pages_changed == 0
    assert ingest_batch(db, _careers("h1")).pages_changed == 0
    assert _signals(db, "website_changed") == []
    st = ingest_batch(db, _careers("h2"))
    assert st.pages_changed == 1 and st.signals_created == 1
    ingest_batch(db, _careers("h2"))
    sigs = _signals(db, "website_changed")
    assert len(sigs) == 1
    assert sigs[0].meta["careers_page"] is True and sigs[0].meta["old_hash"] == "h1"
    page = db.scalars(select(WebPage)).one()
    assert page.content_hash == "h2" and page.last_changed_at is not None and page.company_id is not None
    assert _count(db, Source) == 2


def _jobs(*ids: str) -> ProviderBatch:
    ref = domain_ref("tactica.dev")
    titles = {"1": "Senior Robotics Engineer", "2": "Firmware Engineer", "3": "Account Executive"}
    return ProviderBatch(provider="jobs", jobs=[
        JobRecord(company=ref, external_id=i, ats="ashby", title=titles[i], url=f"https://jobs.ashbyhq.com/t/{i}")
        for i in ids])


def test_job_posted_only_after_first_crawl_and_jobs_close(db):
    s1 = ingest_batch(db, _jobs("1"))
    assert s1.jobs_new == 1 and _signals(db, "job_posted") == []
    s2 = ingest_batch(db, _jobs("1", "2"))
    assert s2.jobs_new == 1
    sigs = _signals(db, "job_posted")
    assert len(sigs) == 1 and sigs[0].meta["external_id"] == "2"
    assert sigs[0].meta["role_class"] == "embedded_firmware"
    ingest_batch(db, _jobs("1", "2"))
    assert len(_signals(db, "job_posted")) == 1
    ingest_batch(db, _jobs("2"))
    job1 = db.scalars(select(Job).where(Job.external_id == "1")).one()
    assert job1.closed_at is not None
    ingest_batch(db, _jobs("1", "2"))
    db.refresh(job1)
    assert job1.closed_at is None  # reappeared -> reopened, not a new posting
    assert len(_signals(db, "job_posted")) == 1


def _person(company: str | None, bio: str | None) -> ProviderBatch:
    return ProviderBatch(provider="github", people=[PersonRecord(
        ref=gh_user_ref("ada"), name="Ada Lovelace", github_handle="ada", current_company=company, bio=bio)])


def test_affiliation_change_appends_prior_and_emits_signal(db):
    ingest_batch(db, _person("@Google DeepMind", "Research scientist working on robot learning."))
    ingest_batch(db, _person("Google DeepMind", "Research scientist working on robot learning."))
    assert _signals(db, "affiliation_changed") == []  # '@' / case is not a change
    ingest_batch(db, _person(None, None))  # missing data never clears fields
    p = db.scalars(select(Person)).one()
    assert p.current_company == "Google DeepMind" and p.bio
    ingest_batch(db, _person("Tactica", "Co-founder @ Tactica. Building something new in tactile sensing."))
    db.refresh(p)
    assert p.current_company == "Tactica" and p.prior_affiliations == ["Google DeepMind"]
    sig = _signals(db, "affiliation_changed")[0]
    assert sig.meta == {"from": "Google DeepMind", "to": "Tactica"} and sig.epistemic == "FACT"
    assert len(_signals(db, "bio_changed")) == 1
    form = _signals(db, "formation_language")
    assert len(form) == 1 and set(form[0].meta["phrases"]) == {"co-founder", "building something new"}
    ingest_batch(db, _person("Tactica", "Co-founder @ Tactica. Building something new in tactile sensing."))
    assert _count(db, Signal) == 3


def test_formation_phrases_are_word_bounded(thesis):
    assert formation_phrases_in("We’re hiring a Founding Engineer", thesis) == ["we're hiring", "founding engineer"]
    assert formation_phrases_in("stealthy ninja", thesis) == []


def test_demo_ingest_flags_rows_and_allows_first_seen_override(db):
    batch = ProviderBatch(provider="demo", companies=[CompanyRecord(
        ref=domain_ref("example-demo.test"), name="Demo Co",
        attrs={"first_seen_at": "2026-09-01T00:00:00Z", "demo_label": "fictional"})],
        signals=[NormalizedSignal(entity=domain_ref("example-demo.test"), signal_type="launch", source_type="demo",
                                  title="Launch", event_at=EVENT)])
    ingest_batch(db, batch, is_demo=True)
    c = db.scalars(select(Company)).one()
    assert c.is_demo and c.demo_label == "fictional"
    assert c.first_seen_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert "first_seen_at" not in c.attrs
    assert db.scalars(select(Signal)).one().is_demo


def test_bad_record_is_rejected_without_sinking_batch(db):
    batch = _github_batch()
    batch.signals.append(NormalizedSignal(entity=repo_ref("x/y").model_copy(update={"type": "paper", "key": "arxiv:9999.0"}),
                                          signal_type="paper_published", source_type="arxiv", title="?",
                                          event_at=EVENT))
    st = ingest_batch(db, batch)
    assert st.rejected == 1 and st.signals_created == 1 and "arxiv:9999.0" in st.errors[0]


# ── runner ──────────────────────────────────────────────────────────────────


def test_runner_records_error_on_exception(db):
    def boom() -> ProviderBatch:
        raise RuntimeError("API exploded")

    run, stats = run_provider(session_scope, "github", boom, params={"q": "x"})
    assert stats is None and run.status == "error"
    assert any("API exploded" in e for e in run.errors)
    with session_scope() as s:
        stored = s.get(IngestionRun, run.id)
        assert stored.status == "error" and stored.finished_at is not None


def test_runner_partial_and_ok(db):
    def partial() -> ProviderBatch:
        b = _github_batch()
        b.errors.append("rate limited on page 3")
        b.rejected = 2
        return b

    hs = HttpStats(requests=4, latencies_ms=[10, 20, 30], rate_limit_remaining=4990)
    run, stats = run_provider(session_scope, "github", partial, params={}, http_stats=hs)
    assert run.status == "partial" and stats is not None
    assert run.records_fetched == 6 + 2 and run.records_rejected == 2 and run.records_accepted == 6
    assert run.signals_created == 1 and run.request_count == 4
    assert run.latency_ms_p50 == 20 and run.rate_limit_remaining == 4990

    run2, _ = run_provider(session_scope, "github", _github_batch, params={})
    assert run2.status == "ok" and run2.errors == []


def test_runner_errors_without_data_is_error(db):
    run, _ = run_provider(session_scope, "x", lambda: ProviderBatch(provider="x", errors=["401 unauthorized"]),
                          params={})
    assert run.status == "error"


# ── stage ───────────────────────────────────────────────────────────────────


def test_stage_estimates_are_conservative(db):
    today = clock.today()
    young = Company(slug="y", name="Young", attrs={"created_at": (today - timedelta(days=60)).isoformat()})
    mid = Company(slug="m", name="Mid", attrs={"created_at": (today - timedelta(days=300)).isoformat()})
    old = Company(slug="o", name="Old", founded_date=today - timedelta(days=2000), attrs={})
    unknown = Company(slug="u", name="Unknown", attrs={})
    founder = CompanyPerson(is_founder=True)
    member = CompanyPerson(is_founder=False)
    assert estimate_stage(young, [], [], [founder, member])[0] == "formation"
    assert estimate_stage(mid, [], [], [founder] + [member] * 5)[0] == "pre_seed"
    assert estimate_stage(mid, [], [], [founder] * 30)[0] == "unknown"
    assert estimate_stage(old, [], [], [])[0] == "unknown"
    stage, basis = estimate_stage(unknown, [], [], [])
    assert stage == "unknown" and "Insufficient" in basis
    old_repo = Repository(full_name="y/r", owner="y", gh_created_at=datetime(2019, 1, 1, tzinfo=UTC))
    assert estimate_stage(young, [], [old_repo], [founder])[0] == "unknown"
    seed = Signal(signal_type="funding_round", source_type="hackernews", title="Young raises $3M seed",
                  event_at=EVENT, meta={})
    assert estimate_stage(young, [seed], [], [])[0] == "seed"


def test_young_org_alone_is_not_an_early_stage_company(db):
    today = clock.today()
    gh = {"founded_date_basis": "github_org_created_at"}
    # Corporate team org: young, small, no funding — but nothing says it is a startup.
    xiaomi = Company(slug="x", name="Xiaomi Robotics", kind="company", founded_date=today - timedelta(days=240),
                     attrs=gh)
    stage, basis = estimate_stage(xiaomi, [], [], [CompanyPerson(is_founder=True)])
    assert stage == "unknown" and "Xiaomi" in basis
    team = Company(slug="t", name="Plain Org", kind="company", founded_date=today - timedelta(days=240), attrs=gh)
    stage, basis = estimate_stage(team, [], [], [CompanyPerson()] * 3)
    assert stage == "unknown" and "GitHub org created" in basis and "founded date" not in basis
    for kind in ("project", "lab"):
        c = Company(slug=kind, name="Shanghai Innovation Institute", kind=kind,
                    founded_date=today - timedelta(days=60), attrs=gh)
        assert estimate_stage(c, [], [], [CompanyPerson(is_founder=True)])[0] == "unknown"
    formation = Signal(signal_type="formation_language", source_type="github", title="x", event_at=EVENT, meta={})
    stage, basis = estimate_stage(team, [formation], [], [CompanyPerson()] * 3)
    assert stage == "pre_seed" and "formation signal" in basis


def test_apply_stage_estimates_writes_inference_provenance(db):
    batch = _github_batch(org_created=(clock.today() - timedelta(days=90)).isoformat())
    batch.relations.append(Relation(kind="founder", src=gh_user_ref("ada"), dst=gh_org_ref("tactica")))
    ingest_batch(db, batch)
    assert apply_stage_estimates(db) == 1
    c = db.scalars(select(Company)).one()
    assert c.stage == "formation" and "GitHub org created" in c.stage_basis
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.field == "stage")).one()
    assert prov.epistemic == "INFERENCE"
    assert apply_stage_estimates(db) == 0


# ── classification ──────────────────────────────────────────────────────────


def test_classify_entities_replaces_keyword_rows_and_keeps_llm_rows(db, thesis):
    from radar.models import EntityTopic, Topic
    from radar.thesis.classifier import classify_entities, ensure_topics

    ingest_batch(db, _github_batch())
    company = db.scalars(select(Company)).one()
    topic_ids = ensure_topics(db, thesis)
    db.add(EntityTopic(entity_type="company", entity_id=company.id, topic_id=topic_ids["defense.counter_uas"],
                       score=0.5, method="llm", evidence=["model"]))
    db.flush()
    counts = classify_entities(db, thesis)
    assert counts["company"] == 1 and counts["repository"] == 1 and counts["person"] == 1
    rows = {(db.get(Topic, et.topic_id).key, et.method) for et in
            db.scalars(select(EntityTopic).where(EntityTopic.entity_id == company.id))}
    assert ("robotics.tactile", "keyword") in rows and ("defense.counter_uas", "llm") in rows
    db.refresh(company)
    assert company.thesis_tags[0] == "robotics" and "defense" in company.thesis_tags
    before = _count(db, EntityTopic)
    classify_entities(db, thesis)
    assert _count(db, EntityTopic) == before  # idempotent


def test_bulk_metric_backfill_counts_rows(db):
    ref = repo_ref("tactica/skin")
    start = date(2026, 7, 1)
    pts = [MetricPoint(entity=ref, metric="stars", day=start + timedelta(days=i), value=i, source="backfill")
           for i in range(90)]
    assert ingest_batch(db, ProviderBatch(provider="github", metrics=pts)).metrics_upserted == 90
    assert _count(db, MetricSnapshot) == 90


# ── snapshot clock pin ───────────────────────────────────────────────────────


@pytest.fixture()
def pinned_clock(monkeypatch):
    """No TODAY_OVERRIDE; restore the pin afterwards."""
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "today_override", None)
    yield
    clock.set_as_of(None)


def test_ingestion_runs_use_wall_clock_not_the_pinned_day(db, pinned_clock):
    clock.set_as_of(date(2026, 1, 15))  # a snapshot day that is not today
    assert clock.now().date() == date(2026, 1, 15)
    run, _ = run_provider(session_scope, "github", _github_batch, params={})
    real = datetime.now(UTC)
    assert abs((run.started_at - real).total_seconds()) < 120
    assert abs((run.finished_at - real).total_seconds()) < 120


def test_successful_live_run_clears_the_pin_but_demo_and_failures_do_not(db, pinned_clock):
    pin = date(2026, 1, 15)
    clock.set_as_of(pin)
    run_provider(session_scope, "demo", lambda: ProviderBatch(provider="demo"), params={}, is_demo=True)
    assert clock.as_of() == pin

    def boom() -> ProviderBatch:
        raise RuntimeError("down")

    run_provider(session_scope, "github", boom, params={})
    assert clock.as_of() == pin
    run_provider(session_scope, "github", _github_batch, params={})
    assert clock.as_of() is None and clock.today() == datetime.now(UTC).date()


def test_init_from_db_detects_live_runs_by_provider_not_timestamp(db, pinned_clock):
    pinned = datetime(2026, 1, 15, 18, tzinfo=UTC)  # legacy rows were stamped with the pinned time
    db.add(IngestionRun(provider="snapshot", status="ok", started_at=pinned, finished_at=pinned,
                        params={"as_of": "2026-01-15"}))
    db.add(IngestionRun(provider="github", status="ok", started_at=pinned, finished_at=pinned,
                        params={"snapshot": True, "file": "0003_github.json.gz"}))
    db.add(IngestionRun(provider="demo", status="ok", started_at=pinned, finished_at=pinned, params={}))
    db.add(IngestionRun(provider="arxiv", status="error", started_at=pinned, finished_at=pinned, params={}))
    db.commit()
    assert clock.init_from_db() == date(2026, 1, 15)  # only the snapshot replay, demo set and a failure
    db.add(IngestionRun(provider="github", status="ok", started_at=pinned, finished_at=pinned, params={}))
    db.commit()
    assert clock.init_from_db() is None  # same timestamp as the snapshot, still a live run


def test_fictional_dataset_never_points_at_real_hosts_or_claims_verified_facts(db):
    from urllib.parse import urlparse

    from radar.demo.fictional import build_fictional_batch
    batch, _ = build_fictional_batch()
    stats = ingest_batch(db, batch, is_demo=True)
    assert stats.errors == []
    prov = db.scalars(select(FieldProvenance)).all()
    assert prov
    hosts = {urlparse(p.source_url).hostname for p in prov if p.source_url}
    assert hosts and all(h.endswith(".example") for h in hosts), hosts
    assert {p.epistemic for p in prov} == {"INFERENCE"}  # fictional values are never VERIFIED
    memberships = [cp.source_url for cp in db.scalars(select(CompanyPerson)) if cp.source_url]
    assert all(urlparse(u).hostname.endswith(".example") for u in memberships)


def test_canonical_url_for_demo_rows_uses_example_hosts():
    from radar.pipeline.resolve import canonical_url

    assert canonical_url(Company(name="Halcyon", github_org="demo-halcyon", is_demo=True)) == \
        "https://github.example/demo-halcyon"
    assert canonical_url(Person(name="Mira", x_handle="mira", is_demo=True)) == "https://x.example/mira"
    assert canonical_url(Repository(full_name="a/b", is_demo=False)) == "https://github.com/a/b"


def test_hn_paper_link_never_overwrites_the_real_title(db):
    from radar.models import ResearchPaper
    from radar.providers.base import PaperRecord, paper_ref
    from radar.providers.hackernews import build_batch

    hit = {"objectID": "1", "title": "Show HN: something", "url": "https://arxiv.org/abs/2610.04956v2",
           "created_at_i": int(EVENT.timestamp()), "points": 50, "num_comments": 3, "author": "x"}
    # HN first: the new row is a flagged stub
    ingest_batch(db, build_batch([hit], now=EVENT))
    p = db.scalars(select(ResearchPaper)).one()
    assert p.attrs.get("stub") is True and p.url == "https://arxiv.org/abs/2610.04956"
    # arXiv arrives: real title, stub cleared
    ingest_batch(db, ProviderBatch(provider="arxiv", papers=[PaperRecord(
        ref=paper_ref("2610.04956"), arxiv_id="2610.04956", title="Real Title About SLOs",
        url="https://arxiv.org/abs/2610.04956", attrs={"author_count": 1})]))
    db.refresh(p)
    assert p.title == "Real Title About SLOs" and not p.attrs.get("stub")
    # HN again: the real title stays
    ingest_batch(db, build_batch([{**hit, "objectID": "2"}], now=EVENT))
    db.refresh(p)
    assert p.title == "Real Title About SLOs" and not p.attrs.get("stub")
    titles = {f.value for f in db.scalars(select(FieldProvenance).where(FieldProvenance.field == "title"))}
    assert titles == {"Real Title About SLOs"}


def test_truncated_board_does_not_close_unseen_jobs(db):
    ingest_batch(db, _jobs("1", "2", "3"))
    partial = _jobs("1")
    partial.jobs = [j.model_copy(update={"complete": False}) for j in partial.jobs]
    ingest_batch(db, partial)
    assert _count(db, Job, Job.closed_at.is_not(None)) == 0  # a lower bound is not a closure
    ingest_batch(db, _jobs("1"))  # complete crawl: now the others really are gone
    assert _count(db, Job, Job.closed_at.is_not(None)) == 2


# ── V2: self-reported provenance, README change, alias normalization, project kind ──


def test_github_profile_fields_are_self_reported_inference(db):
    ingest_batch(db, _github_batch())
    person = db.scalars(select(Person)).one()
    prov = {p.field: p for p in db.scalars(select(FieldProvenance).where(FieldProvenance.entity_id == person.id))}
    for fld in ("current_company", "bio"):
        assert prov[fld].epistemic == "INFERENCE", fld
        assert prov[fld].source_type == "github:self-reported" and prov[fld].confidence == pytest.approx(0.6)
    assert prov["github_handle"].epistemic == "FACT"  # the account itself is observed
    company = db.scalars(select(Company)).one()
    cprov = {p.field: p for p in db.scalars(select(FieldProvenance).where(FieldProvenance.entity_id == company.id))}
    assert cprov["description"].epistemic == "INFERENCE" and cprov["website"].epistemic == "INFERENCE"
    assert cprov["github_org"].epistemic == "FACT"


def test_self_reported_rule_only_applies_to_github_profiles(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("tactica.dev"), name="Tactica", website="https://tactica.dev",
                      description="Tactile skins")]))
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.field == "description")).one()
    assert prov.epistemic == "FACT" and prov.source_type == "website"


def _readme_batch(sha: str | None) -> ProviderBatch:
    return ProviderBatch(provider="github", repositories=[
        RepoRecord(ref=repo_ref("tactica/skin"), full_name="tactica/skin", owner="tactica", stars=5,
                   readme_excerpt=f"readme {sha}", attrs={"readme_sha": sha})])


def test_readme_sha_change_emits_readme_changed_once(db):
    ingest_batch(db, _readme_batch("aaa"))
    assert _signals(db, "readme_changed") == []  # first observation is not a change
    ingest_batch(db, _readme_batch("aaa"))
    assert _signals(db, "readme_changed") == []
    ingest_batch(db, _readme_batch("bbb"))
    ingest_batch(db, _readme_batch("bbb"))
    [sig] = _signals(db, "readme_changed")
    assert sig.epistemic == "FACT" and sig.meta["old_sha"] == "aaa" and sig.meta["new_sha"] == "bbb"
    assert db.scalars(select(Repository)).one().attrs["readme_sha"] == "bbb"
    ingest_batch(db, _readme_batch(None))  # README not fetched this time: not a change, sha kept
    assert len(_signals(db, "readme_changed")) == 1
    assert db.scalars(select(Repository)).one().attrs["readme_sha"] == "bbb"


def _person_batch(company: str) -> ProviderBatch:
    return ProviderBatch(provider="github", people=[
        PersonRecord(ref=gh_user_ref("ada"), name="Ada Lovelace", github_handle="ada", current_company=company)])


def test_alias_spellings_are_not_affiliation_changes_and_normalized_forms_are_stored(db):
    ingest_batch(db, _person_batch("@google-deepmind"))
    ingest_batch(db, _person_batch("DeepMind"))
    assert _signals(db, "affiliation_changed") == []
    p = db.scalars(select(Person)).one()
    assert p.attrs["affiliations_normalized"]["current_company"] == "Google DeepMind"
    ingest_batch(db, _person_batch("Tactica"))
    [sig] = _signals(db, "affiliation_changed")
    p = db.scalars(select(Person)).one()
    assert p.current_company == "Tactica" and p.prior_affiliations == ["DeepMind"]  # raw strings kept
    assert p.attrs["affiliations_normalized"] == {"current_company": "Tactica",
                                                  "prior_affiliations": ["Google DeepMind"]}


def test_young_org_without_domain_is_a_project_until_a_domain_appears(db):
    org = gh_org_ref("newlab")
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=org, name="newlab", github_org="newlab", attrs={"gh_org_created_at": "2026-08-01"})]))
    assert db.scalars(select(Company)).one().kind == "project"
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=org, name="newlab", github_org="newlab", website="https://newlab.ai",
                      alt_refs=[domain_ref("newlab.ai")])]))
    c = db.scalars(select(Company)).one()
    assert c.kind == "company" and c.domain == "newlab.ai"


def test_org_stub_from_repo_owner_is_a_project(db):
    ingest_batch(db, ProviderBatch(provider="github", repositories=[
        RepoRecord(ref=repo_ref("teamx/core"), full_name="teamx/core", owner="teamx", owner_type="Organization",
                   owner_company=gh_org_ref("teamx"))]))
    assert db.scalars(select(Company)).one().kind == "project"


def test_live_run_does_not_release_demo_snapshot_pin(db, monkeypatch):
    monkeypatch.setattr(clock, "snapshot_pinned", lambda: True)
    clock.set_as_of(date(2026, 10, 6))
    try:
        run, _ = run_provider(session_scope, "github", lambda: _github_batch(), params={})
        assert run.status == "ok" and clock.as_of() == date(2026, 10, 6)
        monkeypatch.setattr(clock, "snapshot_pinned", lambda: False)
        run_provider(session_scope, "github", lambda: _github_batch(), params={})
        assert clock.as_of() is None
    finally:
        clock.set_as_of(None)
