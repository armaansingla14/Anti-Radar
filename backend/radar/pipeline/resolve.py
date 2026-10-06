"""Entity resolution: natural-key `EntityRef`s -> database rows.

Rules (conservative by design — a wrong merge corrupts intelligence, a missed
merge only costs a review click):

* Deterministic keys first (domain, GitHub org/user, X handle, arXiv id/author key,
  repo full name). These are exact, case-insensitive lookups.
* Alternate keys (`record.alt_refs` plus keys implied by record fields such as the
  website domain) may *link* a record to an existing row when the primary key finds
  nothing. If primary and alternate keys point at *different* rows we never merge:
  a `MatchCandidate(method='conflicting_keys')` is written and the primary row wins.
* Fuzzy name similarity never merges; it only creates pending `MatchCandidate`s.
* `arxiv_author:<normalized name>` is a name, not an identity: it never resolves on
  its own. Each (paper, author) pair goes through `resolve_arxiv_author`, which
  reuses the person already listed on that paper, or a same-name person only when
  corroborated (shared co-author, identical affiliation string, or a shared code
  repo). Otherwise a new person is created (same `arxiv_author_key`) with a pending
  `MatchCandidate(method='same_name')` for review.
* An arXiv author and a GitHub user are linked only when their normalized full
  names are identical AND the GitHub user contributes to a repository that is the
  code for a paper written by that author (evidence kept in FieldProvenance and
  `Person.attrs['links']`).
"""

from __future__ import annotations

import difflib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from radar import clock
from radar.demo.fictional import CATEGORY_HOST as DEMO_ARXIV_HOST
from radar.demo.fictional import GH_HOST as DEMO_GH_HOST
from radar.demo.fictional import X_HOST as DEMO_X_HOST
from radar.models import (
    Company,
    Epistemic,
    FieldProvenance,
    MatchCandidate,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
)
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    PaperRecord,
    PersonRecord,
    RepoRecord,
    normalize_person_name,
    strip_arxiv_version,
)
from radar.providers.domains import company_domain, github_repos_in, is_academic_domain, slugify

type Entity = Company | Person | Repository | ResearchPaper
type AnyRecord = CompanyRecord | PersonRecord | RepoRecord | PaperRecord

FUZZY_COMPANY_THRESHOLD = 0.88
CODE_FOR_LINK_CONFIDENCE = 0.9
_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "corp", "co", "incorporated", "limited", "corporation"}

def normalize_company_name(name: str) -> str:
    """Lower-case, punctuation-free, legal suffixes (inc/llc/ltd/corp/co) stripped."""
    tokens = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def split_key(ref: EntityRef) -> tuple[str, str]:
    kind, _, value = ref.key.partition(":")
    return kind, value.strip()


def canonical_url(entity: Entity) -> str:
    """Best canonical public URL for an entity (used when a record has no source_url).

    Fictional demo rows only ever point at reserved `.example` hosts, never at real
    github.com / x.com / arxiv.org pages that a stranger could register.
    """
    demo = bool(getattr(entity, "is_demo", False))
    gh, x, arxiv = (DEMO_GH_HOST, DEMO_X_HOST, DEMO_ARXIV_HOST) if demo else (
        "https://github.com/", "https://x.com/", "https://arxiv.org/abs/")
    if isinstance(entity, Repository):
        html = (entity.attrs or {}).get("html_url") if demo else None
        return html or f"{gh}{entity.full_name}"
    if isinstance(entity, ResearchPaper):
        return entity.url or f"{arxiv}{entity.arxiv_id}"
    if isinstance(entity, Person):
        if entity.github_handle:
            return f"{gh}{entity.github_handle}"
        if entity.x_handle:
            return f"{x}{entity.x_handle}"
        return entity.personal_site or ""
    if entity.github_org:
        return f"{gh}{entity.github_org}"
    if entity.website:
        return entity.website
    return f"https://{entity.domain}" if entity.domain else ""


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def record_provenance(
    session: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    field: str,
    value: Any,
    *,
    source_type: str,
    source_url: str | None,
    confidence: float = 1.0,
    epistemic: str = Epistemic.FACT.value,
) -> None:
    """Upsert one FieldProvenance row (unique per entity/field/source_url)."""
    stmt = pg_insert(FieldProvenance).values(
        id=uuid.uuid4(),
        entity_type=entity_type,
        entity_id=entity_id,
        field=field,
        value=_jsonable(value),
        source_url=source_url or "",
        source_type=source_type,
        confidence=confidence,
        epistemic=epistemic,
        last_updated=clock.now(),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_field_prov",
        set_={
            "value": stmt.excluded.value,
            "source_type": stmt.excluded.source_type,
            "confidence": stmt.excluded.confidence,
            "epistemic": stmt.excluded.epistemic,
            "last_updated": stmt.excluded.last_updated,
        },
    )
    session.execute(stmt)


