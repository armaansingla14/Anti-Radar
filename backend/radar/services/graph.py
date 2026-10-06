"""Founder graph: people, companies, repositories, papers and prior-affiliation orgs.

Edges
-----
person -> company   founder | member
person -> repository contributor
person -> paper     author
paper  -> repository code_for (paper.code_urls point at the repo)
person -> org       prior_affiliation (org node id ``org:<name>``)
company -> repository owns;  paper -> company  published_by

Centered on an entity: breadth-first expansion to ``depth`` hops (each hop is one
batched query per node type). Without a center: the top 25 scored companies/people
and their 1-hop neighbourhoods. Always capped at ``MAX_NODES``.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompanyPerson,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
)
from radar.providers.domains import github_repos_in
from radar.schemas.api import GraphEdge, GraphNode, GraphOut
from radar.services.presenters import latest_snapshots

MAX_NODES = 120
TOP_N = 25
ORG_PREFIX = "org:"
ORG_MEMBERS_LIMIT = 15  # an org node expands to at most this many people


class GraphEntityNotFound(LookupError):
    pass


@dataclass
class _G:
    nodes: dict[str, GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], GraphEdge] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)

    def full(self) -> bool:
        return len(self.nodes) >= MAX_NODES

    def node(self, nid: str, ntype: str, label: str, slug: str | None = None, is_demo: bool = False) -> bool:
        if nid in self.nodes:
            return False
        if self.full():
            return False
        self.nodes[nid] = GraphNode(id=nid, type=ntype, label=label, slug=slug, is_demo=is_demo)
        self.types[nid] = ntype
        return True

    def edge(self, a: str, b: str, kind: str) -> None:
        if a in self.nodes and b in self.nodes:
            self.edges.setdefault((a, b, kind), GraphEdge(source=a, target=b, kind=kind))


def _add_entity(g: _G, ent) -> str:
    nid = str(ent.id)
    if isinstance(ent, Company):
        g.node(nid, "company", ent.name, ent.slug, ent.is_demo)
    elif isinstance(ent, Person):
        g.node(nid, "person", ent.name, ent.slug, ent.is_demo)
    elif isinstance(ent, Repository):
        g.node(nid, "repository", ent.full_name, ent.full_name, ent.is_demo)
    elif isinstance(ent, ResearchPaper):
        g.node(nid, "paper", ent.title[:120], ent.arxiv_id, ent.is_demo)
    return nid


def _org_id(name: str) -> str:
    return ORG_PREFIX + name


def _expand(session: Session, g: _G, frontier: set[str]) -> set[str]:
    """Add all neighbours of ``frontier`` nodes; return the newly added node ids."""
    by_type: dict[str, list] = defaultdict(list)
    for nid in frontier:
        t = g.types.get(nid)
        if t == "org":
            by_type["org"].append(nid[len(ORG_PREFIX):])
        elif t:
            by_type[t].append(uuid.UUID(nid))
    before = set(g.nodes)
    pending: list[tuple[str, str, str]] = []  # edges to add once both ends exist

    def link(a: str, b: str, kind: str) -> None:
        pending.append((a, b, kind))

    companies, people = by_type.get("company", []), by_type.get("person", [])
    repos, papers, orgs = by_type.get("repository", []), by_type.get("paper", []), by_type.get("org", [])

    # company/person memberships
    if companies or people:
        q = (select(CompanyPerson, Company, Person).join(Company, Company.id == CompanyPerson.company_id)
             .join(Person, Person.id == CompanyPerson.person_id)
             .where(or_(CompanyPerson.company_id.in_(companies or [uuid.uuid4()]),
                        CompanyPerson.person_id.in_(people or [uuid.uuid4()])))
             .order_by(CompanyPerson.is_founder.desc()))
        for cp, c, p in session.execute(q).tuples():
            _add_entity(g, c)
            _add_entity(g, p)
            link(str(p.id), str(c.id), "founder" if cp.is_founder else "member")
    # repositories owned by companies / contributed by people
    if companies:
        for r in session.scalars(select(Repository).where(Repository.company_id.in_(companies))
                                 .order_by(Repository.stars.desc())):
            _add_entity(g, r)
            link(str(r.company_id), str(r.id), "owns")
        for paper in session.scalars(select(ResearchPaper).where(ResearchPaper.company_id.in_(companies))
                                     .order_by(ResearchPaper.published_at.desc().nulls_last())):
            _add_entity(g, paper)
            link(str(paper.id), str(paper.company_id), "published_by")
    if people or repos:
        q = (select(RepoContributor, Repository, Person).join(Repository, Repository.id == RepoContributor.repository_id)
             .join(Person, Person.id == RepoContributor.person_id)
             .where(or_(RepoContributor.person_id.in_(people or [uuid.uuid4()]),
                        RepoContributor.repository_id.in_(repos or [uuid.uuid4()])))
             .order_by(RepoContributor.contributions.desc()))
        for _rc, r, p in session.execute(q).tuples():
            _add_entity(g, r)
            _add_entity(g, p)
            link(str(p.id), str(r.id), "contributor")
    if repos:
        for r, c in session.execute(select(Repository, Company).join(Company, Company.id == Repository.company_id)
                                    .where(Repository.id.in_(repos))).tuples():
            _add_entity(g, c)
            link(str(c.id), str(r.id), "owns")
    # papers: authors and code repos
    if people or papers:
        q = (select(PaperAuthor, ResearchPaper, Person).join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
             .join(Person, Person.id == PaperAuthor.person_id)
             .where(or_(PaperAuthor.person_id.in_(people or [uuid.uuid4()]),
                        PaperAuthor.paper_id.in_(papers or [uuid.uuid4()])))
             .order_by(ResearchPaper.published_at.desc().nulls_last(), PaperAuthor.position))
        for _pa, paper, p in session.execute(q).tuples():
            _add_entity(g, paper)
            _add_entity(g, p)
            link(str(p.id), str(paper.id), "author")
    paper_rows = list(session.scalars(select(ResearchPaper).where(ResearchPaper.id.in_(papers)))) if papers else []
    paper_cos = {p.company_id for p in paper_rows if p.company_id}
    if paper_cos:
        cos = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(paper_cos)))}
        for paper in paper_rows:
            if paper.company_id in cos:
                _add_entity(g, cos[paper.company_id])
                link(str(paper.id), str(paper.company_id), "published_by")
    code: dict[str, list[uuid.UUID]] = defaultdict(list)
    for paper in paper_rows:
        for u in paper.code_urls or []:
            for full in github_repos_in(u):
                code[full.lower()].append(paper.id)
    if code:
        for r in session.scalars(select(Repository).where(func.lower(Repository.full_name).in_(list(code)))):
            _add_entity(g, r)
            for pid in code[r.full_name.lower()]:
                link(str(pid), str(r.id), "code_for")
    if repos:
        repo_rows = list(session.scalars(select(Repository).where(Repository.id.in_(repos))))
        conds = [ResearchPaper.code_urls.any(f"https://{host}/{r.full_name}")
                 for r in repo_rows for host in ("github.com", "github.example")]
        if conds:
            for paper in session.scalars(select(ResearchPaper).where(or_(*conds))):
                _add_entity(g, paper)
                for r in repo_rows:
                    if any(r.full_name.lower() in s.lower() for s in (paper.code_urls or [])):
                        link(str(paper.id), str(r.id), "code_for")
    # prior affiliations
    if people:
        for p in session.scalars(select(Person).where(Person.id.in_(people))):
            for org in p.prior_affiliations or []:
                oid = _org_id(org)
                g.node(oid, "org", org, None, p.is_demo)
                link(str(p.id), oid, "prior_affiliation")
    if orgs:
        q = (select(Person).where(Person.prior_affiliations.overlap(orgs))
             .outerjoin(ScoreSnapshot, and_(ScoreSnapshot.entity_id == Person.id, ScoreSnapshot.day == clock.today()))
             .order_by(ScoreSnapshot.score.desc().nulls_last()).limit(ORG_MEMBERS_LIMIT * len(orgs)))
        for p in session.scalars(q):
            _add_entity(g, p)
            for org in p.prior_affiliations or []:
                if org in orgs:
                    link(str(p.id), _org_id(org), "prior_affiliation")

    for a, b, kind in pending:
        g.edge(a, b, kind)
    return set(g.nodes) - before


def _resolve(session: Session, ref: str):
    ref = ref.strip()
    if ref.startswith(ORG_PREFIX):
        name = ref[len(ORG_PREFIX):]
        exists = session.scalar(select(Person.id).where(Person.prior_affiliations.any(name)).limit(1))
        if exists is None:
            raise GraphEntityNotFound(f"unknown org {name!r}")
        return name
    try:
        uid = uuid.UUID(ref)
        for model in (Company, Person, Repository, ResearchPaper):
            ent = session.get(model, uid)
            if ent is not None:
                return ent
    except ValueError:
        pass
    for model, col in ((Company, Company.slug), (Person, Person.slug), (Repository, Repository.full_name),
                       (ResearchPaper, ResearchPaper.arxiv_id)):
        ent = session.scalar(select(model).where(col == ref))
        if ent is not None:
            return ent
    raise GraphEntityNotFound(f"unknown entity {ref!r}")


def _close_code_edges(session: Session, g: _G) -> None:
    """paper -> repository code_for edges between nodes already in the graph (one query)."""
    paper_ids = [uuid.UUID(n.id) for n in g.nodes.values() if n.type == "paper"]
    repos = {n.label.lower(): n.id for n in g.nodes.values() if n.type == "repository"}
    if not paper_ids or not repos:
        return
    for pid, urls in session.execute(select(ResearchPaper.id, ResearchPaper.code_urls)
                                     .where(ResearchPaper.id.in_(paper_ids))).tuples():
        for u in urls or []:
            for full in github_repos_in(u):
                if full.lower() in repos:
                    g.edge(str(pid), repos[full.lower()], "code_for")


def _finish(session: Session, g: _G) -> GraphOut:
    _close_code_edges(session, g)
    scored = [uuid.UUID(n.id) for n in g.nodes.values() if n.type in ("company", "person")]
    snaps = latest_snapshots(session, scored)
    nodes = []
    for n in g.nodes.values():
        if n.type in ("company", "person"):
            snap = snaps.get(uuid.UUID(n.id))
            n.score = round(snap.score) if snap else None
        nodes.append(n)
    return GraphOut(nodes=nodes, edges=list(g.edges.values()))


def founder_graph(session: Session, entity_id: str | None = None, depth: int = 1) -> GraphOut:
    g = _G()
    if entity_id:
        center = _resolve(session, entity_id)
        if isinstance(center, str):
            g.node(_org_id(center), "org", center)
            frontier = {_org_id(center)}
        else:
            frontier = {_add_entity(g, center)}
        for _ in range(max(1, min(depth, 2))):
            if g.full() or not frontier:
                break
            frontier = _expand(session, g, frontier)
        return _finish(session, g)

    day = clock.today()
    top = session.execute(
        select(ScoreSnapshot.entity_id, ScoreSnapshot.entity_type).where(
            ScoreSnapshot.day == day, ScoreSnapshot.entity_type.in_(["company", "person"]))
        .order_by(ScoreSnapshot.score.desc()).limit(TOP_N * 2)
    ).all()
    company_ids = [e for e, t in top if t == "company"]
    person_ids = [e for e, t in top if t == "person"]
    ents: dict[uuid.UUID, object] = {}
    if company_ids:
        ents.update({c.id: c for c in session.scalars(select(Company).where(Company.id.in_(company_ids),
                                                                            Company.dismissed.is_(False)))})
    if person_ids:
        ents.update({p.id: p for p in session.scalars(select(Person).where(Person.id.in_(person_ids),
                                                                           Person.dismissed.is_(False)))})
    frontier = set()
    for eid, _t in top:
        if eid in ents and len(frontier) < TOP_N:
            frontier.add(_add_entity(g, ents[eid]))
    if frontier:
        _expand(session, g, frontier)
    return _finish(session, g)
