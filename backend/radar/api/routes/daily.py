"""GET /api/daily — what changed since yesterday (or since ?since / your last visit)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import DailyRadar
from radar.services import feed

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["feeds"])


@router.get("/daily", response_model=DailyRadar)
def get_daily(
    session: SessionDep,
    since: Annotated[datetime | None, Query(description="ISO timestamp; default: earlier of now-24h and last visit")] = None,
    include_demo: Annotated[bool | None, Query()] = None,
) -> DailyRadar:
    try:
        return feed.daily_radar(session, since=since, include_demo=include_demo)
    except ValueError as e:
        raise HTTPException(422, detail=str(e)) from None
