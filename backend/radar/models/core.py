"""ANTI RADAR data model.

Design notes
------------
* Entities (companies, people, repositories, papers) carry `first_seen_at` so the
  product can answer "what entered the radar recently" (Unknown Yesterday).
* Everything observed from the outside world is a `Signal` — a normalized,
  timestamped, sourced event. Scores are *derived* from signals + metric
  snapshots and stored as `ScoreSnapshot`s, so ranking history is reproducible.
* `MetricSnapshot` is the time-series backbone: one row per (entity, metric, day).
  The system gets more powerful every day it runs.
* `FieldProvenance` keeps value/source/confidence/epistemic-status for entity
  fields so FACT, INFERENCE and MODEL_OPINION are never conflated.
* `is_demo` marks fictional/synthetic records. The UI always labels them.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EntityMixin(TimestampMixin):
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    demo_label: Mapped[str | None] = mapped_column(String(64))


# ── Enums ────────────────────────────────────────────────────────────────────


class EntityType(str, enum.Enum):
    company = "company"
    person = "person"
    repository = "repository"
    paper = "paper"
    topic = "topic"


class Epistemic(str, enum.Enum):
    """How we know something. Never display MODEL_OPINION as FACT."""

    FACT = "FACT"  # directly observed from a source
    INFERENCE = "INFERENCE"  # derived deterministically from facts
    MODEL_OPINION = "MODEL_OPINION"  # produced by an LLM


class SourceType(str, enum.Enum):
    github = "github"
    arxiv = "arxiv"
    hackernews = "hackernews"
    website = "website"
    jobs = "jobs"
    x = "x"
    manual = "manual"
    demo = "demo"
    system = "system"


class FeedbackLabel(str, enum.Enum):
    useful = "useful"
    not_interesting = "not_interesting"
    too_obvious = "too_obvious"
    too_early = "too_early"
    wrong_thesis = "wrong_thesis"
    bad_data = "bad_data"
    already_known = "already_known"
    dismiss = "dismiss"


# ── Core entities ────────────────────────────────────────────────────────────


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    last_visit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # last_visit_at as it was when the current visit began (see services.feed.touch_last_visit):
    # the anchor for the Daily "since last visit" window.
    prev_visit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Company(Base, EntityMixin):
    __tablename__ = "companies"
    __table_args__ = (
        Index("ix_companies_first_seen_at", "first_seen_at"),  # Unknown Yesterday windows
        Index("ix_companies_updated_at", "updated_at"),  # services.memo view version (max)
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    founded_date: Mapped[date | None] = mapped_column(Date)
    stage: Mapped[str] = mapped_column(String(40), default="unknown", server_default="unknown")
    stage_basis: Mapped[str | None] = mapped_column(Text)  # why we estimated this stage
    location: Mapped[str | None] = mapped_column(String(200))
    thesis_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    github_org: Mapped[str | None] = mapped_column(String(200), unique=True)
    x_handle: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(40), default="company", server_default="company")
    # company | stealth | project (open-source project not yet a company) | lab
    status: Mapped[str] = mapped_column(String(40), default="active", server_default="active")
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)

    people: Mapped[list[CompanyPerson]] = relationship(back_populates="company", cascade="all, delete-orphan")
    repositories: Mapped[list[Repository]] = relationship(back_populates="company")


class Person(Base, EntityMixin):
    __tablename__ = "people"
    __table_args__ = (
        Index("ix_people_first_seen_at", "first_seen_at"),  # Unknown Yesterday windows
        Index("ix_people_updated_at", "updated_at"),  # services.memo view version (max)
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300))
    bio: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    github_handle: Mapped[str | None] = mapped_column(String(100), unique=True)
    x_handle: Mapped[str | None] = mapped_column(String(100))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    personal_site: Mapped[str | None] = mapped_column(String(500))
    current_company: Mapped[str | None] = mapped_column(String(300))
    current_role: Mapped[str | None] = mapped_column(String(300))
    prior_affiliations: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    domains: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    arxiv_author_key: Mapped[str | None] = mapped_column(String(200), index=True)
    followers: Mapped[int | None] = mapped_column(Integer)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)

    companies: Mapped[list[CompanyPerson]] = relationship(back_populates="person", cascade="all, delete-orphan")


class CompanyPerson(Base, TimestampMixin):
    __tablename__ = "company_people"
    __table_args__ = (UniqueConstraint("company_id", "person_id", name="uq_company_person"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    role: Mapped[str | None] = mapped_column(String(200))
    is_founder: Mapped[bool] = mapped_column(Boolean, default=False)
    start_at: Mapped[date | None] = mapped_column(Date)
    end_at: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    source_url: Mapped[str | None] = mapped_column(String(1000))

    company: Mapped[Company] = relationship(back_populates="people")
    person: Mapped[Person] = relationship(back_populates="companies")


class Repository(Base, EntityMixin):
    __tablename__ = "repositories"
    id: Mapped[uuid.UUID] = uuid_pk()
    full_name: Mapped[str] = mapped_column(String(300), unique=True, index=True)  # owner/name
    owner: Mapped[str] = mapped_column(String(200), index=True)
    owner_type: Mapped[str | None] = mapped_column(String(20))  # User | Organization
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    homepage: Mapped[str | None] = mapped_column(String(500))
    language: Mapped[str | None] = mapped_column(String(80))
    topics: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    stars: Mapped[int] = mapped_column(Integer, default=0)
    forks: Mapped[int] = mapped_column(Integer, default=0)
    watchers: Mapped[int] = mapped_column(Integer, default=0)
    open_issues: Mapped[int] = mapped_column(Integer, default=0)
    contributors_count: Mapped[int | None] = mapped_column(Integer)
    gh_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    readme_excerpt: Mapped[str | None] = mapped_column(Text)
    readme_hash: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str | None] = mapped_column(String(80))
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)

    company: Mapped[Company | None] = relationship(back_populates="repositories")


class ResearchPaper(Base, EntityMixin):
    __tablename__ = "research_papers"
    __table_args__ = (Index("ix_research_papers_first_seen_at", "first_seen_at"),)  # recent_papers window
    id: Mapped[uuid.UUID] = uuid_pk()
    arxiv_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    primary_category: Mapped[str | None] = mapped_column(String(40))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_at_src: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authors: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # [{name, affiliation?}]
    code_urls: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    url: Mapped[str | None] = mapped_column(String(500))
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"))
    embedding = mapped_column(Vector(1536), nullable=True)
    attrs: Mapped[dict] = mapped_column(JSONType, default=dict)


class PaperAuthor(Base):
    __tablename__ = "paper_authors"
    __table_args__ = (UniqueConstraint("paper_id", "person_id", name="uq_paper_author"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("research_papers.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class RepoContributor(Base):
    __tablename__ = "repo_contributors"
    __table_args__ = (UniqueConstraint("repository_id", "person_id", name="uq_repo_contributor"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    contributions: Mapped[int] = mapped_column(Integer, default=0)
    first_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Sources, signals, provenance ─────────────────────────────────────────────


class Source(Base):
    """A fetched document or API response. Every claim in a report cites one."""

    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("url", "content_hash", name="uq_source_url_hash"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    url: Mapped[str] = mapped_column(String(2000), index=True)
    source_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Signal(Base):
    """A normalized, timestamped observation about an entity."""

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_entity_event", "entity_type", "entity_id", "event_at"),
        Index("ix_signals_type_event", "signal_type", "event_at"),
        Index("ix_signals_created_at", "created_at"),  # services.memo data version (max)
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    signal_type: Mapped[str] = mapped_column(String(60), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    raw_value: Mapped[float | None] = mapped_column(Float)
    normalized_value: Mapped[float | None] = mapped_column(Float)  # 0..1 strength
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    epistemic: Mapped[str] = mapped_column(String(20), default=Epistemic.FACT.value)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SignalEntity(Base):
    """Secondary links: a signal about a repo also concerns its company and contributors."""

    __tablename__ = "signal_entities"
    __table_args__ = (UniqueConstraint("signal_id", "entity_id", name="uq_signal_entity"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    signal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    relation: Mapped[str] = mapped_column(String(40), default="related")


class FieldProvenance(Base):
    __tablename__ = "field_provenance"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "field", "source_url", name="uq_field_prov"),
        Index("ix_prov_entity", "entity_type", "entity_id"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict | list | str | float | None] = mapped_column(JSONType)
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    source_type: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    epistemic: Mapped[str] = mapped_column(String(20), default=Epistemic.FACT.value)
    note: Mapped[str | None] = mapped_column(Text)  # e.g. "Self-reported on GitHub profile"
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MetricSnapshot(Base):
    """Daily time series. Think in derivatives."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint("entity_id", "metric", "day", name="uq_metric_day"),
        Index("ix_metric_entity_metric_day", "entity_id", "metric", "day"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    metric: Mapped[str] = mapped_column(String(60))
    day: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), default="observed")  # observed | backfill | demo
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


