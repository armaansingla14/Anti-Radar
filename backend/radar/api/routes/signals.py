"""GET /api/signals — raw normalized signals with filters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import SignalOut
from radar.services import entities
from radar.services.entities import InvalidParam
from radar.settings import get_settings

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["entities"])


@router.get("/signals", response_model=list[SignalOut])
def list_signals(
    session: SessionDep,
    entity_id: Annotated[str | None, Query(max_length=64, description="Signals on or linked to this entity")] = None,
    entity_type: Annotated[str | None, Query(pattern=r"^(company|person|repository|paper|topic)$")] = None,
    signal_type: Annotated[str | None, Query(max_length=60, pattern=r"^[a-z0-9_]+$")] = None,
    source_type: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z0-9_]+$")] = None,
    since: Annotated[datetime | None, Query(description="ISO timestamp (event_at >= since)")] = None,
    include_demo: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SignalOut]:
    include_demo = get_settings().demo_mode if include_demo is None else include_demo
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    try:
        return entities.list_signals(session, entity_id=entity_id, entity_type=entity_type, signal_type=signal_type,
                                     source_type=source_type, since=since, include_demo=include_demo, limit=limit)
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None
