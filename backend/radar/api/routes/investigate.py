"""Investigate: run RADAR ANALYST and stream its progress as Server-Sent Events.

The agent runs in a worker thread with its own session (``session_scope``) so the
event loop never blocks on DB/LLM I/O. ``on_event`` callbacks are bridged to the
async response through ``loop.call_soon_threadsafe`` onto an ``asyncio.Queue``.
The ``done`` event is held back until the worker's transaction has committed, so
a client that immediately fetches ``report_id`` always finds it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.ratelimit import rate_limit
from radar.api.routes.memo import report_out
from radar.db import get_session, session_scope
from radar.models import Company, Person, ResearchReport, UserAction
from radar.schemas.api import InvestigateRequest, ReportOut
from radar.services.presenters import resolve_entity as resolve_by_id_or_slug

log = logging.getLogger("radar.api.investigate")
router = APIRouter(tags=["research"])

KEEPALIVE_SECONDS = 15.0
EVENT_KEYS = ("type", "label", "message", "tool", "summary", "status", "report_id", "title", "detail", "input")
EVENT_TYPES = {"step", "tool_call", "tool_result", "section", "done", "error"}
_END = object()


def sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _clean(event: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only the public keys of an analyst event; drop unknown types."""
    t = event.get("type")
    if t not in EVENT_TYPES:
        return None
    out = {k: event[k] for k in EVENT_KEYS if k in event and event[k] is not None}
    if t == "tool_result" and "status" not in out:
        out["status"] = "error" if event.get("is_error") else "ok"
    if t == "step" and "label" not in out and "message" in out:
        out["label"] = out["message"]
    return out


def _run_worker(entity_type: str, entity_id: str, live_fetch: bool, emit) -> None:
    held_done: dict[str, Any] | None = None

    def on_event(ev: dict[str, Any]) -> None:
        nonlocal held_done
        if ev.get("type") == "done":
            held_done = ev
            return
        if ev.get("type") == "error":
            return  # reported once below, without internal details
        cleaned = _clean(ev)
        if cleaned:
            emit(cleaned)

    try:
        from radar.agent.analyst import investigate

        with session_scope() as s:
            report = investigate(s, entity_type, entity_id, live_fetch=live_fetch, on_event=on_event)
            s.add(UserAction(action="investigate", entity_type=entity_type, entity_id=report.entity_id,
                             meta={"report_id": str(report.id), "generator": report.generator}))
            report_id, generator = str(report.id), report.generator
        # committed — now it is safe to announce the report
        done = _clean(held_done or {"type": "done"}) or {"type": "done"}
        done.update(report_id=report_id, status="complete", generator=generator)
        emit(done)
    except Exception as e:
        log.exception("investigation failed for %s %s", entity_type, entity_id)
        emit({"type": "error", "message": f"Investigation failed ({type(e).__name__}). See server logs."})
    finally:
        emit(_END)


async def _stream(entity_type: str, entity_id: str, live_fetch: bool) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(item: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    thread = threading.Thread(target=_run_worker, args=(entity_type, entity_id, live_fetch, emit),
                              name="radar-investigate", daemon=True)
    yield sse({"type": "step", "label": "Investigation started"})
    thread.start()
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
        except TimeoutError:
            yield ": keep-alive\n\n"
            continue
        if item is _END:
            break
        yield sse(item)


def _resolve(session: Session, entity_type: str, ref: str) -> Company | Person:
    from radar.agent.tools import ToolInputError, resolve_entity

    try:
        return resolve_entity(session, entity_type, ref)
    except ToolInputError:
        raise HTTPException(404, detail=f"unknown {entity_type}: {ref}") from None


@router.post("/investigate", dependencies=[Depends(rate_limit("investigate", 6))],
             response_class=StreamingResponse,
             responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream of agent events"}})
def investigate_stream(req: InvestigateRequest, session: Annotated[Session, Depends(get_session)]):
    entity = _resolve(session, req.entity_type, req.entity_id.strip())  # 404 before streaming
    return StreamingResponse(
        _stream(req.entity_type, str(entity.id), req.live_fetch),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/investigate/latest", response_model=ReportOut)
def latest_brief(
    session: Annotated[Session, Depends(get_session)],
    entity_id: str = Query(..., min_length=1, max_length=200),
) -> ReportOut:
    ent = resolve_by_id_or_slug(session, Company, entity_id) or resolve_by_id_or_slug(session, Person, entity_id)
    if ent is None:
        raise HTTPException(404, detail="unknown entity")
    r = session.scalar(
        select(ResearchReport).where(ResearchReport.entity_id == ent.id, ResearchReport.kind == "brief",
                                     ResearchReport.status == "complete")
        .order_by(ResearchReport.created_at.desc()).limit(1)
    )
    if r is None:
        raise HTTPException(404, detail="no brief yet for this entity")
    return report_out(session, r)
