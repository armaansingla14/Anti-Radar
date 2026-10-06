"""GET /api/brief (JSON) and /api/brief/markdown (download)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import MorningBrief
from radar.services.brief import morning_brief

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
