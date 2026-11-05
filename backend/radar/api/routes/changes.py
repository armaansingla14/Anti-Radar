"""SINCE LAST SNAPSHOT: what changed between two verified snapshot days (read-only)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.changes import ChangesReport, SnapshotPoints
from radar.services import changes

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["changes"])


@router.get("/changes", response_model=ChangesReport)
def changes_report(
    session: SessionDep,
    base: Annotated[date | None, Query(description="Earlier snapshot day (default: previous snapshot)")] = None,
    current: Annotated[date | None, Query(description="Later day (default: latest snapshot)")] = None,
    include_demo: Annotated[bool | None, Query()] = None,
) -> ChangesReport:
    try:
        return changes.compare(session, base, current, include_demo=include_demo)
    except changes.InvalidRange as e:
        raise HTTPException(422, detail=str(e)) from None


@router.get("/changes/points", response_model=SnapshotPoints)
def changes_points(session: SessionDep) -> SnapshotPoints:
    return changes.points_out(session)
