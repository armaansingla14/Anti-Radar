"""API contract for Technical Diligence (workspaces, claims, status) and human reviews.

Nested report sections stay ``dict`` here: their exact shape is defined (and validated, ``extra="forbid"``) by
``radar.diligence.report.DiligenceReport``; repeating it would let the two drift.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DiligenceState = Literal["NOT_RUN", "IN_PROGRESS", "COMPLETE", "NEEDS_REVIEW"]
ReviewTarget = Literal["claim", "underwriting", "report", "failure"]
ReviewDecisionValue = Literal["ACCEPT", "QUESTION", "REJECT", "NEEDS_VERIFICATION"]


class DiligenceCompany(BaseModel):
    id: str
    slug: str
    name: str
    website: str | None = None
    domain: str | None = None
    github_org: str | None = None
    technical_domain: str | None = None
    thesis_tags: list[str] = Field(default_factory=list)
    is_demo: bool = False


class DiligenceStatusOut(BaseModel):
    status: DiligenceState
    run_id: str | None = None
    finished_at: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    pending_review: list[dict[str, str]] = Field(default_factory=list)
    started_at: str | None = None
    last_error: str | None = None


class DiligenceRunSummary(BaseModel):
    id: str
    status: str
    mode: str
    method_version: str
    started_at: str | None = None
    finished_at: str | None = None
    collected_at: str | None = None
    sources_analyzed: int = 0
    evidence_coverage: float | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    report_id: str | None = None
    error: str | None = None
    trace_summary: dict[str, Any] | None = None


class WorkspaceSummary(BaseModel):
    company: DiligenceCompany
    status: DiligenceState
    run: DiligenceRunSummary | None = None
    finished_at: str | None = None


class DiligenceCandidate(BaseModel):
    company: DiligenceCompany
    score: float | None = None


class DiligenceIndexOut(BaseModel):
    workspaces: list[WorkspaceSummary]
    candidates: list[DiligenceCandidate]


class ReviewOut(BaseModel):
    id: str
    target_type: str
    target_id: str
    decision: str
    note: str
    reviewer: str
    created_at: str | None = None


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: ReviewTarget
    target_id: str = Field(min_length=1, max_length=120)
    decision: ReviewDecisionValue
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="analyst", min_length=1, max_length=120)


class DiligenceClaimOut(BaseModel):
    id: str
    ordinal: int
    text: str
    claim_type: str
    source_id: str | None = None
    source_url: str | None = None
    source_quote: str
    observed_at: str | None = None
    epistemic: str
    testability: str
    status: str
    status_label: str
    status_reason: str
    rule: str
    confidence: str
    extracted_by: str
    company_evidence: list[dict[str, Any]]
    independent_evidence: list[dict[str, Any]]
    contradictory_evidence: list[dict[str, Any]]
    test_plan: dict[str, Any] | None = None
    reviews: list[ReviewOut] = Field(default_factory=list)
    run_id: str | None = None
    sources: list[dict[str, Any]] | None = None


class DiligenceWorkspaceOut(BaseModel):
    company: DiligenceCompany
    status: DiligenceStatusOut
    header: dict[str, Any]
    executive_summary: dict[str, Any] | None = None
    claims: list[DiligenceClaimOut]
    underwriting: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    risks: list[dict[str, Any]]
    unknowns: list[str]
    questions: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    sections: dict[str, Any]
    report_id: str | None = None
    report_reviews: list[ReviewOut] = Field(default_factory=list)
    run: DiligenceRunSummary | None = None
    definitions: dict[str, dict[str, str]]
    method_version: str


class DiligenceRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_s: int = Field(default=180, ge=10, le=180)
