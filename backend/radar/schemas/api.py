"""Public API response/request models — the contract between backend and frontend.

Every user-visible claim object carries `epistemic` (FACT | INFERENCE | MODEL_OPINION)
and/or a source link, and every entity carries `is_demo` so the UI can label
synthetic data. Keep this file the single source of truth; the frontend mirrors
it in frontend/lib/types.ts.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Epistemic = Literal["FACT", "INFERENCE", "MODEL_OPINION"]
Verification = Literal["VERIFIED", "INFERRED", "UNKNOWN"]
EntityKind = Literal["company", "person", "repository", "paper"]


# ── Score ────────────────────────────────────────────────────────────────────


class EvidenceItem(BaseModel):
    signal_id: str | None = None
    title: str
    detail: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    event_at: datetime | None = None
    weight: float | None = None  # contribution weight within component, if meaningful


class ScoreComponent(BaseModel):
    key: str  # technical_momentum | founder_signal | acceleration | thesis_fit | novelty | attention_gap
    label: str
    value: float
    max: float
    explanation: str  # one deterministic sentence: why this value
    raw: dict[str, Any] = Field(default_factory=dict)  # the measured inputs
    evidence: list[EvidenceItem] = Field(default_factory=list)


class AttentionGap(BaseModel):
    technical: float  # 0..100 percentile of technical signal
    attention: float  # 0..100 percentile of public attention
    gap: float  # technical - attention (can be negative)
    flagged: bool
    coverage: Literal["good", "partial", "low"]
    sources: list[dict[str, Any]]  # [{key, label, available: bool, value, percentile, note}]


class Collision(BaseModel):
    score: float  # 0..1
    families: list[str]  # independent source families present in window
    window_days: int
    span_days: float | None
    signal_count: int
    evidence: list[EvidenceItem] = Field(default_factory=list)
    summary: str  # e.g. "4 independent sources within 18 days"


class AntiScore(BaseModel):
    total: int
    delta_7d: float | None = None
    delta_30d: float | None = None
    components: list[ScoreComponent]
    attention_gap: AttentionGap | None = None
    collision: Collision | None = None
    version: str
    computed_on: date
    feedback_adjustment: float = 0.0
    adjustments: list[dict[str, Any]] = Field(default_factory=list)  # [{key, label, points, reason}]
    cohort_size: int = 0
    low_coverage: bool = False


class ScorePoint(BaseModel):
    day: date
    score: float


# ── Signals ──────────────────────────────────────────────────────────────────


class SignalOut(BaseModel):
    id: str
    entity_id: str
    entity_type: str
    entity_name: str | None = None
    entity_slug: str | None = None
    signal_type: str
    source_type: str
    source_url: str | None = None
    title: str
    description: str | None = None
    raw_value: float | None = None
    normalized_value: float | None = None
    event_at: datetime
    detected_at: datetime
    confidence: float
    epistemic: Epistemic
    is_demo: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class TimeSeries(BaseModel):
    metric: str
    label: str
    points: list[dict[str, Any]]  # [{day: 'YYYY-MM-DD', value: float}]
    source: Literal["observed", "backfill", "demo", "mixed"] = "observed"
    note: str | None = None


# ── Entities ─────────────────────────────────────────────────────────────────


class FactField(BaseModel):
    """A displayed field with provenance."""

    label: str
    value: Any
    status: Verification
    source_url: str | None = None
    source_type: str | None = None
    note: str | None = None


class TopicTag(BaseModel):
    key: str  # theme.subcategory
    theme: str
    label: str
    score: float
    method: str  # keyword | llm | manual


class PersonCard(BaseModel):
    id: str
    slug: str
    name: str
    headline: str | None = None  # "Former NVIDIA robotics researcher"
    current_company: str | None = None
    current_role: str | None = None
    prior_affiliations: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    github_handle: str | None = None
    x_handle: str | None = None
    personal_site: str | None = None
    avatar_url: str | None = None
    founder_score: int | None = None
    delta_7d: float | None = None
    why_now: list[str] = Field(default_factory=list)  # deterministic bullets
    is_founder: bool = False
    role: str | None = None
    is_demo: bool = False
    first_seen_at: datetime | None = None


class RepoCard(BaseModel):
    id: str
    full_name: str
    description: str | None = None
    language: str | None = None
    stars: int
    forks: int
    stars_30d: float | None = None
    commits_4w: float | None = None
    contributors: int | None = None
    gh_created_at: datetime | None = None
    url: str
    is_demo: bool = False


class PaperCard(BaseModel):
    id: str
    arxiv_id: str
    title: str
    abstract: str | None = None
    authors: list[str]
    categories: list[str]
    published_at: datetime | None = None
    url: str | None = None
    code_urls: list[str] = Field(default_factory=list)
    linked_entities: list[dict[str, Any]] = Field(default_factory=list)  # [{type, slug, name}]
    why_it_matters: str | None = None
    why_it_matters_epistemic: Epistemic | None = None
    why_it_matters_generator: Literal["llm", "template", "cached"] | None = None
    score: int | None = None
    is_demo: bool = False


class IntelCard(BaseModel):
    """The editorial card on the Radar feed. Works for companies and people."""

    rank: int
    entity_type: EntityKind
    id: str
    slug: str
    name: str
    kind_label: str  # "STEALTH ROBOTICS COMPANY", "OPEN-SOURCE PROJECT", "FOUNDER"
    one_liner: str | None = None
    score: int
    delta_7d: float | None = None
    delta_30d: float | None = None
    thesis_tags: list[TopicTag] = Field(default_factory=list)
    why_surfaced: list[EvidenceItem]  # "Why it surfaced" bullets — deterministic from signals
    why_anti: list[str] = Field(default_factory=list)  # "Why Anti Fund might care" — deterministic/thesis based
    why_anti_epistemic: Epistemic = "INFERENCE"
    attention_gap: AttentionGap | None = None
    collision: Collision | None = None
    sparkline: list[float] = Field(default_factory=list)  # e.g. stars by day, last 30d
    sparkline_label: str | None = None
    first_seen_at: datetime | None = None
    is_new: bool = False  # entered radar in the last 7 days (Unknown Yesterday)
    watched: bool = False
    is_demo: bool = False
    data_note: str | None = None  # e.g. "Real data captured 2026-10-06"


class CompanySummary(BaseModel):
    id: str
    slug: str
    name: str
    one_liner: str | None = None
    kind: str
    stage: str
    score: int | None = None
    delta_7d: float | None = None
    thesis_tags: list[TopicTag] = Field(default_factory=list)
    attention_gap: float | None = None
    collision: float | None = None
    stars_30d: float | None = None
    first_seen_at: datetime | None = None
    website: str | None = None
    github_org: str | None = None
    is_demo: bool = False
    watched: bool = False


class TimelineEvent(BaseModel):
    day: date
    title: str
    detail: str | None = None
    source_type: str
    source_url: str | None = None
    signal_type: str
    epistemic: Epistemic = "FACT"
    is_demo: bool = False


class SourceRef(BaseModel):
    id: str  # S1, S2 … within a document, or a source uuid
    url: str | None = None
    title: str | None = None
    source_type: str | None = None
    fetched_at: datetime | None = None


class Narrative(BaseModel):
    """A generated narrative block. Always labeled with how it was produced."""

    text: str
    epistemic: Epistemic = "MODEL_OPINION"
    generator: Literal["llm", "template", "cached"] = "template"
    model: str | None = None
    citations: list[str] = Field(default_factory=list)  # source ids or signal ids


class CompanyDetail(BaseModel):
    summary: CompanySummary
    description: str | None = None
    facts: list[FactField]  # website, founded, location, stage estimate, github org, x
    anti_score: AntiScore | None = None
    score_history: list[ScorePoint] = Field(default_factory=list)
    why_now: Narrative | None = None
    why_anti: Narrative | None = None
    risks: list[Narrative] = Field(default_factory=list)  # questions / risks
    timeline: list[TimelineEvent] = Field(default_factory=list)
    founders: list[PersonCard] = Field(default_factory=list)
    repositories: list[RepoCard] = Field(default_factory=list)
    papers: list[PaperCard] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    hiring: dict[str, Any] = Field(default_factory=dict)  # {open_roles, by_class:{}, new_30d}
    series: list[TimeSeries] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    reports: list[dict[str, Any]] = Field(default_factory=list)  # [{id, kind, title, created_at}]
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    watched: bool = False
    data_note: str | None = None


class PersonDetail(BaseModel):
    card: PersonCard
    bio: str | None = None
    facts: list[FactField]
    founder_score: AntiScore | None = None
    score_history: list[ScorePoint] = Field(default_factory=list)
    why_now: Narrative | None = None
    timeline: list[TimelineEvent] = Field(default_factory=list)
    companies: list[CompanySummary] = Field(default_factory=list)
    repositories: list[RepoCard] = Field(default_factory=list)
    papers: list[PaperCard] = Field(default_factory=list)
    collaborators: list[PersonCard] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    watched: bool = False
    data_note: str | None = None


# ── Feeds ────────────────────────────────────────────────────────────────────


class StrangeSignal(BaseModel):
    id: str
    pattern: str  # machine key, e.g. shared_prior_employer_new_org
    headline: str  # "Three former SpaceX propulsion engineers created the same GitHub organization"
    detail: str
    entities: list[dict[str, Any]]  # [{type, id, slug, name}]
    evidence: list[EvidenceItem]
    detected_at: datetime
    strength: float  # 0..1
    is_demo: bool = False


class ThesisShift(BaseModel):
    topic_key: str
    label: str
    theme: str
    direction: Literal["heating", "cooling"]
    detail: str
    momentum: float


class RadarCounts(BaseModel):
    emerging_companies: int
    founders: int
    strange_signals: int
    thesis_shifts: int
    new_entities_7d: int
    signals_24h: int


class RadarFeed(BaseModel):
    date: date
    generated_at: datetime
    counts: RadarCounts
    cards: list[IntelCard]
    founders: list[IntelCard]
    strange_signals: list[StrangeSignal]
    thesis_shifts: list[ThesisShift]
    data_freshness: dict[str, Any]  # {provider: last_success_iso}
    demo_mode: bool
    snapshot_note: str | None = None


class WatchChange(BaseModel):
    entity_type: str
    entity_id: str
    slug: str
    name: str
    changes: list[str]  # "2 new engineering roles", "GitHub stars +21% (30d)"
    score: int | None = None
    delta: float | None = None
    is_demo: bool = False


class DailyRadar(BaseModel):
    date: date
    since: datetime
    founders: list[IntelCard]
    companies: list[IntelCard]
    papers: list[PaperCard]
    strange_signals: list[StrangeSignal]
    watchlist_changes: list[WatchChange]
    new_entities: list[CompanySummary | PersonCard]
    summary: Narrative | None = None


class BriefItem(BaseModel):
    n: int
    text: str
    entity_refs: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # signal ids


class MorningBrief(BaseModel):
    date: date
    what_changed: list[BriefItem]
    founders_to_know: list[IntelCard]
    companies_to_watch: list[IntelCard]
    research: list[PaperCard]
    strange_signal: StrangeSignal | None = None
    narrative: Narrative | None = None
    markdown: str
    generator: Literal["llm", "template", "cached"]
    generated_at: datetime | None = None


class ThesisCell(BaseModel):
    key: str
    label: str
    theme: str
    momentum: float  # 0..100
    momentum_trend: Literal["up2", "up", "flat", "down"]
    attention: float | None  # 0..100; None when no linked company has an attention measurement
    attention_trend: Literal["up2", "up", "flat", "down"]
    attention_gap: bool
    companies: int
    emerging_companies: int
    founders: int
    papers_30d: int
    hiring_roles_30d: int
    top_entities: list[dict[str, Any]]  # [{type, slug, name, score}]


class ThesisTheme(BaseModel):
    key: str
    label: str
    weight: float
    momentum: float
    cells: list[ThesisCell]


class ThesisMap(BaseModel):
    themes: list[ThesisTheme]
    generated_at: datetime


# ── Search ───────────────────────────────────────────────────────────────────


class SearchFilters(BaseModel):
    entity_types: list[EntityKind] = Field(default_factory=lambda: ["company", "person"])
    themes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    prior_affiliations: list[str] = Field(default_factory=list)
    hiring_role_classes: list[str] = Field(default_factory=list)
    min_score: int | None = None
    min_attention_gap: float | None = None
    max_attention: float | None = None
    accelerating: bool = False
    first_seen_within_days: int | None = None
    event_within_days: int | None = None
    signal_types: list[str] = Field(default_factory=list)
    stage: list[str] = Field(default_factory=list)
    sort: Literal["score", "momentum", "attention_gap", "recent", "delta"] = "score"


class SearchResult(BaseModel):
    entity_type: EntityKind
    id: str
    slug: str
    name: str
    subtitle: str | None = None
    score: int | None = None
    match_reasons: list[str]
    is_demo: bool = False


class SearchResponse(BaseModel):
    query: str
    filters: SearchFilters
    interpretation: list[str]  # human-readable chips: "theme: robotics", "left a tier-1 lab"
    parser: Literal["deterministic", "llm", "hybrid"]
    results: list[SearchResult]
    total: int


# ── Workflow ─────────────────────────────────────────────────────────────────


class WatchRequest(BaseModel):
    entity_type: Literal["company", "person", "repository", "topic"]
    entity_id: str | None = None
    topic_key: str | None = None
    note: str | None = None


class FeedbackRequest(BaseModel):
    entity_type: EntityKind
    entity_id: str
    label: Literal[
        "useful", "not_interesting", "too_obvious", "too_early", "wrong_thesis", "bad_data", "already_known", "dismiss"
    ]
    note: str | None = Field(default=None, max_length=2000)


class InvestigateRequest(BaseModel):
    entity_type: Literal["company", "person"]
    entity_id: str
    live_fetch: bool = True  # allow the agent to fetch fresh public data


class MemoRequest(BaseModel):
    entity_type: Literal["company", "person"]
    entity_id: str
    report_id: str | None = None  # base memo on an existing brief


class IngestGithubRequest(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    repo: str | None = Field(default=None, max_length=200, pattern=r"^[\w.\-]+/[\w.\-]+$")
    org: str | None = Field(default=None, max_length=100, pattern=r"^[\w.\-]+$")
    limit: int = Field(default=10, ge=1, le=50)


class IngestUrlRequest(BaseModel):
    url: str = Field(max_length=2000)
    company_name: str | None = Field(default=None, max_length=200)


class IngestXRequest(BaseModel):
    url: str = Field(max_length=500)
    pasted_text: str | None = Field(default=None, max_length=5000)
    entity_type: Literal["company", "person"] | None = None
    entity_id: str | None = None


class ReportOut(BaseModel):
    id: str
    kind: str
    title: str
    entity_type: str | None = None
    entity_id: str | None = None
    entity_slug: str | None = None
    body: dict[str, Any]
    markdown: str
    sources: list[dict[str, Any]]
    model: str | None = None
    generator: str
    status: str
    cost_usd: float
    created_at: datetime
    is_demo: bool = False


class ProviderStatus(BaseModel):
    provider: str
    label: str
    state: Literal["healthy", "degraded", "error", "disabled", "never_run"]
    reason: str | None = None
    last_success_at: datetime | None = None
    last_run_at: datetime | None = None
    records_collected: int = 0
    records_rejected: int = 0
    signals_created: int = 0
    errors: list[str] = Field(default_factory=list)
    rate_limit_remaining: int | None = None
    latency_ms_p50: float | None = None
    runs_24h: int = 0


class SystemStatus(BaseModel):
    providers: list[ProviderStatus]
    llm: dict[str, Any]  # {provider, enabled, models:{}, calls_24h, cost_24h, cost_total, cache_hit_rate}
    database: dict[str, Any]  # counts per table, snapshot days
    demo_mode: bool
    score_version: str
    last_scored_at: datetime | None = None


class RetroEntry(BaseModel):
    name: str
    slug: str | None
    why_included: str
    observable_signals: list[EvidenceItem]
    signal_families: list[str]
    history_available: bool
    history_note: str
    current_score: int | None = None
    framework_reading: str  # deterministic: which parts of the framework fire
    series: list[TimeSeries] = Field(default_factory=list)


class Retrospective(BaseModel):
    disclaimer: str
    method: str
    entries: list[RetroEntry]
    takeaways: list[str]


class GraphNode(BaseModel):
    id: str
    type: str  # person | company | repository | paper | org
    label: str
    slug: str | None = None
    score: int | None = None
    is_demo: bool = False


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: str


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


# ── Workflow responses (watchlist, feedback, ingest) ─────────────────────────


class WatchItemOut(BaseModel):
    id: str
    entity_type: str  # company | person | repository | topic
    entity_id: str
    topic_key: str | None = None
    name: str
    slug: str | None = None
    note: str | None = None
    created_at: datetime
    last_seen_at: datetime
    score: int | None = None
    delta_7d: float | None = None
    is_demo: bool = False
    changes: list[str] = Field(default_factory=list)  # deterministic, since last_seen_at


class WatchlistOut(BaseModel):
    name: str
    items: list[WatchItemOut]
    changes_since_last_visit: int  # total change lines across items


class OkOut(BaseModel):
    ok: bool = True
    detail: str | None = None


class FeedbackOut(BaseModel):
    ok: bool = True
    weights: dict[str, float]  # all learned preference multipliers after this feedback
    changed: dict[str, float] = Field(default_factory=dict)  # the multipliers this feedback moved
    dismissed: bool = False


class IngestRunOut(BaseModel):
    provider: str
    status: str
    records_fetched: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    signals_created: int = 0
    errors: list[str] = Field(default_factory=list)


class IngestEntityRef(BaseModel):
    type: str  # company | person | repository
    id: str
    slug: str
    name: str


class IngestOut(BaseModel):
    run: IngestRunOut
    entity: IngestEntityRef | None = None
    info: dict[str, Any] = Field(default_factory=dict)  # e.g. X text_source: oembed | user_provided
    recomputed: bool = False
