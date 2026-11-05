"""Agent evaluation control plane: per-agent metrics, evaluations, the failure ledger and regression cases.

GET endpoints only read stored rows: they never run the evaluator or a model. Writes (create / review a
failure) are refused with 403 ``snapshot_read_only`` on a backend serving the verified snapshot, and by the
admin gate when ADMIN_TOKEN is set.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.evals import failures as F
from radar.evals.cases import CaseError, load_cases
from radar.evals.compare import configured_models, recommend_models
from radar.evals.metrics import METHODOLOGY, agent_metrics, llm_telemetry
from radar.evals.runner import latest_results
from radar.evals.taxonomy import CATEGORY_DEFINITIONS, EVALUATOR_VERSION, resolve_agent_alias
from radar.models import AgentFailure, AgentRunEvaluation, Company, EvalCaseResult, Person, ResearchReport
from radar.schemas.evals import (
    AgentsOverview,
    CaseOut,
    CaseResultOut,
    EvaluationOut,
    FailureCreate,
    FailureOut,
    FailureReview,
    ModelComparison,
    RegressionSummary,
    ReviewOut,
)
from radar.settings import get_settings

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(tags=["agents"])


def _refuse_on_snapshot() -> None:
    if get_settings().demo_snapshot:
        raise HTTPException(403, detail="snapshot_read_only")


def _agent_filter(agent: str | None) -> str | None:
    if not agent:
        return None
    name = resolve_agent_alias(agent)
    if name is None:
        raise HTTPException(422, detail=f"unknown agent {agent!r}")
    return name


def _regression(session: Session) -> RegressionSummary:
    try:
        cases = load_cases()
    except CaseError as e:
        return RegressionSummary(cases_total=0, passing=0, failing=0, never_run=0, error=str(e))
    latest = latest_results(session)
    runs = [latest[c.id] for c in cases if c.id in latest]
    return RegressionSummary(cases_total=len(cases), passing=sum(1 for r in runs if r.passed),
                             failing=sum(1 for r in runs if not r.passed), never_run=len(cases) - len(runs),
                             last_run_at=max((r.run_at for r in runs), default=None))


@router.get("/agents", response_model=AgentsOverview)
def overview(session: SessionDep) -> AgentsOverview:
    """Stored evaluation state: agents (registry order), LLM telemetry, methodology, taxonomy, regression
    summary and model comparison. Every metric is {value, n, reason}; null means not computable."""
    return AgentsOverview(
        agents=agent_metrics(session), llm_telemetry=llm_telemetry(session), methodology=dict(METHODOLOGY),
        taxonomy=[{"category": str(k), "definition": v} for k, v in CATEGORY_DEFINITIONS.items()],
        regression=_regression(session),
        model_comparison=ModelComparison(
            configured_models=[{"provider": m.provider, "model": m.model, "tiers": list(m.tiers)}
                               for m in configured_models()],
            recommendations=recommend_models(session)),
        evaluator_version=EVALUATOR_VERSION,
    )


def _evaluation_out(session: Session, ev: AgentRunEvaluation, report: ResearchReport | None) -> EvaluationOut:
    slug = None
    if report is not None and report.entity_id is not None:
        model = Company if report.entity_type == "company" else Person if report.entity_type == "person" else None
        ent = session.get(model, report.entity_id) if model else None
        slug = ent.slug if ent is not None else None
    return EvaluationOut(
        id=str(ev.id), report_id=str(ev.report_id) if ev.report_id else None,
        report_title=report.title if report else None, report_kind=report.kind if report else None,
        entity_type=report.entity_type if report else None,
        entity_id=str(report.entity_id) if report is not None and report.entity_id else None, entity_slug=slug,
        report_url=f"/reports/{ev.report_id}" if ev.report_id else None, agent=ev.agent, model=ev.model,
        generator=ev.generator, evaluator_version=ev.evaluator_version, evaluated_at=ev.evaluated_at,
        task_success=ev.task_success, structured_output_valid=ev.structured_output_valid,
        claims_total=ev.claims_total, fact_claims=ev.fact_claims, citations_total=ev.citations_total,
        invalid_citations=ev.invalid_citations, unsupported_claims=ev.unsupported_claims,
        unsupported_claim_rate=ev.unsupported_claim_rate, citation_precision=ev.citation_precision,
        source_coverage=ev.source_coverage, sources_registered=ev.sources_registered, sources_used=ev.sources_used,
        latency_ms=ev.latency_ms, cost_usd=float(ev.cost_usd or 0.0), fallback=ev.fallback, error=ev.error,
        flags=[f for f in ev.flags or [] if isinstance(f, dict)], review_status=ev.review_status,
        notes=ev.notes or "",
    )


@router.get("/agents/evaluations", response_model=list[EvaluationOut])
def evaluations(
    session: SessionDep,
    agent: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[EvaluationOut]:
    """Evaluations at the current evaluator version, newest first."""
    q = (select(AgentRunEvaluation, ResearchReport)
         .outerjoin(ResearchReport, ResearchReport.id == AgentRunEvaluation.report_id)
         .where(AgentRunEvaluation.evaluator_version == EVALUATOR_VERSION))
    if name := _agent_filter(agent):
        q = q.where(AgentRunEvaluation.agent == name)
    rows = session.execute(q.order_by(AgentRunEvaluation.evaluated_at.desc(), AgentRunEvaluation.report_id)
                           .limit(limit)).all()
    return [_evaluation_out(session, ev, rep) for ev, rep in rows]


def _failure_out(session: Session, f: AgentFailure) -> FailureOut:
    return FailureOut(
        number=f.number, display=F.display_number(f.number), agent=f.agent,
        report_id=str(f.report_id) if f.report_id else None, entity_ref=f.entity_ref, claim_text=f.claim_text,
        evidence_summary=f.evidence_summary or "", category=f.category, severity=f.severity,
        resolution=f.resolution or "", regression_case=f.regression_case, regression_test=f.regression_test,
        status=f.status, origin=f.origin, source_ref=f.source_ref, detected_at=f.detected_at,
        created_at=f.created_at, updated_at=f.updated_at,
        reviews=[ReviewOut(decision=r.decision, note=r.note, reviewer=r.reviewer, created_at=r.created_at)
                 for r in F.review_history(session, f.number)],
    )


@router.get("/agents/failures", response_model=list[FailureOut])
def failures(
    session: SessionDep,
    status: Annotated[str | None, Query(pattern=r"^(OPEN|CONFIRMED|FIXED|DISMISSED)$")] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    agent: Annotated[str | None, Query(max_length=40)] = None,
) -> list[FailureOut]:
    if category and category not in CATEGORY_DEFINITIONS:
        raise HTTPException(422, detail=f"unknown category {category!r}")
    rows = F.list_failures(session, status=status, category=category, agent=_agent_filter(agent))
    return [_failure_out(session, f) for f in rows]


@router.get("/agents/failures/{number}", response_model=FailureOut)
def failure(session: SessionDep, number: Annotated[int, Path(ge=1, le=10_000_000)]) -> FailureOut:
    f = F.get_failure(session, number)
    if f is None:
        raise HTTPException(404, detail="Failure not found")
    return _failure_out(session, f)


@router.post("/agents/failures", response_model=FailureOut, status_code=201)
def create_failure(req: FailureCreate, session: SessionDep) -> FailureOut:
    _refuse_on_snapshot()
    report_id = None
    if req.report_id:
        try:
            report_id = uuid.UUID(req.report_id)
        except ValueError:
            raise HTTPException(422, detail="report_id must be a UUID") from None
        if session.get(ResearchReport, report_id) is None:
            raise HTTPException(404, detail="Report not found")
    f = F.create_failure(session, agent=req.agent, claim_text=req.claim_text, category=req.category,
                         severity=req.severity, evidence_summary=req.evidence_summary, report_id=report_id,
                         entity_ref=req.entity_ref, regression_case=req.regression_case,
                         regression_test=req.regression_test, origin="reviewer", status="OPEN")
    session.commit()
    return _failure_out(session, f)


@router.post("/agents/failures/{number}/review", response_model=FailureOut)
def review(req: FailureReview, session: SessionDep, number: Annotated[int, Path(ge=1, le=10_000_000)]
           ) -> FailureOut:
    _refuse_on_snapshot()
    try:
        f = F.review_failure(session, number, status=req.status, note=req.note)
    except LookupError:
        raise HTTPException(404, detail="Failure not found") from None
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from None
    session.commit()
    return _failure_out(session, f)


@router.get("/agents/cases", response_model=list[CaseOut])
def cases(session: SessionDep) -> list[CaseOut]:
    """Regression case definitions (sorted by id) with the latest template-baseline result of each."""
    try:
        defs = load_cases()
    except CaseError as e:
        raise HTTPException(500, detail=f"case files invalid: {e}") from None
    latest = latest_results(session)
    out = []
    for c in defs:
        r: EvalCaseResult | None = latest.get(c.id)
        out.append(CaseOut(
            id=c.id, title=c.title, agent=c.agent, task=c.task, failure_category=c.failure_category,
            regression_for=c.regression_for, min_citations=c.min_citations,
            expected_epistemic=c.expected_epistemic, expected=c.expected,
            latest=None if r is None else CaseResultOut(
                passed=r.passed, provider=r.provider, model=r.model, run_at=r.run_at,
                failures=list((r.metrics or {}).get("failures") or []), error=r.error,
                metrics={k: v for k, v in (r.metrics or {}).items() if k != "failures"}),
        ))
    return out
