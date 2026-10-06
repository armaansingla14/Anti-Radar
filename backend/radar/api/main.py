"""FastAPI application. Routers are discovered from radar.api.routes.* (each exposes `router`)."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from radar import __version__, clock
from radar.api import routes
from radar.settings import get_settings

log = logging.getLogger("radar.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    clock.init_from_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ANTI RADAR",
        version=__version__,
        description="Conviction Before Consensus — technical-signal intelligence API.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Total-Count"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "as_of": clock.today().isoformat(),
                "snapshot": clock.as_of() is not None}

    for mod in sorted(pkgutil.iter_modules(routes.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{routes.__name__}.{mod.name}")
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")
    return app


app = create_app()
