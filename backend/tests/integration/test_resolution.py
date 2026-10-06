"""Entity resolution: deterministic merges, conflicts, fuzzy candidates, person link rule."""

from __future__ import annotations

from sqlalchemy import func, select

from radar.models import Company, FieldProvenance, MatchCandidate, Person
from radar.pipeline.ingest import ingest_batch
from radar.pipeline.resolve import Resolver, normalize_company_name
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    PaperRecord,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    arxiv_author_ref,
    domain_ref,
    gh_org_ref,
    gh_user_ref,
    paper_ref,
    repo_ref,
)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_normalize_company_name_strips_only_legal_suffixes():
    assert normalize_company_name("Acme Robotics, Inc.") == "acme robotics"
    assert normalize_company_name("Foo Labs LLC") == "foo labs"
    assert normalize_company_name("Bar AI") == "bar ai"


def test_domain_match_merges(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("acme.com"), name="Acme", website="https://acme.com")]))
    stats = ingest_batch(db, ProviderBatch(provider="hackernews", companies=[
        CompanyRecord(ref=domain_ref("ACME.com"), name="Acme Robotics Inc", description="Robot hands")]))
    assert _count(db, Company) == 1
    assert stats.entities_created == 0
    c = db.scalars(select(Company)).one()
    assert c.domain == "acme.com"
    assert c.description == "Robot hands"
    assert c.name == "Acme"  # name from the first real record is kept


def test_github_org_blog_domain_links_to_existing_company(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("acme.com"), name="Acme", website="https://acme.com")]))
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("AcmeHQ"), name="Acme HQ", github_org="AcmeHQ",
                      website="https://www.acme.com/blog", source_url="https://github.com/AcmeHQ")]))
    assert _count(db, Company) == 1
    c = db.scalars(select(Company)).one()
    assert c.github_org == "AcmeHQ"
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.field == "resolution_link")).one()
    assert prov.value["matched_via"] == "domain:acme.com"
    assert prov.value["ref"] == "github_org:acmehq"
    assert _count(db, MatchCandidate) == 0


def test_conflicting_keys_create_candidate_not_merge(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("acme.com"), name="Acme Corp", website="https://acme.com")]))
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("zebra-dyn"), name="Zebra Dynamics", github_org="zebra-dyn")]))
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("zebra-dyn"), name="Zebra Dynamics", github_org="zebra-dyn",
                      website="https://acme.com")]))
    assert _count(db, Company) == 2
    mc = db.scalars(select(MatchCandidate)).one()
    assert mc.method == "conflicting_keys" and mc.status == "pending"
    zebra = db.scalars(select(Company).where(Company.github_org == "zebra-dyn")).one()
    assert zebra.domain is None  # the domain stays with the company that owns it


def test_fuzzy_similar_names_create_candidate_only(db):
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("acmerobotics.com"), name="Acme Robotics", website="https://acmerobotics.com")]))
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("acme-robotic"), name="Acme Robotic Inc", github_org="acme-robotic")]))
    assert _count(db, Company) == 2
    mc = db.scalars(select(MatchCandidate)).one()
    assert mc.method == "fuzzy_name" and mc.status == "pending" and mc.confidence >= 0.88
    slugs = sorted(db.scalars(select(Company.slug)))
    assert slugs == ["acme-robotic-inc", "acme-robotics"]


def test_dissimilar_names_no_candidate(db):
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("fusionworks"), name="Fusion Works"),
        CompanyRecord(ref=gh_org_ref("tactilelabs"), name="Tactile Labs"),
    ]))
    assert _count(db, MatchCandidate) == 0


def test_slug_collision_gets_numeric_suffix(db):
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("orbit-a"), name="Orbit"),
        CompanyRecord(ref=gh_org_ref("orbit-b"), name="Orbit"),
        CompanyRecord(ref=gh_org_ref("orbit-c"), name="Orbit"),
    ]))
    assert sorted(db.scalars(select(Company.slug))) == ["orbit", "orbit-2", "orbit-3"]


def test_name_key_matches_only_when_unique(db):
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("helio-a"), name="Helio"),
    ]))
    r = Resolver(db, provider="hackernews").resolve(EntityRef(type="company", key="name:Helio Inc"))
    assert r is not None and not r.created
    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("helio-b"), name="Helio"),
    ]))
    r2 = Resolver(db, provider="hackernews").resolve(EntityRef(type="company", key="name:Helio"))
    assert r2 is not None and r2.created  # ambiguous name never picks a row


# ── arXiv author <-> GitHub user ────────────────────────────────────────────


