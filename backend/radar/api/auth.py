"""Admin gate for writes.

When ``ADMIN_TOKEN`` is configured, every non-safe request (anything but GET/HEAD/OPTIONS) under ``/api``
must carry ``X-Radar-Admin: <token>``; otherwise it is rejected with ``403 {"detail": "read_only"}``.
Without a token configured writes are open (development; the demo backend), except that a backend serving
the verified snapshot (``DEMO_SNAPSHOT``) never accepts ingestion, which would contaminate the snapshot.

Implemented as a pure ASGI middleware so streaming (SSE) responses pass through untouched.
"""

from __future__ import annotations

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

from radar.settings import Settings, get_settings

ADMIN_HEADER = "x-radar-admin"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def token_valid(provided: str | None, settings: Settings | None = None) -> bool:
    """Constant-time comparison against the configured admin token (False when none is configured)."""
    expected = (settings or get_settings()).admin_token
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def is_admin_request(headers: dict[str, str] | None, settings: Settings | None = None) -> bool:
    return token_valid((headers or {}).get(ADMIN_HEADER), settings)


def read_only(headers: dict[str, str] | None, settings: Settings | None = None) -> bool:
    """True when writes from this request would be refused by the gate."""
    s = settings or get_settings()
    return bool(s.admin_token) and not is_admin_request(headers, s)


def write_denied(method: str, path: str, headers: dict[str, str], settings: Settings | None = None) -> str | None:
    """The ``detail`` to reject a request with, or None to let it through."""
    if method.upper() in SAFE_METHODS or not path.startswith("/api/"):
        return None
    s = settings or get_settings()
    if s.admin_token:
        return None if is_admin_request(headers, s) else "read_only"
    if s.demo_snapshot and path.startswith("/api/ingest"):
        return "snapshot_read_only"
    return None


class AdminGateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        detail = write_denied(scope.get("method", "GET"), scope.get("path", ""), headers)
        if detail is None:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})
