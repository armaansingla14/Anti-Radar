"""GET /api/radar — Morning Intelligence feed."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import RadarFeed
from radar.services import feed
from radar.services.entities import InvalidParam

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["feeds"])


@router.get("/radar", response_model=RadarFeed)
def get_radar(
    session: SessionDep,
    include_demo: Annotated[bool | None, Query(description="Include labeled fictional entities (default: DEMO_MODE)")] = None,
    new_only: Annotated[bool, Query(description="Unknown Yesterday: only entities first seen within 7 days")] = False,
    theme: Annotated[str | None, Query(max_length=120, description="Thesis theme or topic key, e.g. robotics")] = None,
) -> RadarFeed:
    try:
        out = feed.radar_feed(session, include_demo=include_demo, new_only=new_only, theme=theme)
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None
    feed.touch_last_visit(session)  # after the feed: the previous visit stays readable for /daily
    return out
