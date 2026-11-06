"""Technical Diligence workspaces and human review decisions.

Reads are open. ``POST /diligence/{company}/run`` and ``POST /reviews`` are writes: behind the admin gate
(``radar.api.auth``), rate-limited, and refused with ``403 snapshot_read_only`` on the verified-snapshot backend
(``DEMO_SNAPSHOT``), which must stay exactly as captured. A run is synchronous within its time budget
(≤ 180 s); the "running" row is committed first so ``/status`` reports IN_PROGRESS meanwhile.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from radar.api.ratelimit import rate_limit
from radar.db import get_session, session_scope
from radar.diligence import workspace as ws
from radar.diligence.run import diligence_status, resolve_company, run_diligence
from radar.schemas.diligence import (
    DiligenceClaimOut,
    DiligenceIndexOut,
    DiligenceRunRequest,
    DiligenceStatusOut,
    DiligenceWorkspaceOut,
    ReviewIn,
    ReviewOut,
)
from radar.settings import get_settings

log = logging.getLogger("radar.api.diligence")
router = APIRouter(tags=["diligence"])

SessionDep = Annotated[Session, Depends(get_session)]
CompanyRef = Annotated[str, Path(min_length=1, max_length=200)]


def _company(session: Session, ref: str):
    c = resolve_company(session, ref)
    if c is None:
        raise HTTPException(404, detail="company_not_found")
    return c


def _refuse_on_snapshot() -> None:
    if get_settings().demo_snapshot:
        raise HTTPException(403, detail="snapshot_read_only")


def _log_portfolio_handoff(session: Session, company_id) -> None:
    """A person asked for diligence on a portfolio company: the V4 decision log records the handoff."""
    from sqlalchemy import select

    from radar.models import PortfolioPosition
    from radar.portfolio.decisions import log_human

    if session.scalar(select(PortfolioPosition.id).where(PortfolioPosition.company_id == company_id).limit(1)):
        log_human(session, company_id=company_id, kind="diligence_requested", actor="analyst",
                  note="RUN DILIGENCE requested (V3 diligence workspace)")


@router.get("/diligence", response_model=DiligenceIndexOut)
def diligence_index(session: SessionDep, include_demo: bool = False) -> dict:
    return ws.index(session, include_demo=include_demo)


@router.get("/diligence/{company_ref}", response_model=DiligenceWorkspaceOut)
def diligence_workspace(session: SessionDep, company_ref: CompanyRef) -> dict:
    return ws.workspace(session, _company(session, company_ref))


@router.get("/diligence/{company_ref}/status", response_model=DiligenceStatusOut)
def diligence_run_status(session: SessionDep, company_ref: CompanyRef) -> dict:
    return diligence_status(session, _company(session, company_ref).id)


@router.get("/diligence/{company_ref}/claims/{ordinal}", response_model=DiligenceClaimOut)
def diligence_claim(session: SessionDep, company_ref: CompanyRef,
                    ordinal: Annotated[int, Path(ge=1, le=1000)]) -> dict:
    out = ws.claim_detail(session, _company(session, company_ref), ordinal)
    if out is None:
        raise HTTPException(404, detail="claim_not_found")
    return out


@router.post("/diligence/{company_ref}/run", response_model=DiligenceStatusOut,
             dependencies=[Depends(rate_limit("diligence_run", 3))])
def diligence_run(company_ref: CompanyRef, req: Annotated[DiligenceRunRequest | None, Body()] = None) -> dict:
    """Live collection + analysis, synchronously within the time budget; returns the resulting status."""
    _refuse_on_snapshot()
    with session_scope() as s:
        company = _company(s, company_ref)
        if diligence_status(s, company.id)["status"] == "IN_PROGRESS":
            raise HTTPException(409, detail="diligence_in_progress")
        company_id = company.id
        _log_portfolio_handoff(s, company_id)
    budget = (req or DiligenceRunRequest()).budget_s
    with session_scope() as s:
        run = run_diligence(s, company_id, live=True, budget_s=budget, commit_progress=True)
        if run.status == "error":
            log.warning("diligence run %s failed: %s", run.id, run.error)
    with session_scope() as s:
        return diligence_status(s, company_id)


@router.post("/reviews", response_model=ReviewOut, status_code=201,
             dependencies=[Depends(rate_limit("reviews", 30))])
def create_review(session: SessionDep, req: ReviewIn) -> dict:
    _refuse_on_snapshot()
    try:
        r = ws.create_review(session, target_type=req.target_type, target_id=req.target_id, decision=req.decision,
                             note=req.note, reviewer=req.reviewer)
    except LookupError as e:
        raise HTTPException(404, detail=str(e)) from None
    except ws.ReviewError as e:
        raise HTTPException(422, detail=str(e)) from None
    return ws.review_out(r)


@router.get("/reviews", response_model=list[ReviewOut])
def list_reviews(
    session: SessionDep,
    target_type: Annotated[str | None, Query(pattern=r"^(claim|underwriting|report|failure|portfolio_company|material_event)$")] = None,
    target_id: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    return ws.list_reviews(session, target_type=target_type, target_id=target_id, limit=limit)