def _arxiv_batch(code_repo: str = "jdoe/tactile-net") -> ProviderBatch:
    pref = paper_ref("2610.01234v2")
    aref = arxiv_author_ref("Jane Doe")
    return ProviderBatch(
        provider="arxiv",
        papers=[PaperRecord(ref=pref, arxiv_id="2610.01234", title="TactileNet: dexterous touch",
                            authors=[{"name": "Jane Doe"}], code_urls=[f"https://github.com/{code_repo}"],
                            url="https://arxiv.org/abs/2610.01234")],
        people=[PersonRecord(ref=aref, name="Jane Doe", source_url="https://arxiv.org/abs/2610.01234")],
        repositories=[RepoRecord(ref=repo_ref(code_repo), full_name=code_repo, owner=code_repo.split("/")[0],
                                 attrs={"stub": True})],
        relations=[Relation(kind="author", src=aref, dst=pref, attrs={"position": 0}),
                   Relation(kind="code_for", src=pref, dst=repo_ref(code_repo))],
    )


def _github_batch(repo: str = "jdoe/tactile-net", name: str = "Jane Doe") -> ProviderBatch:
    uref = gh_user_ref("JDoe")
    return ProviderBatch(
        provider="github",
        repositories=[RepoRecord(ref=repo_ref(repo), full_name=repo, owner=repo.split("/")[0], stars=50)],
        people=[PersonRecord(ref=uref, name=name, github_handle="JDoe")],
        relations=[Relation(kind="contributor", src=uref, dst=repo_ref(repo), attrs={"contributions": 40})],
    )


def test_arxiv_author_and_github_user_merge_under_code_for_rule(db):
    ingest_batch(db, _arxiv_batch())
    ingest_batch(db, _github_batch())
    people = db.scalars(select(Person)).all()
    assert len(people) == 1
    p = people[0]
    assert p.github_handle == "JDoe" and p.arxiv_author_key == "jane doe"
    assert p.attrs["links"][0]["method"] == "name+code_for"
    assert p.attrs["links"][0]["repos"] == ["jdoe/tactile-net"]
    prov = db.scalars(select(FieldProvenance).where(FieldProvenance.field == "identity_link")).one()
    assert prov.epistemic == "INFERENCE"


def test_github_first_then_arxiv_author_also_links(db):
    ingest_batch(db, _github_batch())
    ingest_batch(db, _arxiv_batch())
    assert _count(db, Person) == 1


def test_same_name_without_code_for_link_is_not_merged(db):
    ingest_batch(db, _arxiv_batch())
    ingest_batch(db, _github_batch(repo="jdoe/dotfiles"))
    assert _count(db, Person) == 2


def test_code_for_link_requires_identical_full_name(db):
    ingest_batch(db, _arxiv_batch())
    ingest_batch(db, _github_batch(name="Jane Q. Doe"))
    assert _count(db, Person) == 2


def test_identical_names_sharing_a_repo_become_candidate(db):
    ingest_batch(db, ProviderBatch(
        provider="github",
        repositories=[RepoRecord(ref=repo_ref("lab/sim"), full_name="lab/sim", owner="lab")],
        people=[PersonRecord(ref=gh_user_ref("wz1"), name="Wei Zhang"),
                PersonRecord(ref=gh_user_ref("wz2"), name="Wei Zhang")],
        relations=[Relation(kind="contributor", src=gh_user_ref("wz1"), dst=repo_ref("lab/sim")),
                   Relation(kind="contributor", src=gh_user_ref("wz2"), dst=repo_ref("lab/sim"))],
    ))
    assert _count(db, Person) == 2
    mc = db.scalars(select(MatchCandidate)).one()
    assert mc.entity_type == "person" and mc.method == "same_name+repo" and 0.6 <= mc.confidence <= 0.8


# ── arXiv author identity: a name is not an identity ───────────────────────


def _paper(aid: str, authors: list[dict], day: int, code: list[str] | None = None) -> PaperRecord:
    from datetime import UTC, datetime

    return PaperRecord(ref=paper_ref(aid), arxiv_id=aid, title=f"Paper {aid}", authors=authors,
                       published_at=datetime(2026, 10, day, 12, tzinfo=UTC), url=f"https://arxiv.org/abs/{aid}",
                       code_urls=[f"https://github.com/{c}" for c in code or []])


def _enriched(*papers: PaperRecord) -> ProviderBatch:
    from datetime import UTC, datetime

    from radar.providers.arxiv import enrich

    return enrich(ProviderBatch(provider="arxiv", papers=list(papers)), window_days=21,
                  now=datetime(2026, 10, 6, 18, tzinfo=UTC))


def _chen(db) -> list[Person]:
    return list(db.scalars(select(Person).where(Person.arxiv_author_key == "chen chen").order_by(Person.created_at)))


def _sig(db, signal_type: str):
    from radar.models import Signal

    return list(db.scalars(select(Signal).where(Signal.signal_type == signal_type)))


def test_homonym_arxiv_authors_across_runs_are_not_merged(db):
    ingest_batch(db, _enriched(_paper("2610.04956", [{"name": "Chen Chen", "affiliation": "Tsinghua University"},
                                                     {"name": "Li Wei"}], 4)))
    ingest_batch(db, _enriched(_paper("2610.06695", [{"name": "Chen Chen", "affiliation": "University of Sydney"},
                                                     {"name": "Sarah Jones"}], 5)))
    people = _chen(db)
    assert len(people) == 2  # same arxiv_author_key allowed, separate rows
    assert [p.current_company for p in people] == ["Tsinghua University", "University of Sydney"]
    mc = db.scalars(select(MatchCandidate)).one()
    assert mc.method == "same_name" and mc.status == "pending" and mc.confidence < 0.5
    assert {mc.a_id, mc.b_id} == {p.id for p in people}
    assert _sig(db, "affiliation_changed") == []  # no invented career move


