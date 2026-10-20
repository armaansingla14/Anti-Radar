"""Natural-language search endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from radar.api.ratelimit import rate_limit
from radar.db import get_session
from radar.schemas.api import SearchResponse
from radar.services.search import search

router = APIRouter(tags=["search"])


# Uncached queries rank every candidate (~0.5-1 s of CPU each): cap distinct-query bursts per client.
SEARCH_PER_MINUTE = 120


@router.get("/search", response_model=SearchResponse, dependencies=[Depends(rate_limit("search", SEARCH_PER_MINUTE))])
def search_endpoint(
    session: Annotated[Session, Depends(get_session)],
    q: str = Query(..., min_length=1, max_length=500, description="Natural-language query"),
    limit: int = Query(25, ge=1, le=100),
) -> SearchResponse:
    return search(session, q.strip(), limit)
