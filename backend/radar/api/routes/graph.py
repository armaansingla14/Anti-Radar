"""Founder graph."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import GraphOut
from radar.services.graph import GraphEntityNotFound, founder_graph

router = APIRouter(tags=["graph"])


@router.get("/graph", response_model=GraphOut)
def graph(
    session: Annotated[Session, Depends(get_session)],
    entity_id: str | None = Query(None, max_length=300, description="Entity id/slug, repo full_name, arXiv id or org:<name>"),
    depth: int = Query(1, ge=1, le=2),
) -> GraphOut:
    try:
        return founder_graph(session, entity_id.strip() if entity_id else None, depth)
    except GraphEntityNotFound as e:
        raise HTTPException(404, detail=str(e)) from None
