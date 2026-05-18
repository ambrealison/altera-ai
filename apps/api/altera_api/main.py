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
from altera_api.ratelimit import RateLimitMiddleware
from altera_api.version import VersionInfo, get_version_info


def _parse_allowed_origins() -> list[str]:
    """Return the CORS allowed-origins list from ``CORS_ALLOWED_ORIGINS``.

    The env var is a comma-separated list of origins
    (e.g. ``https://app.altera-ai.com,https://staging.altera-ai.com``).
    Defaults to ``http://localhost:3000`` for local dev.

    Never use ``*`` with ``allow_credentials=True`` — browsers will reject
    pre-flights and CORS will silently break.  Production safety is
    enforced at startup by :func:`_check_cors_production_config`.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["http://localhost:3000"]


def _check_cors_production_config() -> None:
    """Raise ``RuntimeError`` if production mode has no explicit CORS origins.

    Called at server startup (lifespan), after env vars are in their final
    state.  When ``ALTERA_DEV_AUTH_ENABLED`` is true (dev/test) the
    localhost fallback from :func:`_parse_allowed_origins` is acceptable.
    In all other modes ``CORS_ALLOWED_ORIGINS`` must be explicitly set.
    """
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if not origins:
        dev_auth = os.getenv("ALTERA_DEV_AUTH_ENABLED", "false").lower()
        if dev_auth not in ("1", "true", "yes"):
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must be set in production "
                "(ALTERA_DEV_AUTH_ENABLED is not true). "
                "Set it to the exact frontend origin, e.g. "
                "CORS_ALLOWED_ORIGINS=https://app.altera-ai.com"
            )


class HealthResponse(BaseModel):
    status: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _check_cors_production_config()
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
    #   1. RateLimitMiddleware  — added first → innermost; 429 responses are
    #      still wrapped by SecurityHeadersMiddleware so headers are stamped.
    #   2. SecurityHeadersMiddleware — stamps security headers on every response
    #      including 429s from RateLimitMiddleware.
    #   3. CORSMiddleware — handles preflight and attaches CORS headers.
    #   4. RequestLoggingMiddleware — added last → outermost; logs every request.
    app.add_middleware(RateLimitMiddleware)
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
