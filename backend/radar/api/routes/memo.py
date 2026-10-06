"""Anti Memo generation (and the shared ReportOut converter)."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from radar.api.ratelimit import rate_limit
from radar.db import get_session
from radar.models import Company, Person, ResearchReport, UserAction
from radar.schemas.api import MemoRequest, ReportOut

log = logging.getLogger("radar.api.memo")
router = APIRouter(tags=["research"])


def report_out(session: Session, r: ResearchReport) -> ReportOut:
    slug = None
    if r.entity_id is not None:
        model = Company if r.entity_type == "company" else Person if r.entity_type == "person" else None
        ent = session.get(model, r.entity_id) if model else None
        slug = ent.slug if ent else None
    return ReportOut(
        id=str(r.id), kind=r.kind, title=r.title, entity_type=r.entity_type,
        entity_id=str(r.entity_id) if r.entity_id else None, entity_slug=slug, body=r.body or {},
        markdown=r.markdown or "", sources=list(r.sources or []), model=r.model, generator=r.generator,
        status=r.status, cost_usd=float(r.cost_usd or 0.0), created_at=r.created_at, is_demo=r.is_demo,
    )


@router.post("/memo", response_model=ReportOut, dependencies=[Depends(rate_limit("memo", 10))])
def memo(req: MemoRequest, session: Annotated[Session, Depends(get_session)]) -> ReportOut:
    from radar.agent.memo import generate_memo
    from radar.agent.tools import ToolInputError, resolve_entity

    try:
        entity = resolve_entity(session, req.entity_type, req.entity_id)
    except ToolInputError as e:
        raise HTTPException(404, detail=str(e)) from None
    try:
        report = generate_memo(session, req.entity_type, entity.id, req.report_id)
    except ValueError as e:  # report_id is not a brief for this entity (or malformed)
        raise HTTPException(404, detail=f"brief not found for this entity: {req.report_id}") from e
    session.add(UserAction(action="memo", entity_type=req.entity_type, entity_id=entity.id,
                           meta={"report_id": str(report.id)}))
    session.flush()
    session.refresh(report)
    return report_out(session, report)
