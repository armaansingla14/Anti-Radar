"""GET /api/brief (JSON, composed live) and /api/brief/markdown (download); brief history:
GET /api/brief/archive (stored briefs, newest first) and GET /api/brief/day/{YYYY-MM-DD|today}
(the brief stored for that day; 404 ``no_brief_for_date`` when none was stored — never generated
retroactively)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from radar import clock
from radar.db import get_session
from radar.schemas.api import MorningBrief
from radar.schemas.feeds_v2 import BriefArchiveEntry
from radar.services.brief import brief_archive, morning_brief, stored_brief

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["feeds"])


@router.get("/brief", response_model=MorningBrief)
def get_brief(session: SessionDep, include_demo: Annotated[bool | None, Query()] = None) -> MorningBrief:
    return morning_brief(session, include_demo=include_demo)


@router.get("/brief/markdown", response_class=Response,
            responses={200: {"content": {"text/markdown": {}}}})
def get_brief_markdown(session: SessionDep, include_demo: Annotated[bool | None, Query()] = None) -> Response:
    b = morning_brief(session, include_demo=include_demo)
    return Response(content=b.markdown, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="anti-radar-brief-{b.date.isoformat()}.md"'})


@router.get("/brief/archive", response_model=list[BriefArchiveEntry])
def get_brief_archive(session: SessionDep,
                      limit: Annotated[int, Query(ge=1, le=366)] = 120) -> list[BriefArchiveEntry]:
    return brief_archive(session, limit=limit)


@router.get("/brief/day/{day}", response_model=MorningBrief,
            responses={404: {"description": "No brief was stored for this date",
                             "content": {"application/json": {"example": {"detail": "no_brief_for_date"}}}}})
def get_brief_day(session: SessionDep,
                  day: Annotated[str, Path(max_length=10, pattern=r"^(today|\d{4}-\d{2}-\d{2})$")]) -> MorningBrief:
    if day == "today":
        d = clock.today()
    else:
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, detail="invalid_date") from None
    b = stored_brief(session, d)
    if b is None:
        raise HTTPException(404, detail="no_brief_for_date")
    return b
