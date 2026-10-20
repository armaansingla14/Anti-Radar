"""Who is this request, as far as the UI needs to know: environment, write access, dataset."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from radar import clock
from radar.api.auth import is_admin_request, read_only
from radar.settings import get_settings

router = APIRouter(tags=["session"])


@router.get("/session")
def session(request: Request) -> JSONResponse:
    s = get_settings()
    headers = {k.lower(): v for k, v in request.headers.items()}
    as_of = clock.as_of()
    body = {
        "app_env": s.app_env,
        "read_only": read_only(headers, s),
        "admin": is_admin_request(headers, s),
        "demo_snapshot": s.demo_snapshot,
        "demo_synthetic": s.demo_synthetic,
        "snapshot_as_of": as_of.isoformat() if as_of else None,
    }
    # Depends on a request header: never cache across visitors.
    return JSONResponse(body, headers={"Cache-Control": "no-store", "Vary": "X-Radar-Admin"})
