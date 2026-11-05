"""API models for the agent evaluation control plane (``/api/agents*``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from radar.evals.taxonomy import CATEGORY_DEFINITIONS, FAILURE_SEVERITIES, resolve_agent_alias

FailureStatus = Literal["OPEN", "CONFIRMED", "FIXED", "DISMISSED"]


class MetricValue(BaseModel):
    """A metric is null with a reason whenever it cannot be computed; ``n`` is its denominator / sample size."""

    value: float | None = None
    n: int = 0
    reason: str | None = None


class AgentSummary(BaseModel):
    key: str
    name: str
    kinds: list[str]
    purposes: list[str]
    description: str
    runs: int
    metrics: dict[str, MetricValue]
    by_generator: dict[str, int] = Field(default_factory=dict)
    flags_by_category: dict[str, int] = Field(default_factory=dict)
    open_failures: int = 0


class LLMTelemetryRow(BaseModel):
    purpose: str
    agent: str
    calls: int
    live_calls: int
    ok_rate: MetricValue
    latency_p50_ms: MetricValue
    latency_p95_ms: MetricValue
    cost_usd: float
    cache_hit_rate: MetricValue
    models: list[str]


class RegressionSummary(BaseModel):
    cases_total: int
    passing: int
    failing: int
    never_run: int
    last_run_at: datetime | None = None
    error: str | None = None  # the case files could not be loaded


class ConfiguredModelOut(BaseModel):
    provider: str
    model: str
    tiers: list[str]


class ModelRef(BaseModel):
    provider: str
    model: str


class Recommendation(BaseModel):
    task: str
    recommendation: ModelRef | None = None
    reason: str


class ModelComparison(BaseModel):
    configured_models: list[ConfiguredModelOut]
    recommendations: list[Recommendation]


class TaxonomyEntry(BaseModel):
    category: str
    definition: str


class AgentsOverview(BaseModel):
    agents: list[AgentSummary]
    llm_telemetry: list[LLMTelemetryRow]
    methodology: dict[str, str]
    taxonomy: list[TaxonomyEntry]
    regression: RegressionSummary
    model_comparison: ModelComparison
    evaluator_version: str


class EvalFlag(BaseModel):
    rule: str
    category: str
    severity: str
    section: str | None = None
    claim_text: str | None = None
    citation: str | None = None
    detail: str = ""
    needs_human_review: bool = False


class EvaluationOut(BaseModel):
    id: str
    report_id: str | None = None
    report_title: str | None = None
    report_kind: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    entity_slug: str | None = None
    report_url: str | None = None
    agent: str
    model: str | None = None
    generator: str | None = None
    evaluator_version: str
    evaluated_at: datetime
    task_success: bool
    structured_output_valid: bool
    claims_total: int
    fact_claims: int
    citations_total: int
    invalid_citations: int
    unsupported_claims: int
    unsupported_claim_rate: float | None = None
    citation_precision: float | None = None
    source_coverage: float | None = None
    sources_registered: int
    sources_used: int
    latency_ms: float | None = None
    cost_usd: float
    fallback: bool
    error: bool
    flags: list[EvalFlag]
    review_status: str
    notes: str = ""


class ReviewOut(BaseModel):
    decision: str
    note: str
    reviewer: str
    created_at: datetime | None = None


class FailureOut(BaseModel):
    number: int
    display: str
    agent: str
    report_id: str | None = None
    entity_ref: str | None = None
    claim_text: str
    evidence_summary: str
    category: str
    severity: str
    resolution: str
    regression_case: str | None = None
    regression_test: str | None = None
    related_tests: list[str] = Field(default_factory=list)
    status: str
    origin: str
    source_ref: str | None = None
    detected_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    reviews: list[ReviewOut] = Field(default_factory=list)


class FailureCreate(BaseModel):
    agent: str = Field(min_length=1, max_length=40)
    claim_text: str = Field(min_length=1, max_length=4000)
    category: str
    severity: str
    evidence_summary: str = Field(default="", max_length=8000)
    report_id: str | None = Field(default=None, max_length=64)
    entity_ref: str | None = Field(default=None, max_length=220)
    regression_case: str | None = Field(default=None, max_length=80)
    regression_test: str | None = Field(default=None, max_length=300)

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORY_DEFINITIONS:
            raise ValueError(f"unknown category {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def _severity(cls, v: str) -> str:
        if v not in FAILURE_SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(FAILURE_SEVERITIES)}")
        return v

    @field_validator("agent")
    @classmethod
    def _agent(cls, v: str) -> str:
        name = resolve_agent_alias(v)
        if name is None:
            raise ValueError(f"unknown agent {v!r}")
        return name


class FailureReview(BaseModel):
    status: FailureStatus
    note: str = Field(min_length=1, max_length=4000)


class CaseResultOut(BaseModel):
    passed: bool
    provider: str
    model: str
    run_at: datetime | None = None
    failures: list[str] = Field(default_factory=list)
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)


class CaseOut(BaseModel):
    id: str
    title: str
    agent: str
    task: str
    failure_category: str
    regression_for: str | None = None
    min_citations: int | None = None
    expected_epistemic: dict[str, str] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    latest: CaseResultOut | None = None
