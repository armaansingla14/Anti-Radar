"""Watchlist endpoints (single default analyst)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import OkOut, WatchItemOut, WatchlistOut, WatchRequest
from radar.services import watchlist as svc

router = APIRouter(tags=["watchlist"])
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/watchlist", response_model=WatchlistOut)
def get_watchlist(session: SessionDep) -> WatchlistOut:
    return svc.get_watchlist(session)


@router.post("/watchlist", response_model=WatchItemOut)
def watch(req: WatchRequest, session: SessionDep) -> WatchItemOut:
    try:
        return svc.add_item(session, req)
    except svc.WatchTargetNotFound as e:
        raise HTTPException(404, detail=str(e)) from None


@router.post("/watchlist/seen", response_model=OkOut)
def mark_seen(session: SessionDep) -> OkOut:
    n = svc.mark_seen(session)
    return OkOut(ok=True, detail=f"{n} items marked seen")


@router.delete("/watchlist/{ref}", response_model=OkOut)
def unwatch(ref: str, session: SessionDep) -> OkOut:
    """`ref` is the watchlist item id or the watched entity's id."""
    if not svc.remove_item(session, ref):
        raise HTTPException(404, detail="watchlist item not found")
    return OkOut(ok=True)