# Back-compat name used in the spec.
SignalSnapshot = MetricSnapshot


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    __table_args__ = (
        UniqueConstraint("entity_id", "day", name="uq_score_day"),
        Index("ix_score_type_day", "entity_type", "day"),
        Index("ix_score_snapshots_created_at", "created_at"),  # services.memo data version (max)
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    day: Mapped[date] = mapped_column(Date)
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    # {component: {value, max, raw:{...}, evidence:[signal ids], notes:[..]}}
    attention: Mapped[dict] = mapped_column(JSONType, default=dict)
    collision: Mapped[dict] = mapped_column(JSONType, default=dict)
    version: Mapped[str] = mapped_column(String(20))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyMetric(Base):
    """Latest derived metrics per company (denormalized for fast feeds)."""

    __tablename__ = "company_metrics"
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Topics ───────────────────────────────────────────────────────────────────


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(120), unique=True)  # theme.subcategory
    theme: Mapped[str] = mapped_column(String(60), index=True)
    label: Mapped[str] = mapped_column(String(200))


class EntityTopic(Base):
    __tablename__ = "entity_topics"
    __table_args__ = (UniqueConstraint("entity_id", "topic_id", name="uq_entity_topic"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20), default="keyword")  # keyword | llm | manual
    evidence: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")


