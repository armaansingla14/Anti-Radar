"""relationships.build: deterministic edges from stored facts; idempotent; prunes stale rows."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select

from radar.models.core import (
    Company,
    CompanyPerson,
    PaperAuthor,
    Person,
    Relationship,
    RepoContributor,
    Repository,
    ResearchPaper,
)
from radar.pipeline import relationships as rel


def person(db, slug, *, company=None, prior=(), handle=None, demo=False) -> Person:
    p = Person(slug=slug, name=slug.title(), github_handle=handle or slug, current_company=company,
               prior_affiliations=list(prior), is_demo=demo, attrs={"self_reported": True})
    db.add(p)
    db.flush()
    return p


def paper(db, arxiv_id, when, authors, demo=False) -> ResearchPaper:
    pp = ResearchPaper(arxiv_id=arxiv_id, title=f"Paper {arxiv_id}", published_at=when,
                       url=f"https://arxiv.org/abs/{arxiv_id}", is_demo=demo)
    db.add(pp)
    db.flush()
    for i, a in enumerate(authors):
        db.add(PaperAuthor(paper_id=pp.id, person_id=a.id, position=i))
    db.flush()
    return pp


def repo(db, full, company=None) -> Repository:
    r = Repository(full_name=full, owner=full.split("/")[0], company_id=company.id if company else None)
    db.add(r)
    db.flush()
    return r


def contrib(db, r, p, n, first=None, last=None):
    db.add(RepoContributor(repository_id=r.id, person_id=p.id, contributions=n, first_commit_at=first,
                           last_commit_at=last))
    db.flush()


def rows(db, kind=None):
    q = select(Relationship)
    if kind:
        q = q.where(Relationship.kind == kind)
    return list(db.scalars(q))


def test_build_all_kinds(db):
    a = person(db, "ada", company="NVIDIA", prior=["Google DeepMind"])
    b = person(db, "ben", company="Acme Robotics", prior=["@nvidia"])
    c = person(db, "cy", prior=["Freelance"])
    d = person(db, "dee", prior=["freelance"])
    paper(db, "2501.00001", datetime(2025, 3, 1, tzinfo=UTC), [a, b, c])
    co = Company(slug="acme", name="Acme", github_org="acme", kind="project")
    db.add(co)
    db.flush()
    r = repo(db, "acme/core", co)
    contrib(db, r, a, 40, datetime(2026, 8, 2, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC))
    contrib(db, r, b, 12, datetime(2026, 8, 10, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))
    contrib(db, r, c, 2)  # below the co-contribution threshold
    db.add(CompanyPerson(company_id=co.id, person_id=a.id, role="contributor",
                         source_url="https://github.com/acme/core"))
    db.add(CompanyPerson(company_id=co.id, person_id=b.id, role="founder", is_founder=True,
                         source_url="https://github.com/ben"))  # profile-only source: self-reported
    db.flush()

    out = rel.build(db)
    assert out["AUTHORED"] == 3 and out["COAUTHORED_WITH"] == 3 and out["CONTRIBUTED_TO"] == 3
    assert out["CONTRIBUTED_WITH"] == 1 and out["MEMBER_OF"] == 1 and out["FOUNDED"] == 1

    for e in rows(db):
        if e.kind in rel.PERSON_PAIR_KINDS:
            assert str(e.src_id) < str(e.dst_id)
    [cw] = rows(db, "CONTRIBUTED_WITH")
    assert cw.first_seen_at == datetime(2026, 8, 10, tzinfo=UTC)  # overlap starts when the later one started
    assert cw.last_seen_at == datetime(2026, 9, 30, tzinfo=UTC) and cw.epistemic == "VERIFIED"
    coauth = rows(db, "COAUTHORED_WITH")
    assert all(x.first_seen_at == datetime(2025, 3, 1, tzinfo=UTC) and x.epistemic == "VERIFIED" for x in coauth)
    assert coauth[0].evidence[0]["source_url"].startswith("https://arxiv.org/abs/")

    [member] = rows(db, "MEMBER_OF")
    assert member.epistemic == "VERIFIED" and member.first_seen_at == datetime(2026, 8, 2, tzinfo=UTC)
    [founded] = rows(db, "FOUNDED")
    assert founded.epistemic == "INFERRED"  # only a GitHub profile backs it

    # WORKED_WITH: "NVIDIA" and "@nvidia" normalize together; "Freelance" is not an employer.
    [ww] = rows(db, "WORKED_WITH")
    assert {ww.src_id, ww.dst_id} == {a.id, b.id} and ww.epistemic == "INFERRED" and ww.first_seen_at is None
    raws = " ".join(e["detail"] for e in ww.evidence)
    assert "NVIDIA" in raws and "@nvidia" in raws
    assert all("self-reported" in e["title"] and e["source_url"].startswith("https://github.com/")
               for e in ww.evidence)
    assert {e["prior"] for e in ww.evidence} == {False, True}
    assert d.id not in {x for e in rows(db) for x in (e.src_id, e.dst_id)}


def test_idempotent_and_prunes_stale(db):
    a, b = person(db, "ada"), person(db, "ben")
    pp = paper(db, "2502.00002", datetime(2025, 5, 1, tzinfo=UTC), [a, b])
    first = rel.build(db)
    ids = {(e.src_id, e.dst_id, e.kind): e.id for e in rows(db)}
    second = rel.build(db)
    assert first == {**second, "deleted": first["deleted"]} and second["deleted"] == 0
    assert {(e.src_id, e.dst_id, e.kind): e.id for e in rows(db)} == ids  # upsert keeps row identity
    db.execute(delete(PaperAuthor).where(PaperAuthor.paper_id == pp.id, PaperAuthor.person_id == b.id))
    third = rel.build(db)
    assert third["deleted"] == 2 and third["COAUTHORED_WITH"] == 0
    assert db.scalar(select(func.count()).select_from(Relationship)) == 1


def test_giant_org_skipped_unless_cocontributors(db):
    people = [person(db, f"g{i}", company="Google") for i in range(rel.GIANT_ORG_PEOPLE + 1)]
    r = repo(db, "x/y")
    contrib(db, r, people[0], 5)
    contrib(db, r, people[1], 5)
    rel.build(db)
    ww = rows(db, "WORKED_WITH")
    assert len(ww) == 1 and {ww[0].src_id, ww[0].dst_id} == {people[0].id, people[1].id}


def test_large_author_lists_and_demo_flag(db):
    many = [person(db, f"p{i}") for i in range(rel.MAX_PAPER_AUTHORS + 1)]
    paper(db, "2503.00003", datetime(2025, 6, 1, tzinfo=UTC), many)
    a, b = person(db, "ada", demo=True), person(db, "ben")
    paper(db, "2504.00004", datetime(2025, 7, 1, tzinfo=UTC), [a, b])
    rel.build(db)
    co = rows(db, "COAUTHORED_WITH")
    assert len(co) == 1 and co[0].is_demo
    assert len(rows(db, "AUTHORED")) == len(many) + 2


def test_employer_key_filters_non_employers():
    assert rel.employer_key("Independent") is None and rel.employer_key("stealth") is None
    assert rel.employer_key("✖️") is None
    assert rel.employer_key("@nvidia")[0] == rel.employer_key("NVIDIA")[0]
    assert rel.membership_epistemic("https://github.com/someone", "founder") == "INFERRED"
    assert rel.membership_epistemic("https://github.com/org/repo", "contributor") == "VERIFIED"
    assert rel.membership_epistemic(None, "member") == "INFERRED"
    assert date  # keep import used
    assert uuid


def test_evidence_cap_keeps_oldest_dated_items():
    items = [{"title": f"r{i}", "event_at": f"20{10 + i:02d}-01-01T00:00:00+00:00"} for i in range(15)]
    items.append({"title": "undated", "event_at": None})
    kept = rel.keep_evidence(items, 10)
    titles = [e["title"] for e in kept]
    assert len(kept) == 10 and "r0" in titles and "r14" in titles  # oldest and newest both survive
    assert [e["event_at"] for e in kept] == sorted((e["event_at"] for e in kept), reverse=True)
