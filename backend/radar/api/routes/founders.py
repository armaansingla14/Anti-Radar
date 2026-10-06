"""Founder list, detail and founder score (Why drawer)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.models import Person
from radar.schemas.api import AntiScore, PersonCard, PersonDetail
from radar.services import entities
from radar.services.entities import InvalidParam
from radar.settings import get_settings

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["entities"])

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$"


@router.get("/founders", response_model=list[PersonCard])
def list_founders(
    session: SessionDep,
    response: Response,
    q: Annotated[str | None, Query(max_length=200, description="Name / bio / handle / company substring")] = None,
    theme: Annotated[str | None, Query(max_length=120)] = None,
    no_company: Annotated[bool, Query(description="Formation targets: no company on record, no organization named in bio/headline, "
                                            "and a formation signal in the last 120 days")] = False,
    sort: Literal["score", "delta", "recent", "followers"] = "score",
    include_demo: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[PersonCard]:
    include_demo = get_settings().demo_mode if include_demo is None else include_demo
    try:
        items, total = entities.list_founders(session, q=q, theme=theme, no_company=no_company, sort=sort,
                                      include_demo=include_demo, limit=limit, offset=offset, with_total=True)
        response.headers["X-Total-Count"] = str(total)
        return items
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None


@router.get("/founders/{id_or_slug}", response_model=PersonDetail)
def get_founder(
    session: SessionDep,
    id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> PersonDetail:
    out = entities.person_detail(session, id_or_slug)
    if out is None:
        raise HTTPException(404, detail="Founder not found")
    return out


@router.get("/founders/{id_or_slug}/score", response_model=AntiScore)
def get_founder_score(
    session: SessionDep,
    id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> AntiScore:
    if entities.resolve(session, Person, id_or_slug) is None:
        raise HTTPException(404, detail="Founder not found")
    out = entities.entity_score(session, Person, id_or_slug)
    if out is None:
        raise HTTPException(404, detail="No founder score computed for this person yet")
    return out
