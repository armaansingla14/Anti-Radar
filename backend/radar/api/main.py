"""FastAPI application. Routers are discovered from radar.api.routes.* (each exposes `router`)."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from radar import __version__, clock, config_check
from radar.api import routes
from radar.api.auth import AdminGateMiddleware
from radar.settings import get_settings

log = logging.getLogger("radar.api")


def pin_snapshot_clock() -> date | None:
    """DEMO_SNAPSHOT backends always reason as of the verified snapshot, even if a live run slipped in."""
    if clock.as_of() is not None:
        return clock.as_of()
    from sqlalchemy import select

    from radar.db import session_scope
    from radar.models import IngestionRun

    day: date | None = None
    try:
        with session_scope() as s:
            params = s.scalar(select(IngestionRun.params).where(IngestionRun.provider == "snapshot")
                              .order_by(IngestionRun.started_at.desc()).limit(1))
        if params and params.get("as_of"):
            day = date.fromisoformat(params["as_of"])
    except Exception:  # noqa: BLE001 — DB not ready; fall back to the committed manifest
        day = None
    if day is None:
        from radar.demo.seed import snapshot_manifest

        manifest = snapshot_manifest() or {}
        if manifest.get("as_of"):
            day = date.fromisoformat(manifest["as_of"])
    if day is not None:
        clock.set_as_of(day)
    return day


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    try:
        report = config_check.validate(settings)
    except config_check.ConfigError as e:
        log.error("%s", e.render())
        raise
    for w in report.warnings:
        log.warning("config: %s", w)
    clock.init_from_db()
    if settings.demo_snapshot:
        pin_snapshot_clock()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ANTI RADAR",
        version=__version__,
        description="Conviction Before Consensus — technical-signal intelligence API.",
        lifespan=lifespan,
    )
    # Added first so CORS (added last, outermost) still decorates the gate's 403s.
    app.add_middleware(AdminGateMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Radar-Admin"],
        expose_headers=["X-Total-Count"],
    )

    @app.get("/api/health")
    def health() -> dict:
        """Liveness: never touches the database (cheap keep-warm target)."""
        return {"ok": True, "version": __version__, "app_env": get_settings().app_env,
                "as_of": clock.today().isoformat(), "snapshot": clock.as_of() is not None}

    @app.get("/api/ready")
    def ready() -> JSONResponse:
        """Readiness: the database answers ``SELECT 1``."""
        from sqlalchemy import text

        from radar.db import engine

        try:
            with engine().connect() as c:
                c.execute(text("SELECT 1"))
        except Exception as e:  # noqa: BLE001
            log.warning("readiness check failed: %s", type(e).__name__)
            return JSONResponse({"ok": False, "database": "unavailable"}, status_code=503,
                                headers={"Cache-Control": "no-store"})
        return JSONResponse({"ok": True, "database": "ok"}, headers={"Cache-Control": "no-store"})

    for mod in sorted(pkgutil.iter_modules(routes.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{routes.__name__}.{mod.name}")
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")
    return app


app = create_app()