def test_homonyms_in_one_batch_get_no_recurrence_signal(db):
    ingest_batch(db, _enriched(
        _paper("2610.04956", [{"name": "Chen Chen", "affiliation": "Tsinghua University"}, {"name": "Li Wei"}], 4),
        _paper("2610.06695", [{"name": "Chen Chen", "affiliation": "University of Sydney"},
                              {"name": "Sarah Jones"}], 5),
    ))
    people = _chen(db)
    assert len(people) == 2
    # each person keeps the affiliation printed on their own paper
    assert sorted(p.current_company for p in people) == ["Tsinghua University", "University of Sydney"]
    assert _sig(db, "author_recurrence") == []


def test_reingesting_the_same_paper_reuses_the_author(db):
    b = _paper("2610.04956", [{"name": "Chen Chen"}, {"name": "Li Wei"}], 4)
    ingest_batch(db, _enriched(b))
    ingest_batch(db, _enriched(b))
    assert len(_chen(db)) == 1
    assert _count(db, MatchCandidate) == 0


def test_shared_coauthor_corroborates_and_recurrence_is_inference(db):
    ingest_batch(db, _enriched(
        _paper("2610.04956", [{"name": "Chen Chen"}, {"name": "Li Wei"}], 4),
        _paper("2610.06695", [{"name": "Chen Chen"}, {"name": "Li Wei"}, {"name": "Sarah Jones"}], 5),
    ))
    people = _chen(db)
    assert len(people) == 1
    assert people[0].attrs["links"][0]["method"] == "shared_coauthor"
    rec = [s for s in _sig(db, "author_recurrence") if s.entity_id == people[0].id]
    assert len(rec) == 1 and rec[0].title == "Chen Chen: 2 papers in 1 day"
    assert rec[0].epistemic == "INFERENCE"
    assert rec[0].meta["identity"] == "name_match" and "matched by name" in rec[0].meta["caveat"]
    assert "matched by name" in rec[0].description


def test_affiliation_change_only_claimed_for_corroborated_identity(db):
    ingest_batch(db, _enriched(_paper("2610.04956", [{"name": "Chen Chen", "affiliation": "Tsinghua University"},
                                                     {"name": "Li Wei"}], 4)))
    # same coauthor -> same person; new affiliation is a corroborated observation
    ingest_batch(db, _enriched(_paper("2610.06695", [{"name": "Chen Chen", "affiliation": "University of Sydney"},
                                                     {"name": "Li Wei"}], 5)))
    assert len(_chen(db)) == 1
    sig = _sig(db, "affiliation_changed")
    assert len(sig) == 1 and sig[0].meta == {"from": "Tsinghua University", "to": "University of Sydney"}


def test_same_affiliation_or_shared_code_corroborates(db):
    ingest_batch(db, _enriched(_paper("2610.00001", [{"name": "Chen Chen", "affiliation": "MIT"}], 2,
                                      code=["lab/sim"])))
    ingest_batch(db, _enriched(_paper("2610.00002", [{"name": "Chen Chen", "affiliation": "mit"}], 3)))
    ingest_batch(db, _enriched(_paper("2610.00003", [{"name": "Chen Chen"}], 4, code=["lab/sim"])))
    assert len(_chen(db)) == 1
    assert _count(db, MatchCandidate) == 0


def test_second_github_org_sharing_a_blog_domain_is_not_merged(db):
    from datetime import date

    ingest_batch(db, ProviderBatch(provider="github", companies=[
        CompanyRecord(ref=gh_org_ref("acme"), name="Acme", github_org="acme", website="https://acme.ai",
                      description="Robot arms", founded_date=date(2018, 3, 1))]))
    for _ in range(2):  # stable across runs
        ingest_batch(db, ProviderBatch(provider="github", companies=[
            CompanyRecord(ref=gh_org_ref("acme-labs"), name="Acme Labs", github_org="acme-labs",
                          website="https://acme.ai", description="Research sandbox",
                          founded_date=date(2026, 8, 1))]))
    rows = {c.github_org: c for c in db.scalars(select(Company))}
    assert set(rows) == {"acme", "acme-labs"}
    parent = rows["acme"]
    assert parent.description == "Robot arms" and parent.founded_date == date(2018, 3, 1)
    assert parent.domain == "acme.ai" and rows["acme-labs"].domain is None
    mc = db.scalars(select(MatchCandidate)).one()
    assert mc.method == "shared_domain" and mc.status == "pending"
    assert {mc.a_id, mc.b_id} == {parent.id, rows["acme-labs"].id}
