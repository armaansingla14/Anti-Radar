"""Research reports (Anti Briefs, Anti Memos): JSON, list, and markdown download."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from radar.db import get_session
from radar.schemas.api import ReportOut
from radar.services import entities
from radar.services.entities import InvalidParam

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=list[ReportOut])
def list_reports(
    session: SessionDep,
    entity_id: Annotated[str | None, Query(max_length=64)] = None,
    kind: Annotated[str | None, Query(pattern=r"^(brief|memo|morning_brief|meeting_brief)$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ReportOut]:
    """Light listing: body / markdown / sources are omitted (fetch /reports/{id} for the full report)."""
    try:
        return entities.list_reports(session, entity_id=entity_id, kind=kind, limit=limit)
    except InvalidParam as e:
        raise HTTPException(422, detail=str(e)) from None


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(session: SessionDep, report_id: Annotated[str, Path(max_length=64)]) -> ReportOut:
    r = entities.get_report(session, report_id)
    if r is None:
        raise HTTPException(404, detail="Report not found")
    return entities.report_out(session, r)


@router.get("/reports/{report_id}/markdown", response_class=Response,
            responses={200: {"content": {"text/markdown": {}}}})
def get_report_markdown(session: SessionDep, report_id: Annotated[str, Path(max_length=64)]) -> Response:
    r = entities.get_report(session, report_id)
    if r is None:
        raise HTTPException(404, detail="Report not found")
    name = entities.report_filename(session, r)
    return Response(content=r.markdown or "", media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
