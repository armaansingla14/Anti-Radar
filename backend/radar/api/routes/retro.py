"""Retrospective signal analysis (not a backtest)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import Retrospective
from radar.services.retro import retrospective

router = APIRouter(tags=["retrospective"])


@router.get("/retrospective", response_model=Retrospective)
def get_retrospective(session: Annotated[Session, Depends(get_session)]) -> Retrospective:
    return retrospective(session)
