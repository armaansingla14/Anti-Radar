"""Portfolio Pulse: what changed after we cared, and what deserves attention now.

Reads are open. ``POST /portfolio/{company}/review`` and ``POST /portfolio/questions/{id}`` are human decisions:
behind the admin gate (``radar.api.auth``), rate-limited, and refused with ``403 snapshot_read_only`` on the
verified-snapshot backend (``DEMO_SNAPSHOT``). The diligence handoff reuses ``POST /diligence/{company}/run``.
No response model carries ``private_fields``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from radar import clock
from radar.api.ratelimit import rate_limit
from radar.db import get_session
from radar.diligence import workspace as ws
from radar.models import Company, MaterialEvent
from radar.portfolio import views
from radar.portfolio.decisions import log_human
from radar.portfolio.questions import set_status
from radar.schemas.portfolio import (
    BriefArchiveItem,
    BriefOut,
    CompsMapOut,
    EventDetailOut,
    EventListOut,
    PortfolioCompanyOut,
    PortfolioIndexItem,
    PortfolioReviewIn,
    PulseOut,
    QuestionOut,
    QuestionStatusIn,
    ReviewInfo,
)
from radar.settings import get_settings

router = APIRouter(tags=["portfolio"])
SessionDep = Annotated[Session, Depends(get_session)]
CompanyRef = Annotated[str, Path(min_length=1, max_length=200)]
EVENT_TYPE_RE = r"^(PRODUCT|TECHNICAL|TALENT|COMMERCIAL|FINANCING|COMPETITIVE|REGULATORY|RISK|STRATEGIC)$"
RELATION_RE = r"^(SELF|COMPETITOR|ADJACENT|INFRASTRUCTURE|INCUMBENT|OPEN_SOURCE)$"


def _refuse_on_snapshot() -> None:
    if get_settings().demo_snapshot:
        raise HTTPException(403, detail="snapshot_read_only")


def _company(session: Session, ref: str):
    found = views.resolve_portfolio_company(session, ref)
    if found is None:
        raise HTTPException(404, detail="portfolio_company_not_found")
    return found


def _any_company(session: Session, ref: str) -> Company:
    try:
        c = session.get(Company, uuid.UUID(ref))
    except ValueError:
        from sqlalchemy import select

        c = session.scalar(select(Company).where(Company.slug == ref))
    if c is None:
        raise HTTPException(404, detail="company_not_found")
    return c


def _uuid(v: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(404, detail=f"{what}_not_found") from None


@router.get("/portfolio", response_model=PulseOut)
def portfolio_pulse(session: SessionDep) -> dict:
    return views.pulse(session)


@router.get("/portfolio/companies", response_model=list[PortfolioIndexItem])
def portfolio_companies(session: SessionDep) -> list[dict]:
    return views.index(session)


@router.get("/portfolio/events", response_model=EventListOut)
def portfolio_events(
    session: SessionDep,
    type: Annotated[str | None, Query(pattern=EVENT_TYPE_RE)] = None,  # noqa: A002 — public query name
    materiality: Annotated[str | None, Query(pattern=r"^(MATERIAL|WATCH|NOISE)$")] = None,
    company: Annotated[str | None, Query(max_length=200)] = None,
    relation: Annotated[str | None, Query(pattern=RELATION_RE)] = None,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    c = _any_company(session, company) if company else None
    return views.list_events(session, event_type=type, materiality=materiality, company=c, relation=relation,
                             offset=offset, limit=limit)


@router.get("/portfolio/events/{event_id}", response_model=EventDetailOut)
def portfolio_event(session: SessionDep, event_id: Annotated[str, Path(max_length=64)]) -> dict:
    ev = session.get(MaterialEvent, _uuid(event_id, "event"))
    if ev is None:
        raise HTTPException(404, detail="event_not_found")
    return views.event_detail(session, ev)


@router.get("/portfolio/comps", response_model=list[CompsMapOut])
def portfolio_comps(session: SessionDep) -> list[dict]:
    return [views.comps_map(session, c) for c, _ in views._portfolio(session)]


@router.get("/portfolio/comps/{company_ref}", response_model=CompsMapOut)
def portfolio_comps_company(session: SessionDep, company_ref: CompanyRef) -> dict:
    c, _ = _company(session, company_ref)
    return views.comps_map(session, c)


@router.get("/portfolio/brief", response_model=BriefOut)
def portfolio_brief(session: SessionDep) -> dict:
    r = views.brief_for(session)
    if r is None:
        raise HTTPException(404, detail="no_portfolio_brief")
    return views.brief_out(r)


@router.get("/portfolio/brief/archive", response_model=list[BriefArchiveItem])
def portfolio_brief_archive(session: SessionDep, limit: Annotated[int, Query(ge=1, le=365)] = 60) -> list[dict]:
    return views.brief_archive(session, limit)


@router.get("/portfolio/brief/day/{day}", response_model=BriefOut)
def portfolio_brief_day(session: SessionDep, day: Annotated[str, Path(pattern=r"^\d{4}-\d{2}-\d{2}$")]) -> dict:
    r = views.brief_for(session, day)
    if r is None:
        raise HTTPException(404, detail="no_portfolio_brief")
    return views.brief_out(r)


@router.get("/portfolio/{company_ref}", response_model=PortfolioCompanyOut)
def portfolio_company(session: SessionDep, company_ref: CompanyRef) -> dict:
    c, p = _company(session, company_ref)
    return views.company_view(session, c, p)


@router.post("/portfolio/{company_ref}/review", response_model=ReviewInfo, status_code=201,
             dependencies=[Depends(rate_limit("portfolio_review", 30))])
def portfolio_review(session: SessionDep, company_ref: CompanyRef, req: PortfolioReviewIn) -> dict:
    """A human review of the company (or of one of its events): ReviewDecision + a 'reviewed' log line."""
    _refuse_on_snapshot()
    c, _ = _company(session, company_ref)
    ev = None
    if req.event_id:
        ev = session.get(MaterialEvent, _uuid(req.event_id, "event"))
        if ev is None:
            raise HTTPException(404, detail="event_not_found")
    try:
        r = ws.create_review(session, target_type="material_event" if ev else "portfolio_company",
                             target_id=str(ev.id if ev else c.id), decision=req.decision, note=req.note,
                             reviewer=req.reviewer)
    except LookupError as e:
        raise HTTPException(404, detail=str(e)) from None
    except ws.ReviewError as e:
        raise HTTPException(422, detail=str(e)) from None
    if ev is not None and req.decision in ("MARK_REVIEWED", "NO_ACTION"):
        ev.status = "REVIEWED"
        ev.reviewed_at = clock.wall_now()
        ev.reviewed_by = r.reviewer
    kind = "monitoring_changed" if req.decision == "MONITOR" else "reviewed"
    log_human(session, company_id=c.id, event_id=ev.id if ev else None, kind=kind, actor=r.reviewer,
              note=f"{req.decision}" + (f": {req.note}" if req.note else ""))
    return {"id": str(r.id), "decision": r.decision, "note": r.note, "reviewer": r.reviewer,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.post("/portfolio/questions/{question_id}", response_model=QuestionOut,
             dependencies=[Depends(rate_limit("portfolio_question", 30))])
def portfolio_question(session: SessionDep, question_id: Annotated[str, Path(max_length=64)],
                       req: QuestionStatusIn) -> dict:
    _refuse_on_snapshot()
    try:
        q = set_status(session, _uuid(question_id, "question"), req.status, reviewer=req.reviewer, note=req.note)
    except LookupError:
        raise HTTPException(404, detail="question_not_found") from None
    return views.questions_out(session, [q], with_company=True)[0]
