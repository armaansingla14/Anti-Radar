"""System status / observability."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import SystemStatus
from radar.services.status import system_status

router = APIRouter(tags=["status"])


@router.get("/status", response_model=SystemStatus)
def status(session: Annotated[Session, Depends(get_session)]) -> SystemStatus:
    return system_status(session)
