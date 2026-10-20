"""Formation radar: technical teams before the company announcement."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import AntiScore, FormationDetail, FormationFeed
from radar.services import formation
from radar.services.entities import InvalidParam

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["formation"])

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$"
_NOT_FOUND = "Formation candidate not found"


@router.get("/formation", response_model=FormationFeed)
def formation_feed(
    session: SessionDep,
    tier: Literal["high", "emerging", "early"] | None = None,
    theme: Annotated[str | None, Query(max_length=120)] = None,
    include_demo: Annotated[bool | None, Query()] = None,
) -> FormationFeed:
    try:
        return formation.formation_feed(session, tier=tier, theme=theme, include_demo=include_demo)
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None


@router.get("/formation/{id_or_slug}", response_model=FormationDetail)
def formation_detail(
    session: SessionDep, id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> FormationDetail:
    out = formation.formation_detail(session, id_or_slug)
    if out is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return out


@router.get("/formation/{id_or_slug}/score", response_model=AntiScore)
def formation_score(
    session: SessionDep, id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> AntiScore:
    out = formation.formation_score(session, id_or_slug)
    if out is None:
        raise HTTPException(404, detail=_NOT_FOUND)
    return out
