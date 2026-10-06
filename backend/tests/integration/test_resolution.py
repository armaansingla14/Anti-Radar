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
