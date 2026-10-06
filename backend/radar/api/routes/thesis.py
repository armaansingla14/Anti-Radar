"""Thesis Map endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import ThesisMap
from radar.services.thesis_map import thesis_map

router = APIRouter(tags=["thesis"])


@router.get("/thesis", response_model=ThesisMap)
def get_thesis_map(session: Annotated[Session, Depends(get_session)]) -> ThesisMap:
    return thesis_map(session)