# ── Hiring ───────────────────────────────────────────────────────────────────


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("company_id", "external_id", name="uq_job_ext"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(200))
    ats: Mapped[str | None] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(500))
    department: Mapped[str | None] = mapped_column(String(200))
    role_class: Mapped[str | None] = mapped_column(String(60))
    location: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


# ── Website intelligence ─────────────────────────────────────────────────────


class WebPage(Base):
    __tablename__ = "web_pages"
    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000), unique=True)
    page_kind: Mapped[str] = mapped_column(String(40))  # home | about | team | careers | blog | docs | changelog
    title: Mapped[str | None] = mapped_column(Text)
    text_excerpt: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extracted: Mapped[dict] = mapped_column(JSONType, default=dict)


# ── User workflow ────────────────────────────────────────────────────────────


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    items: Mapped[list[WatchlistItem]] = relationship(back_populates="watchlist", cascade="all, delete-orphan")


class WatchlistItem(Base, TimestampMixin):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "entity_id", name="uq_watch_item"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    watchlist_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    topic_key: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class UserFeedback(Base):
    __tablename__ = "user_feedback"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    entity_type: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    label: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSONType, default=dict)  # thesis tags + signal types at time of feedback
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PreferenceWeight(Base):
    """Lightweight learned multipliers from feedback, e.g. key='thesis:robotics'."""

    __tablename__ = "preference_weights"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    n: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserAction(Base):
    """Audit trail of user actions (watch, dismiss, investigate, memo, open)."""

    __tablename__ = "user_actions"
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchReport(Base, TimestampMixin):
    __tablename__ = "research_reports"
    __table_args__ = (Index("ix_research_reports_kind_created", "kind", "created_at"),)  # brief history
    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(30))  # brief | memo | morning_brief
    entity_type: Mapped[str | None] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[dict] = mapped_column(JSONType, default=dict)  # structured, validated JSON
    markdown: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # [{id:'S1', url, title, type}]
    model: Mapped[str | None] = mapped_column(String(80))
    generator: Mapped[str] = mapped_column(String(20), default="llm")  # llm | template | cached
    status: Mapped[str] = mapped_column(String(20), default="complete")
    trace: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # agent steps
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