@dataclass
class Resolved:
    entity: Entity
    created: bool
    via: str  # "key" | "alt:<key>" | "code_for_link" | "created" | arXiv: "same_paper" | "corroborated:<how>"


@dataclass
class AuthorContext:
    """What one paper says about one of its authors (input to `resolve_arxiv_author`)."""

    name: str
    position: int
    affiliation: str | None
    coauthors: set[str]  # normalized full names of the other authors on the paper
    code_repos: set[str]  # lower-case owner/name of the paper's code repos


# Ways an arXiv author is tied to an existing person by evidence beyond the name.
CORROBORATED_AUTHOR_VIA = ("corroborated:shared_coauthor", "corroborated:same_affiliation",
                           "corroborated:shared_code", "code_for_link")
SAME_NAME_CANDIDATE_CONFIDENCE = 0.3
MAX_SAME_NAME_CANDIDATES = 10


class Resolver:
    """Resolves refs to rows within one session, caching lookups for the batch."""

    def __init__(self, session: Session, *, is_demo: bool = False, provider: str = "system") -> None:
        self.session = session
        self.is_demo = is_demo
        self.provider = provider
        self.candidates_created = 0
        self.created: list[tuple[str, uuid.UUID]] = []
        self._cache: dict[tuple[str, str], Entity] = {}
        self._company_names: dict[uuid.UUID, str] | None = None
        self._person_names: dict[str, list[uuid.UUID]] | None = None

    # ── public API ──────────────────────────────────────────────────────────

    def lookup(self, ref: EntityRef) -> Entity | None:
        """Deterministic key lookup only. Never creates; never fuzzy."""
        ck = (ref.type, ref.key.lower())
        hit = self._cache.get(ck)
        if hit is not None:
            return hit
        found = self._query(ref)
        if found is not None:
            self._cache[ck] = found
        return found

    def resolve(
        self,
        ref: EntityRef,
        record: AnyRecord | None = None,
        *,
        create: bool = True,
        name_hint: str | None = None,
        code_repos: set[str] | None = None,
    ) -> Resolved | None:
        """Find or create the row for `ref` (+ the record's alternate keys).

        `code_repos` (lower-case owner/name) enables the arXiv-author <-> GitHub-user
        link rule for people: for a GitHub user, repos it contributes to; for an
        arXiv author, code repos of papers it authored.
        """
        if ref.type == "person" and split_key(ref)[0] == "arxiv_author":
            return None  # name-only key: needs paper context, see resolve_arxiv_author
        alts = [r for r in self._alt_refs(record) if r.type == ref.type and r.key.lower() != ref.key.lower()]
        primary = self.lookup(ref)
        alt_hits = [(r, self.lookup(r)) for r in alts]
        source_url = getattr(record, "source_url", None)

        entity: Entity | None = primary
        via = "key"
        created = False
        if primary is not None:
            for r, other in alt_hits:
                if other is not None and other.id != primary.id:
                    self._conflict(ref, r, primary, other)
        else:
            matched = [(r, e) for r, e in alt_hits if e is not None]
            # A row that already carries a *different* value of this record's own primary key is a
            # different entity (e.g. a sub-org sharing the parent's website): never link through it.
            disjoint = [(r, e) for r, e in matched if self._holds_other_key(e, ref)]
            matched = [(r, e) for r, e in matched if (r, e) not in disjoint]
            if matched:
                link_ref, entity = matched[0]
                via = f"alt:{link_ref.key}"
                for r, other in matched[1:]:
                    if other.id != entity.id:
                        self._conflict(link_ref, r, entity, other)
                record_provenance(
                    self.session, ref.type, entity.id, "resolution_link",
                    {"ref": ref.key, "matched_via": link_ref.key, "method": "alt_ref_exact"},
                    source_type=self.provider, source_url=source_url or canonical_url(entity),
                    epistemic=Epistemic.INFERENCE.value if self.is_demo else Epistemic.FACT.value,
                )
            elif ref.type == "person" and code_repos:
                name = getattr(record, "name", None) or name_hint
                link = self._code_for_link(ref, name, code_repos) if name else None
                if link is not None:
                    entity, evidence = link
                    via = "code_for_link"
                    self._record_person_link(entity, ref, evidence, source_url)
            if entity is None:
                if not create:
                    return None
                entity = self._create(ref, record, name_hint)
                if entity is None:
                    return None
                created = True
                via = "created"

        self._attach_keys(entity, [ref, *alts], record)
        self._cache[(ref.type, ref.key.lower())] = entity
        if primary is None:
            for r, other in disjoint:
                if other.id != entity.id:
                    self.add_candidate(
                        ref.type, entity.id, other.id,
                        method="shared_domain" if split_key(r)[0] == "domain" else "conflicting_keys", confidence=0.5,
                        evidence={"keys": [ref.key, r.key], "provider": self.provider,
                                  "note": f"shares {r.key} with a row that has a different "
                                          f"{split_key(ref)[0]}; not merged"},
                    )
        if created:
            self.created.append((ref.type, entity.id))
            if isinstance(entity, Company):
                self._fuzzy_company_candidates(entity)
        return Resolved(entity=entity, created=created, via=via)

    def resolve_arxiv_author(self, ref: EntityRef, paper: ResearchPaper, ctx: AuthorContext) -> Resolved | None:
        """Person for one author position of one paper. Never merges on the name alone.

        1. the person already recorded as this author of this paper (re-ingest) -> "same_paper"
        2. exactly one same-name person with corroborating evidence -> "corroborated:<how>"
        3. a GitHub person with the identical full name contributing to the paper's code -> "code_for_link"
        4. otherwise a new person; every same-name person gets a pending `same_name` candidate.
        """
        norm = normalize_person_name(split_key(ref)[1])
        if not norm:
            return None
        on_paper = self.session.scalars(
            select(Person).join(PaperAuthor, PaperAuthor.person_id == Person.id)
            .where(PaperAuthor.paper_id == paper.id, Person.arxiv_author_key == norm)
            .order_by(func.abs(PaperAuthor.position - ctx.position), Person.id)
        ).first()
        if on_paper is not None:
            return Resolved(entity=on_paper, created=False, via="same_paper")
        same_name = self.session.scalars(
            select(Person).where(Person.arxiv_author_key == norm).order_by(Person.created_at, Person.id)
        ).all()
        corroborated = [(p, ev) for p in same_name if (ev := self._author_corroboration(p, norm, ctx))]
        if len(corroborated) == 1:
            person, evidence = corroborated[0]
            self._record_person_link(person, ref, {**evidence, "paper": paper.arxiv_id}, paper.url)
            return Resolved(entity=person, created=False, via=f"corroborated:{evidence['method']}")
        if not corroborated and ctx.code_repos:
            link = self._code_for_link(ref, ctx.name, ctx.code_repos)
            if link is not None:
                person, evidence = link
                if not person.arxiv_author_key:
                    person.arxiv_author_key = norm
                self._record_person_link(person, ref, evidence, paper.url)
                return Resolved(entity=person, created=False, via="code_for_link")
        person = self._create(ref, None, ctx.name)
        assert isinstance(person, Person)
        person.arxiv_author_key = norm
        self.created.append(("person", person.id))
        strong = {p.id: ev for p, ev in corroborated}
        for other in same_name[:MAX_SAME_NAME_CANDIDATES]:
            ev = strong.get(other.id)
            self.add_candidate(
                "person", person.id, other.id, method="same_name",
                confidence=0.6 if ev else SAME_NAME_CANDIDATE_CONFIDENCE,
                evidence={"name": norm, "key": ref.key, "paper": paper.arxiv_id, "provider": self.provider,
                          **({"corroboration": ev, "note": "several same-name people corroborated; not merged"}
                             if ev else {"note": "same arXiv author name only; no shared co-author, "
                                                 "affiliation or code repo; not merged"})},
            )
        return Resolved(entity=person, created=True, via="created")

    def _author_corroboration(self, person: Person, norm: str, ctx: AuthorContext) -> dict | None:
        """Evidence beyond the name that `person` is the author described by `ctx`, or None."""
        rows = self.session.execute(
            select(ResearchPaper.arxiv_id, ResearchPaper.authors, ResearchPaper.code_urls, PaperAuthor.position)
            .join(PaperAuthor, PaperAuthor.paper_id == ResearchPaper.id)
            .where(PaperAuthor.person_id == person.id)
        ).all()
        coauthors: dict[str, set[str]] = defaultdict(set)
        affiliations: dict[str, set[str]] = defaultdict(set)
        repos: dict[str, set[str]] = defaultdict(set)
        for aid, authors, code_urls, pos in rows:
            for i, a in enumerate(authors or []):
                n = normalize_person_name(a.get("name") or "")
                if n == norm and (i == pos or pos >= len(authors)):
                    if a.get("affiliation"):
                        affiliations[_affiliation_norm(a["affiliation"])].add(aid)
                elif n != norm and len(n.split()) >= 2:
                    coauthors[n].add(aid)
            for u in code_urls or []:
                for slug in github_repos_in(u):
                    repos[slug.lower()].add(aid)
        shared = sorted(set(coauthors) & ctx.coauthors)
        if shared:
            return {"method": "shared_coauthor", "coauthors": shared[:5],
                    "papers": sorted({a for n in shared for a in coauthors[n]})}
        aff = _affiliation_norm(ctx.affiliation or "")
        if aff and aff in affiliations:
            return {"method": "same_affiliation", "affiliation": ctx.affiliation, "papers": sorted(affiliations[aff])}
        for full in self.session.scalars(
            select(Repository.full_name).join(RepoContributor, RepoContributor.repository_id == Repository.id)
            .where(RepoContributor.person_id == person.id)
        ):
            repos[full.lower()].add("contributor")
        code = sorted(set(repos) & {r.lower() for r in ctx.code_repos})
        if code:
            return {"method": "shared_code", "repos": code,
                    "papers": sorted({a for r in code for a in repos[r] if a != "contributor"})}
        return None

    def add_candidate(
        self, entity_type: str, a_id: uuid.UUID, b_id: uuid.UUID, *, method: str, confidence: float, evidence: dict
    ) -> bool:
        """Insert a pending MatchCandidate for an unordered pair (idempotent)."""
        if a_id == b_id:
            return False
        a, b = sorted([a_id, b_id], key=str)
        res = self.session.execute(
            pg_insert(MatchCandidate)
            .values(
                id=uuid.uuid4(), entity_type=entity_type, a_id=a, b_id=b, method=method,
                confidence=round(confidence, 3), evidence=_jsonable(evidence), status="pending",
            )
            .on_conflict_do_nothing(constraint="uq_match_pair")
        )
        inserted = bool(res.rowcount)
        self.candidates_created += int(inserted)
        return inserted

    def people_with_name(self, normalized_name: str) -> list[uuid.UUID]:
        if self._person_names is None:
            self._person_names = defaultdict(list)
            for pid, name in self.session.execute(select(Person.id, Person.name)):
                self._person_names[normalize_person_name(name)].append(pid)
        return list(self._person_names.get(normalized_name, []))

    def forget(self) -> None:
        """Drop cached rows (call after a savepoint rollback)."""
        self._cache.clear()
        self._company_names = None
        self._person_names = None

    # ── lookups ─────────────────────────────────────────────────────────────

    def _query(self, ref: EntityRef) -> Entity | None:
        kind, value = split_key(ref)
        if not value:
            return None
        v = value.lower()
        s = self.session
        if ref.type == "company":
            col = {"domain": Company.domain, "github_org": Company.github_org, "x": Company.x_handle}.get(kind)
            if col is not None:
                return self._unique(select(Company).where(func.lower(col) == v.lstrip("@")))
            if kind == "name":
                norm = normalize_company_name(value)
                ids = [cid for cid, n in self._company_name_index().items() if n == norm]
                return s.get(Company, ids[0]) if len(ids) == 1 else None
        elif ref.type == "person":
            if kind == "github_user":
                return self._unique(select(Person).where(func.lower(Person.github_handle) == v))
            if kind == "x":
                return self._unique(select(Person).where(func.lower(Person.x_handle) == v.lstrip("@")))
            if kind == "arxiv_author":
                return None  # a name is not an identity; see resolve_arxiv_author
            if kind == "name":
                ids = self.people_with_name(normalize_person_name(value))
                return s.get(Person, ids[0]) if len(ids) == 1 else None
        elif ref.type == "repository" and kind == "repo":
            return self._unique(select(Repository).where(func.lower(Repository.full_name) == v))
        elif ref.type == "paper" and kind == "arxiv":
            return s.scalars(
                select(ResearchPaper).where(ResearchPaper.arxiv_id == strip_arxiv_version(value))
            ).first()
        return None

    @staticmethod
    def _holds_other_key(entity: Entity, ref: EntityRef) -> bool:
        """True when `entity` already stores a different value for `ref`'s deterministic key type."""
        kind, value = split_key(ref)
        field = _KEY_FIELDS.get((ref.type, kind))
        current = getattr(entity, field, None) if field else None
        if not current or not value:
            return False
        return str(current).lower().lstrip("@") != value.lower().lstrip("@")

    def _unique(self, stmt) -> Entity | None:
        rows = self.session.scalars(stmt.limit(2)).all()
        return rows[0] if len(rows) == 1 else None

    def _company_name_index(self) -> dict[uuid.UUID, str]:
        if self._company_names is None:
            self._company_names = {
                cid: normalize_company_name(n) for cid, n in self.session.execute(select(Company.id, Company.name))
            }
        return self._company_names

    # ── alternate keys implied by records ───────────────────────────────────

    @staticmethod
    def _alt_refs(record: AnyRecord | None) -> list[EntityRef]:
        if record is None:
            return []
        refs: list[EntityRef] = list(getattr(record, "alt_refs", []) or [])
        if isinstance(record, CompanyRecord):
            dom = company_domain(record.website)
            if dom and not is_academic_domain(dom):
                refs.append(EntityRef(type="company", key=f"domain:{dom}"))
            if record.github_org:
                refs.append(EntityRef(type="company", key=f"github_org:{record.github_org.lower()}"))
            if record.x_handle:
                refs.append(EntityRef(type="company", key=f"x:{record.x_handle.lower().lstrip('@')}"))
        elif isinstance(record, PersonRecord):
            if record.github_handle:
                refs.append(EntityRef(type="person", key=f"github_user:{record.github_handle.lower()}"))
            if record.x_handle:
                refs.append(EntityRef(type="person", key=f"x:{record.x_handle.lower().lstrip('@')}"))
        seen: set[str] = set()
        out = []
        for r in refs:
            if r.key.lower() not in seen:
                seen.add(r.key.lower())
                out.append(r)
        return out

    def _attach_keys(self, entity: Entity, refs: list[EntityRef], record: AnyRecord | None) -> None:
        """Copy deterministic keys onto the row when empty and not owned by another row."""
        for r in refs:
            kind, value = split_key(r)
            if not value:
                continue
            field = _KEY_FIELDS.get((r.type, kind))
            if field is None or getattr(entity, field):
                continue
            owner = self.lookup(r)
            if owner is not None and owner.id != entity.id:
                continue
            setattr(entity, field, self._key_value(kind, value, record))
            self._cache[(r.type, r.key.lower())] = entity

    @staticmethod
    def _key_value(kind: str, value: str, record: AnyRecord | None) -> str:
        """Stored form of a key; keeps the record's original casing for GitHub handles."""
        original = None
        if kind == "github_org" and isinstance(record, CompanyRecord):
            original = record.github_org
        elif kind == "github_user" and isinstance(record, PersonRecord):
            original = record.github_handle
        if original and original.lower() == value.lower():
            return original
        if kind == "arxiv_author":
            return normalize_person_name(value)
        if kind == "domain":
            return value.lower()
        return value.lstrip("@")

    # ── creation ────────────────────────────────────────────────────────────

    def _create(self, ref: EntityRef, record: AnyRecord | None, name_hint: str | None) -> Entity | None:
        kind, value = split_key(ref)
        common: dict[str, Any] = {"id": uuid.uuid4(), "is_demo": self.is_demo}
        attrs: dict[str, Any] = {} if record is not None else {"stub": True}
        if ref.type == "company":
            name = record.name if isinstance(record, CompanyRecord) else (name_hint or value)
            ent: Entity = Company(
                **common, slug=self._unique_slug(Company, name), name=name, kind="company",
                thesis_tags=[], attrs=attrs,
            )
            if self._company_names is not None:
                self._company_names[ent.id] = normalize_company_name(name)
        elif ref.type == "person":
            if isinstance(record, PersonRecord):
                name = record.name
            elif name_hint:
                name = name_hint
            else:
                name = value.title() if kind in ("arxiv_author", "name") else value
            ent = Person(
                **common, slug=self._unique_slug(Person, name), name=name,
                prior_affiliations=[], domains=[], attrs=attrs,
            )
            if self._person_names is not None:
                self._person_names[normalize_person_name(name)].append(ent.id)
        elif ref.type == "repository":
            full = record.full_name if isinstance(record, RepoRecord) else value
            if "/" not in full:
                return None
            ent = Repository(
                **common, full_name=full, owner=full.split("/")[0], topics=[], attrs=attrs,
                stars=0, forks=0, watchers=0, open_issues=0, archived=False,
            )
        elif ref.type == "paper":
            if not isinstance(record, PaperRecord):
                return None  # never invent a paper title
            ent = ResearchPaper(
                **common, arxiv_id=strip_arxiv_version(record.arxiv_id), title=record.title,
                categories=[], authors=[], code_urls=[], attrs={},
            )
        else:
            return None
        self.session.add(ent)
        self.session.flush([ent])
        return ent

    def _unique_slug(self, model: type, name: str) -> str:
        base = slugify(name)
        taken = set(
            self.session.scalars(
                select(model.slug).where((model.slug == base) | model.slug.like(f"{base}-%"))
            ).all()
        )
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    # ── candidates ──────────────────────────────────────────────────────────

    def _conflict(self, ref_a: EntityRef, ref_b: EntityRef, a: Entity, b: Entity) -> None:
        self.add_candidate(
            ref_a.type, a.id, b.id, method="conflicting_keys", confidence=0.5,
            evidence={"keys": [ref_a.key, ref_b.key], "provider": self.provider,
                      "note": "record keys identify two different existing rows; not merged"},
        )

    def _fuzzy_company_candidates(self, company: Company) -> None:
        norm = normalize_company_name(company.name)
        if len(norm) < 3:
            return
        for cid, other in self._company_name_index().items():
            if cid == company.id or not other:
                continue
            sm = difflib.SequenceMatcher(None, norm, other)
            if sm.real_quick_ratio() < FUZZY_COMPANY_THRESHOLD or sm.quick_ratio() < FUZZY_COMPANY_THRESHOLD:
                continue  # cheap upper bounds first; ratio() is the expensive part
            ratio = sm.ratio()
            if ratio >= FUZZY_COMPANY_THRESHOLD:
                self.add_candidate(
                    "company", company.id, cid, method="fuzzy_name", confidence=ratio,
                    evidence={"names": [norm, other], "ratio": round(ratio, 3)},
                )

    # ── arXiv author <-> GitHub user link ───────────────────────────────────

    def _code_for_link(self, ref: EntityRef, name: str, code_repos: set[str]) -> tuple[Person, dict] | None:
        kind, value = split_key(ref)
        norm = normalize_person_name(name)
        if len(norm.split()) < 2:
            return None  # only full names may link
        repos = {r.lower() for r in code_repos}
        hits: list[tuple[Person, dict]] = []
        if kind == "github_user":
            people = self.session.scalars(
                select(Person).where(Person.arxiv_author_key == norm, Person.github_handle.is_(None))
            ).all()
            for p in people:
                papers = self.session.execute(
                    select(ResearchPaper.arxiv_id, ResearchPaper.code_urls)
                    .join(PaperAuthor, PaperAuthor.paper_id == ResearchPaper.id)
                    .where(PaperAuthor.person_id == p.id)
                ).all()
                shared = {
                    (aid, slug.lower())
                    for aid, urls in papers for url in (urls or []) for slug in github_repos_in(url)
                    if slug.lower() in repos
                }
                if shared:
                    hits.append((p, {"papers": sorted({a for a, _ in shared}), "repos": sorted({s for _, s in shared})}))
        elif kind == "arxiv_author":
            if normalize_person_name(value) != norm:
                return None
            rows = self.session.execute(
                select(Person, Repository.full_name)
                .join(RepoContributor, RepoContributor.person_id == Person.id)
                .join(Repository, Repository.id == RepoContributor.repository_id)
                .where(
                    func.lower(Repository.full_name).in_(repos),
                    Person.github_handle.is_not(None),
                    Person.arxiv_author_key.is_(None),
                )
            ).all()
            by_person: dict[uuid.UUID, tuple[Person, set[str]]] = {}
            for p, full in rows:
                if normalize_person_name(p.name) == norm:
                    by_person.setdefault(p.id, (p, set()))[1].add(full.lower())
            hits = [(p, {"repos": sorted(rs)}) for p, rs in by_person.values()]
        if len(hits) != 1:
            return None  # none, or ambiguous: never guess
        person, evidence = hits[0]
        evidence.update({"method": "name+code_for", "normalized_name": norm})
        return person, evidence

    def _record_person_link(self, person: Person, ref: EntityRef, evidence: dict, source_url: str | None) -> None:
        link = {"key": ref.key, **evidence, "linked_at": clock.now().isoformat()}
        attrs = dict(person.attrs or {})
        attrs["links"] = [*attrs.get("links", []), link]
        person.attrs = attrs
        record_provenance(
            self.session, "person", person.id, "identity_link", link,
            source_type=self.provider, source_url=source_url or canonical_url(person),
            confidence=CODE_FOR_LINK_CONFIDENCE, epistemic=Epistemic.INFERENCE.value,
        )


def _affiliation_norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


_KEY_FIELDS: dict[tuple[str, str], str] = {
    ("company", "domain"): "domain",
    ("company", "github_org"): "github_org",
    ("company", "x"): "x_handle",
    ("person", "github_user"): "github_handle",
    ("person", "arxiv_author"): "arxiv_author_key",
    ("person", "x"): "x_handle",
}
