"""Portfolio Pulse schemas: PORTFOLIO ANALYST report bodies (validated by the evaluator) and API responses.

Public mode: no response model has a ``private_fields`` field, and positions are serialised from an explicit
allow-list (``PositionOut``), so private investment data can never leak through the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Materiality = Literal["MATERIAL", "WATCH", "NOISE"]
Epistemic = Literal["FACT", "INFERENCE"]
ActionValue = Literal["REVIEW", "MONITOR", "UPDATE_COMPETITIVE_MAP", "RUN_DILIGENCE", "PREPARE_FOUNDER_QUESTIONS",
                      "NO_ACTION"]
PortfolioDecisionValue = Literal["MARK_REVIEWED", "NEEDS_FOLLOW_UP", "MONITOR", "NO_ACTION"]
QuestionStatus = Literal["OPEN", "ANSWERED", "DISMISSED"]


# ── report bodies (evals.metrics.SCHEMAS) ────────────────────────────────────


class ReportClaim(BaseModel):
    text: str = Field(min_length=1)
    epistemic: Epistemic
    source_ids: list[str]
    event_id: str | None = None
    materiality: Materiality | None = None
    relation: str | None = None
    company: str | None = None
    company_slug: str | None = None


class ReportQuestion(BaseModel):
    question: str
    event_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    source: str | None = None
    reason: str | None = None
    question_id: str | None = None


class Period(BaseModel):
    start: str
    end: str
    days: int


class SuggestedReview(BaseModel):
    action: ActionValue
    label: str
    reason: ReportClaim


class PortfolioReviewBody(BaseModel):
    agent: Literal["PORTFOLIO ANALYST"]
    kind: Literal["portfolio_review"]
    date: str
    company: dict[str, Any]
    period: Period
    summary: ReportClaim
    what_changed: list[ReportClaim]
    material_events: dict[str, list[ReportClaim]]
    open_questions: list[ReportQuestion]
    suggested_review: SuggestedReview
    noise_filtered: dict[str, int]
    limitations: str


class AttentionCardBody(BaseModel):
    company: dict[str, Any]
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    score: float
    why_surfaced: ReportClaim
    what_changed: list[ReportClaim]
    what_this_could_mean: ReportClaim
    suggested_action: ActionValue
    action_label: str
    action_reason: str
    questions: list[ReportQuestion]


class PortfolioBriefBody(BaseModel):
    agent: Literal["PORTFOLIO ANALYST"]
    kind: Literal["portfolio_brief"]
    date: str
    period: Period
    five_things: list[ReportClaim] = Field(max_length=5)
    needs_attention: list[AttentionCardBody]
    technical_changes: list[ReportClaim]
    competitive_moves: list[ReportClaim]
    talent_changes: list[ReportClaim]
    noise_filtered: dict[str, Any]
    limitations: str


# ── API ──────────────────────────────────────────────────────────────────────


class CompanyBrief(BaseModel):
    id: str
    slug: str
    name: str
    website: str | None = None
    domain: str | None = None
    github_org: str | None = None
    is_demo: bool = False


class PositionOut(BaseModel):
    """Public investment context only. Unknown stays None (shown as UNKNOWN)."""

    model_config = ConfigDict(extra="forbid")
    portfolio_name: str
    status: str
    publicly_disclosed: bool
    source_url: str | None = None
    source_checked_at: str | None = None
    investment_stage: str | None = None
    investment_year: int | None = None
    sector: str | None = None
    thesis_tags: list[str] = Field(default_factory=list)


class EventSource(BaseModel):
    url: str | None = None
    title: str | None = None
    source_type: str | None = None
    family: str | None = None
    publisher: str | None = None
    observed_at: str | None = None


class ImpactOut(BaseModel):
    company: CompanyBrief
    relation: str
    impact_reason: str
    confidence: float
    materiality_for_company: Materiality


class AffectOut(BaseModel):
    company_slug: str
    company_name: str
    relation: str  # SELF | COMPETITOR | ADJACENT | INFRASTRUCTURE | INCUMBENT | OPEN_SOURCE
    comp_type: str | None = None  # the configured comp type when relation is not SELF
    impact_reason: str
    materiality_for_company: Materiality


class EventOut(BaseModel):
    id: str
    affects: list[AffectOut] = Field(default_factory=list)  # portfolio companies this event affects
    company: CompanyBrief
    event_type: str
    event_subtype: str
    title: str
    description: str | None = None
    event_at: str
    detected_at: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    sources: list[EventSource] = Field(default_factory=list)
    confidence: float
    epistemic: str
    materiality: Materiality
    materiality_rule: str
    materiality_reason: str
    novelty: str | None = None
    impact_areas: list[str] = Field(default_factory=list)
    why_it_matters: str
    why_it_matters_epistemic: Literal["INFERENCE"] = "INFERENCE"
    suggested_review: ActionValue
    status: str
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    raw_count: int
    families: list[str] = Field(default_factory=list)
    relation: str | None = None  # from the requesting company's point of view (lists filtered by company)
    performance_claim: bool = False
    is_demo: bool = False


class QuestionOut(BaseModel):
    id: str
    company: CompanyBrief | None = None
    question: str
    reason: str
    source: str | None = None
    status: QuestionStatus
    created_from_event_id: str | None = None
    created_at: str | None = None


class DecisionOut(BaseModel):
    id: str
    kind: str
    actor: str
    note: str
    event_id: str | None = None
    question_id: str | None = None
    created_at: str | None = None


class DecisionTraceOut(BaseModel):
    event: list[DecisionOut] = Field(default_factory=list)


class AttentionCardOut(BaseModel):
    company: CompanyBrief
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    score: float
    why_surfaced: str
    what_changed: list[dict[str, Any]]
    material_events: list[str]
    what_this_could_mean: str
    what_this_could_mean_epistemic: Literal["INFERENCE"] = "INFERENCE"
    questions: list[dict[str, Any]]
    suggested_action: ActionValue
    action_reason: str
    sources: list[dict[str, Any]]
    counts: dict[str, int]


class FunnelOut(BaseModel):
    raw_changes: int = 0
    candidates: int = 0
    after_dedup: int = 0
    material: int = 0
    watch: int = 0
    noise: int = 0
    companies_surfaced: int = 0
    day: str | None = None
    window: dict[str, str] | None = None
    available: bool = False
    reason: str | None = None


class MetricWindowOut(BaseModel):
    metric: str
    current: float | None = None
    prior: float | None = None
    window: str
    comparable: bool
    reason: str | None = None
    delta: float | None = None
    proxy_label: str | None = None
    source_url: str | None = None
    confidence: float | None = None


class MoverOut(BaseModel):
    company: CompanyBrief
    metric: MetricWindowOut


class ProviderHealthOut(BaseModel):
    key: str
    label: str
    state: str
    reason: str | None = None
    last_success_at: str | None = None


class MonitoringOut(BaseModel):
    tracked: int
    portfolio_companies: int
    comps: int
    fresh: int
    stale: int
    never_observed: int
    stale_after_days: int
    providers: list[ProviderHealthOut]
    events_updated_at: str | None = None
    data_as_of: str | None = None


class PulseOut(BaseModel):
    day: str | None = None
    counts: dict[str, int]
    funnel: FunnelOut
    needs_attention: list[AttentionCardOut]
    material_events: list[EventOut]
    movers: list[MoverOut]
    since_last_review: list[dict[str, Any]]
    monitoring: MonitoringOut
    brief_id: str | None = None


class CompOut(BaseModel):
    company: CompanyBrief
    comp_type: str
    why_comparable: str
    source_url: str
    confidence: str
    epistemic: str
    recent_events: list[EventOut] = Field(default_factory=list)
    technical_momentum: float | None = None
    anti_score: float | None = None


class ReviewInfo(BaseModel):
    id: str
    decision: str
    note: str
    reviewer: str
    created_at: str | None = None


class PortfolioCompanyOut(BaseModel):
    company: CompanyBrief
    position: PositionOut
    events: list[EventOut]
    competitor_events: list[EventOut]
    noise_count: int
    metrics: list[MetricWindowOut]
    proxies: list[dict[str, Any]]
    comps: list[CompOut]
    questions: list[QuestionOut]
    last_review: ReviewInfo | None = None
    next_review: str | None = None
    decision_trace: list[DecisionOut]
    data_age: dict[str, Any] | None = None
    # USAspending for this company: {state: queried | configured | not_applicable, recipient, last_queried_at}
    usaspending: dict[str, Any] | None = None
    report_id: str | None = None
    diligence: dict[str, Any]
    needs_attention: AttentionCardOut | None = None


class PortfolioIndexItem(BaseModel):
    company: CompanyBrief
    position: PositionOut
    material: int
    watch: int
    noise: int
    open_questions: int
    priority: str | None = None


class EventListOut(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[EventOut]


class EventDetailOut(BaseModel):
    event: EventOut
    impacts: list[ImpactOut]
    questions: list[QuestionOut]
    decision_trace: list[DecisionOut]
    suggested_action: ActionValue
    diligence: dict[str, Any] | None = None
    related_comps: list[dict[str, Any]] = Field(default_factory=list)


class CompsMapOut(BaseModel):
    company: CompanyBrief
    direct: list[CompOut]
    emerging: list[CompOut]
    adjacent: list[CompOut]


class BriefOut(BaseModel):
    id: str
    date: str
    title: str
    body: dict[str, Any]
    markdown: str
    sources: list[dict[str, Any]]
    generator: str
    created_at: str | None = None


class BriefArchiveItem(BaseModel):
    id: str
    date: str
    title: str


class PortfolioReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: PortfolioDecisionValue
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="analyst", min_length=1, max_length=120)
    event_id: str | None = Field(default=None, max_length=64)  # review one event instead of the company


class QuestionStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: QuestionStatus
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="analyst", min_length=1, max_length=120)
