"""GET /api/home — the home page in one call (count strip, WHAT CHANGED, top of each section)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.feeds_v2 import HomeFeed
from radar.services import feed, home

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["feeds"])


@router.get("/home", response_model=HomeFeed)
def get_home(
    session: SessionDep,
    new: Annotated[str | None, Query(max_length=10, description="today | week: only entities first detected "
                                                                "within 1 / 7 days of the data's as-of time")] = None,
    include_demo: Annotated[bool | None, Query(description="Include labeled fictional entities")] = None,
) -> HomeFeed:
    try:
        out = home.home_feed(session, new, include_demo=include_demo)
    except home.InvalidWindow as e:
        raise HTTPException(422, detail=str(e)) from None
    feed.touch_last_visit(session)  # the home page is a visit, like /radar (keeps /daily's "since" window)
    return out
