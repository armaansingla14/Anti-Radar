"""Company list, detail and score (Why drawer)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.models import Company
from radar.schemas.api import AntiScore, CompanyDetail, CompanySummary
from radar.services import entities
from radar.services.entities import InvalidParam
from radar.settings import get_settings

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["entities"])

_SLUG = r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$"


@router.get("/companies", response_model=list[CompanySummary])
def list_companies(
    session: SessionDep,
    response: Response,
    q: Annotated[str | None, Query(max_length=200, description="Name / description substring")] = None,
    theme: Annotated[str | None, Query(max_length=120)] = None,
    stage: Annotated[str | None, Query(max_length=40, pattern=r"^[a-z_]+$")] = None,
    kind: Literal["company", "stealth", "project", "lab"] | None = None,
    sort: Literal["score", "delta", "attention_gap", "recent", "stars"] = "score",
    include_demo: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[CompanySummary]:
    include_demo = get_settings().demo_synthetic if include_demo is None else include_demo
    try:
        items, total = entities.list_companies(session, q=q, theme=theme, stage=stage, kind=kind, sort=sort,
                                       include_demo=include_demo, limit=limit, offset=offset, with_total=True)
        response.headers["X-Total-Count"] = str(total)
        return items
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None


@router.get("/companies/{id_or_slug}", response_model=CompanyDetail)
def get_company(
    session: SessionDep,
    id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> CompanyDetail:
    out = entities.company_detail(session, id_or_slug)
    if out is None:
        raise HTTPException(404, detail="Company not found")
    return out


@router.get("/companies/{id_or_slug}/score", response_model=AntiScore)
def get_company_score(
    session: SessionDep,
    id_or_slug: Annotated[str, Path(max_length=200, pattern=_SLUG)]
) -> AntiScore:
    if entities.resolve(session, Company, id_or_slug) is None:
        raise HTTPException(404, detail="Company not found")
    out = entities.entity_score(session, Company, id_or_slug)
    if out is None:
        raise HTTPException(404, detail="No ANTI SCORE computed for this company yet")
    return out