# ── Entity resolution ────────────────────────────────────────────────────────


class MatchCandidate(Base):
    """Uncertain entity matches. Never silently merged."""

    __tablename__ = "match_candidates"
    __table_args__ = (UniqueConstraint("a_id", "b_id", name="uq_match_pair"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    entity_type: Mapped[str] = mapped_column(String(20))
    a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    method: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Operations ───────────────────────────────────────────────────────────────


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | ok | partial | error | skipped
    records_fetched: Mapped[int] = mapped_column(Integer, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    signals_created: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str]] = mapped_column(JSONType, default=list)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    latency_ms_p50: Mapped[float | None] = mapped_column(Float)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSONType, default=dict)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    purpose: Mapped[str] = mapped_column(String(60), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCache(Base):
    __tablename__ = "llm_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(model|prompt_version|input)
    purpose: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    response: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Relationships & formation (M2) ───────────────────────────────────────────


class Relationship(Base, TimestampMixin):
    """A deterministic edge between two entities, rebuilt by ``pipeline.relationships.build``.

    Person-person kinds (WORKED_WITH, COAUTHORED_WITH, CONTRIBUTED_WITH) are stored once with
    ``str(src_id) < str(dst_id)``. ``first_seen_at``/``last_seen_at`` are evidence dates (paper
    date, first commit, …), never ingestion times; NULL when the evidence carries no date
    (e.g. a shared self-reported affiliation)."""

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("src_id", "dst_id", "kind", name="uq_relationship"),
        Index("ix_relationships_kind", "kind"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    src_type: Mapped[str] = mapped_column(String(20))
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    dst_type: Mapped[str] = mapped_column(String(20))
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    # WORKED_WITH | COAUTHORED_WITH | CONTRIBUTED_WITH | MEMBER_OF | FOUNDED | AUTHORED | CONTRIBUTED_TO
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    epistemic: Mapped[str] = mapped_column(String(20), default="VERIFIED")  # VERIFIED | INFERRED
    evidence: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    # [{title, detail, source_url, source_type, event_at}]
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class FormationCandidate(Base):
    """A possible formation (team around a young org, or one person) with its FORMATION SCORE.

    Rebuilt by ``scoring.formation.run``; rows that no longer qualify are deleted."""

    __tablename__ = "formation_candidates"
    __table_args__ = (
        Index("ix_formation_tier_score", "tier", "score"),
        Index("ix_formation_candidates_updated_at", "updated_at"),  # home / view memo version (max)
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(220), unique=True)
    kind: Mapped[str] = mapped_column(String(10))  # team | person
    anchor_type: Mapped[str] = mapped_column(String(20))  # company | person
    anchor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    title: Mapped[str] = mapped_column(String(400))
    theme: Mapped[str | None] = mapped_column(String(60))
    subtopic: Mapped[str | None] = mapped_column(String(120))
    member_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list, server_default="{}")
    score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(10))  # high | emerging | early
    label: Mapped[str] = mapped_column(String(40))  # EMERGING TEAM | POSSIBLE FORMATION | FORMATION SIGNALS DETECTED
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    convergence: Mapped[dict] = mapped_column(JSONType, default=dict)
    signal_families: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    independent_signals: Mapped[int] = mapped_column(Integer, default=0)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    latest_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    why_now: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    day: Mapped[date] = mapped_column(Date)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ── Who Should We Meet (M3) ──────────────────────────────────────────────────


