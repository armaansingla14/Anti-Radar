"""Provider contract.

Providers are *pure with respect to the database*: they fetch from the outside
world and return a `ProviderBatch` of normalized records that reference
entities by natural keys (`EntityRef`). The ingestion pipeline resolves refs to
database rows (entity resolution), upserts, writes snapshots and signals.

This keeps provider parsing trivially testable from recorded fixtures and lets
new sources plug in without touching the database layer.

Natural key conventions (EntityRef.key):
    company     "domain:acme.com" | "github_org:acme" | "x:acmerobotics" | "name:Acme Robotics"
    person      "github_user:jdoe" | "arxiv_author:jane doe" | "x:janedoe" | "name:Jane Doe"
    repository  "repo:owner/name"            (lower-cased)
    paper       "arxiv:2410.01234"           (no version suffix)
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

EntityKind = Literal["company", "person", "repository", "paper"]


class EntityRef(BaseModel):
    type: EntityKind
    key: str

    def __hash__(self) -> int:
        return hash((self.type, self.key))


class CompanyRecord(BaseModel):
    ref: EntityRef
    name: str
    description: str | None = None
    website: str | None = None
    github_org: str | None = None
    x_handle: str | None = None
    location: str | None = None
    kind: str = "company"  # company | stealth | project | lab
    founded_date: date | None = None
    alt_refs: list[EntityRef] = Field(default_factory=list)  # other keys known to identify this company
    attrs: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None


class PersonRecord(BaseModel):
    ref: EntityRef
    name: str
    bio: str | None = None
    location: str | None = None
    github_handle: str | None = None
    x_handle: str | None = None
    personal_site: str | None = None
    current_company: str | None = None
    followers: int | None = None
    email_domain: str | None = None
    alt_refs: list[EntityRef] = Field(default_factory=list)
    attrs: dict[str, Any] = Field(default_factory=dict)
    source_url: str | None = None


class RepoRecord(BaseModel):
    ref: EntityRef
    full_name: str
    owner: str
    owner_type: str | None = None
    description: str | None = None
    homepage: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    open_issues: int = 0
    contributors_count: int | None = None
    gh_created_at: datetime | None = None
    pushed_at: datetime | None = None
    readme_excerpt: str | None = None
    license: str | None = None
    archived: bool = False
    owner_company: EntityRef | None = None  # company that owns the repo (GitHub org)
    attrs: dict[str, Any] = Field(default_factory=dict)


class PaperRecord(BaseModel):
    ref: EntityRef
    arxiv_id: str
    title: str
    abstract: str | None = None
    categories: list[str] = Field(default_factory=list)
    primary_category: str | None = None
    published_at: datetime | None = None
    updated_at: datetime | None = None
    authors: list[dict[str, Any]] = Field(default_factory=list)  # [{name, affiliation?}]
    code_urls: list[str] = Field(default_factory=list)
    url: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)


class JobRecord(BaseModel):
    company: EntityRef
    external_id: str
    ats: str | None = None
    title: str
    department: str | None = None
    location: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    # False when the board response was truncated: the job list is a lower bound, so jobs
    # missing from it must not be treated as closed.
    complete: bool = True


class Relation(BaseModel):
    """Typed edge between two entities."""

    kind: Literal["contributor", "author", "member", "founder", "owns", "mentions", "code_for"]
    src: EntityRef  # person for contributor/author/member/founder; company for owns; paper for code_for
    dst: EntityRef
    attrs: dict[str, Any] = Field(default_factory=dict)  # e.g. contributions, role, position
    confidence: float = 1.0
    source_url: str | None = None


class MetricPoint(BaseModel):
    entity: EntityRef
    metric: str  # stars | forks | contributors | commits_week | releases | hn_points | open_roles | followers
    day: date
    value: float
    source: Literal["observed", "backfill", "demo"] = "observed"


class NormalizedSignal(BaseModel):
    entity: EntityRef
    signal_type: str
    source_type: str
    source_url: str | None = None
    title: str
    description: str | None = None
    raw_value: float | None = None
    normalized_value: float | None = None  # 0..1 strength
    event_at: datetime
    confidence: float = 1.0
    epistemic: Literal["FACT", "INFERENCE", "MODEL_OPINION"] = "FACT"
    related: list[EntityRef] = Field(default_factory=list)  # other entities this signal concerns
    metadata: dict[str, Any] = Field(default_factory=dict)

    def dedup_key(self) -> str:
        """Same source + url + type + entity + event day = same signal."""
        day = self.event_at.astimezone(timezone.utc).date().isoformat()
        basis = "|".join(
            [self.source_type, self.source_url or "", self.signal_type, self.entity.type, self.entity.key, day]
        )
        return hashlib.sha256(basis.encode()).hexdigest()


class ProviderBatch(BaseModel):
    provider: str
    companies: list[CompanyRecord] = Field(default_factory=list)
    people: list[PersonRecord] = Field(default_factory=list)
    repositories: list[RepoRecord] = Field(default_factory=list)
    papers: list[PaperRecord] = Field(default_factory=list)
    jobs: list[JobRecord] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)
    metrics: list[MetricPoint] = Field(default_factory=list)
    signals: list[NormalizedSignal] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)  # {url, source_type, title, snippet, content_hash}
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)

    def extend(self, other: "ProviderBatch") -> None:
        for f in ("companies", "people", "repositories", "papers", "jobs", "relations", "metrics", "signals",
                  "sources", "errors"):
            getattr(self, f).extend(getattr(other, f))
        self.rejected += other.rejected

    def counts(self) -> dict[str, int]:
        return {
            f: len(getattr(self, f))
            for f in ("companies", "people", "repositories", "papers", "jobs", "relations", "metrics", "signals")
        }


class Provider(Protocol):
    name: str

    def enabled(self) -> tuple[bool, str]:
        """(enabled, reason). Disabled providers are shown on the status page with the reason."""
        ...


# ── Helpers shared by providers ──────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def repo_ref(full_name: str) -> EntityRef:
    return EntityRef(type="repository", key=f"repo:{full_name.lower()}")


def gh_user_ref(login: str) -> EntityRef:
    return EntityRef(type="person", key=f"github_user:{login.lower()}")


def gh_org_ref(login: str) -> EntityRef:
    return EntityRef(type="company", key=f"github_org:{login.lower()}")


def domain_ref(domain: str) -> EntityRef:
    return EntityRef(type="company", key=f"domain:{domain.lower()}")


def paper_ref(arxiv_id: str) -> EntityRef:
    return EntityRef(type="paper", key=f"arxiv:{strip_arxiv_version(arxiv_id)}")


def arxiv_author_ref(name: str) -> EntityRef:
    return EntityRef(type="person", key=f"arxiv_author:{normalize_person_name(name)}")


def strip_arxiv_version(arxiv_id: str) -> str:
    import re

    return re.sub(r"v\d+$", "", arxiv_id.strip())


def normalize_person_name(name: str) -> str:
    import re
    import unicodedata

    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z\s\-]", " ", n).lower()
    return re.sub(r"\s+", " ", n).strip()
