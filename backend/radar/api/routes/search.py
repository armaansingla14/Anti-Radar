"""Natural-language search endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import SearchResponse
from radar.services.search import search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
def search_endpoint(
    session: Annotated[Session, Depends(get_session)],
    q: str = Query(..., min_length=1, max_length=500, description="Natural-language query"),
    limit: int = Query(25, ge=1, le=100),
) -> SearchResponse:
    return search(session, q.strip(), limit)
