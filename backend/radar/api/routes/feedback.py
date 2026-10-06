"""Analyst feedback: nudges bounded preference weights; 'dismiss' hides the entity from feeds."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import FeedbackOut, FeedbackRequest
from radar.services import watchlist as svc

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackOut)
def feedback(req: FeedbackRequest, session: Annotated[Session, Depends(get_session)]) -> FeedbackOut:
    try:
        return FeedbackOut(**svc.submit_feedback(session, req))
    except svc.WatchTargetNotFound as e:
        raise HTTPException(404, detail=str(e)) from None
