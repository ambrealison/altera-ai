"""FastAPI application entry point.

* ``GET  /health``   liveness check
* ``GET  /version``  app version + build phase
* ``/api/v1/*``       Phase 12 SaaS API surface (projects, uploads,
                      classify, review, runs, exports), backed by an
                      in-memory store.

Auth lands with Supabase in Phase 13; until then a stub demo user is
wired in via :mod:`altera_api.api.dependencies`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from altera_api.api import api_router
from altera_api.observability import (
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    init_sentry,
)
from altera_api.version import VersionInfo, get_version_info


def _parse_allowed_origins() -> list[str]:
    """Return the CORS allowed-origins list from ``CORS_ALLOWED_ORIGINS``.

    The env var is a comma-separated list of origins
    (e.g. ``https://app.altera-ai.com,https://staging.altera-ai.com``).
    Defaults to ``http://localhost:3000`` for local dev.

    Never use ``*`` with ``allow_credentials=True`` — browsers will
    reject the pre-flight and CORS will silently break.  In production,
    set this to the exact frontend origin(s).
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


class HealthResponse(BaseModel):
    status: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=os.getenv("LOG_LEVEL", "INFO"))
    init_sentry()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Altera AI API",
        version=get_version_info().app_version,
        description=(
            "Open-source SaaS backend for retailer protein-ratio analysis. "
            "Implements the Protein Tracker (GPA & ProVeg) and WWF Planet-Based "
            "Diets Retailer methodologies as strictly separate pipelines."
        ),
        lifespan=_lifespan,
    )

    # Middleware is applied in reverse-declaration order (last added = outermost
    # wrapper).  Declaration order here:
    #   1. SecurityHeadersMiddleware — added first → runs last (innermost); stamps
    #      security headers on every outgoing response.
    #   2. CORSMiddleware — handles preflight and attaches CORS headers.
    #   3. RequestLoggingMiddleware — added last → runs first (outermost); logs
    #      every request, including OPTIONS preflights.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/version", response_model=VersionInfo, tags=["meta"])
    def version() -> VersionInfo:
        return get_version_info()

    app.include_router(api_router)

    return app


app = create_app()