class MeetingRecommendation(Base):
    """One person recommended for a meeting in an ISO week (America/New_York), ranked by ``services.meet``.

    Rows are written once per week (idempotent) so the week view is stable; ``reasons`` keeps the
    deterministic explanation as it was when ranked: {why_person: [str], why_now: [EvidenceItem dict],
    what_changed: [str], thesis: [str], ...}."""

    __tablename__ = "meeting_recommendations"
    __table_args__ = (
        UniqueConstraint("week", "person_id", name="uq_meeting_week_person"),
        Index("ix_meeting_week_rank", "week", "rank"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    week: Mapped[str] = mapped_column(String(10))  # ISO week 'YYYY-Www'
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(10))  # HIGH | MEDIUM | WATCH
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[dict] = mapped_column(JSONType, default=dict)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence: Mapped[str] = mapped_column(String(20))  # HIGH CONFIDENCE | MEDIUM CONFIDENCE | EARLY SIGNAL
    formation_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Snapshot history (V3: since last snapshot) ───────────────────────────────


class FormationHistory(Base):
    """What a formation candidate looked like on a scored day.

    ``FormationCandidate`` keeps only the current state (rows that stop qualifying are deleted), so each
    formation run also appends one row per qualifying candidate here. Comparing two days yields
    NEW / STRENGTHENED / UNCHANGED / WEAKENED / DROPPED (``services.changes``)."""

    __tablename__ = "formation_history"
    __table_args__ = (
        UniqueConstraint("anchor_id", "day", name="uq_formation_history_anchor_day"),
        Index("ix_formation_history_day", "day"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    day: Mapped[date] = mapped_column(Date)
    slug: Mapped[str] = mapped_column(String(220))
    kind: Mapped[str] = mapped_column(String(10))
    anchor_type: Mapped[str] = mapped_column(String(20))
    anchor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(400))
    theme: Mapped[str | None] = mapped_column(String(60))
    score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(40))
    signal_families: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    independent_signals: Mapped[int] = mapped_column(Integer, default=0)
    member_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list, server_default="{}")
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Technical diligence (V3) ─────────────────────────────────────────────────


class DiligenceRun(Base):
    """One diligence pass over a company: what was collected (artifacts), what it claims (claims), and the
    structured DiligenceReport (a ``ResearchReport`` of kind ``diligence``). Collection (network) and
    analysis (deterministic, offline) are separate so a captured run replays without the network."""

    __tablename__ = "diligence_runs"
    __table_args__ = (Index("ix_diligence_runs_company_started", "company_id", "started_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | complete | error
    mode: Mapped[str] = mapped_column(String(10), default="live")  # live | replay
    method_version: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when artifacts were fetched
    sources_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    evidence_coverage: Mapped[float | None] = mapped_column(Float)  # share of claims with independent evidence
    summary: Mapped[dict] = mapped_column(JSONType, default=dict)  # counts by status / type / testability
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error: Mapped[str | None] = mapped_column(Text)
    trace: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # every fetch / step, for audit
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class DiligenceArtifact(Base):
    """A public artifact observed during diligence (page, README, manifest, release list, registry metadata,
    third-party post…). Untrusted data: text is stored for evidence quotes and never executed."""

    __tablename__ = "diligence_artifacts"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_diligence_artifact_key"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diligence_runs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(300))  # stable within a run, e.g. "readme:org/repo"
    kind: Mapped[str] = mapped_column(String(30))
    publisher: Mapped[str] = mapped_column(String(20))  # company | code | registry | third_party
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(500))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    flags: Mapped[list[str]] = mapped_column(JSONType, default=list)  # prompt-injection / safety flags


class DiligenceClaim(Base):
    """A material claim a company makes about itself, with the evidence for and against it.

    ``verification_status`` is decided by the deterministic rules in ``radar.diligence.rules`` from the
    listed evidence; ``status_reason`` records which rule fired."""

    __tablename__ = "diligence_claims"
    __table_args__ = (Index("ix_diligence_claims_company", "company_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diligence_runs.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # DiligenceArtifact
    source_url: Mapped[str | None] = mapped_column(Text)
    source_quote: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    epistemic: Mapped[str] = mapped_column(String(20), default="COMPANY_CLAIM")
    testability: Mapped[str] = mapped_column(String(20))  # HIGH | MEDIUM | LOW | NOT_TESTABLE
    verification_status: Mapped[str] = mapped_column(String(30))
    status_reason: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[str] = mapped_column(String(10))  # HIGH | MEDIUM | LOW
    company_evidence: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    independent_evidence: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    contradictory_evidence: Mapped[list[dict]] = mapped_column(JSONType, default=list)
    test_plan: Mapped[dict | None] = mapped_column(JSONType)
    extracted_by: Mapped[str] = mapped_column(String(20), default="rules")  # rules | llm
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewDecision(Base):
    """Human judgment recorded next to an agent assessment (claims, underwriting categories, failures)."""

    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_target", "target_type", "target_id"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    target_type: Mapped[str] = mapped_column(String(30))  # claim | underwriting | report | failure
    target_id: Mapped[str] = mapped_column(String(120))
    decision: Mapped[str] = mapped_column(String(30))  # ACCEPT | QUESTION | REJECT | NEEDS_VERIFICATION
    note: Mapped[str] = mapped_column(Text, default="")
    reviewer: Mapped[str] = mapped_column(String(120), default="analyst")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ── Agent evaluation (V3) ────────────────────────────────────────────────────


class AgentRunEvaluation(Base):
    """Automatic evaluation of one agent output (a ResearchReport): validity, citations, unsupported claims,
    coverage, latency, cost. Metrics that cannot be computed stay NULL, never estimated."""

    __tablename__ = "agent_run_evaluations"
    __table_args__ = (
        UniqueConstraint("report_id", "evaluator_version", name="uq_agent_eval_report_version"),
        Index("ix_agent_eval_agent", "agent"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("research_reports.id", ondelete="CASCADE"))
    agent: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    generator: Mapped[str | None] = mapped_column(String(20))
    evaluator_version: Mapped[str] = mapped_column(String(20))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    task_success: Mapped[bool] = mapped_column(Boolean, default=True)
    structured_output_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    claims_total: Mapped[int] = mapped_column(Integer, default=0)
    fact_claims: Mapped[int] = mapped_column(Integer, default=0)
    citations_total: Mapped[int] = mapped_column(Integer, default=0)
    invalid_citations: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_claims: Mapped[int] = mapped_column(Integer, default=0)
    unsupported_claim_rate: Mapped[float | None] = mapped_column(Float)
    citation_precision: Mapped[float | None] = mapped_column(Float)
    source_coverage: Mapped[float | None] = mapped_column(Float)
    sources_registered: Mapped[int] = mapped_column(Integer, default=0)
    sources_used: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list[dict]] = mapped_column(JSONType, default=list)  # [{rule, category, severity, claim, ...}]
    review_status: Mapped[str] = mapped_column(String(20), default="AUTOMATIC")  # AUTOMATIC | HUMAN_REVIEWED
    notes: Mapped[str] = mapped_column(Text, default="")


class AgentFailure(Base):
    """A reviewed agent / ranking failure with its taxonomy category, resolution and regression case."""

    __tablename__ = "agent_failures"
    id: Mapped[uuid.UUID] = uuid_pk()
    number: Mapped[int] = mapped_column(Integer, unique=True)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_ref: Mapped[str | None] = mapped_column(String(220))
    claim_text: Mapped[str] = mapped_column(Text)
    evidence_summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # HIGH | MEDIUM | LOW
    resolution: Mapped[str] = mapped_column(Text, default="")
    regression_case: Mapped[str | None] = mapped_column(String(80))
    regression_test: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | CONFIRMED | FIXED | DISMISSED
    origin: Mapped[str] = mapped_column(String(20), default="evaluator")  # evaluator | documented | reviewer
    source_ref: Mapped[str | None] = mapped_column(Text)  # where it was found (doc section, report url)
    detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EvalCaseResult(Base):
    """One regression / comparison case run against one generator (template, or a configured model)."""

    __tablename__ = "eval_case_results"
    __table_args__ = (Index("ix_eval_case_results_case", "case_id", "run_at"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    case_id: Mapped[str] = mapped_column(String(80))
    agent: Mapped[str] = mapped_column(String(40))
    task: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    passed: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
