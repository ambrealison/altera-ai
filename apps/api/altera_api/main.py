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

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from altera_api.api import api_router
from altera_api.api.admin import admin_router
from altera_api.api.templates import templates_router
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

    # PostgREST surfaces RLS denials as APIError(code='42501'). The
    # caller already passed our route-level permission check, so the
    # only path to this is a write that RLS forbids — return a clean
    # 403 with a JSON body instead of a bare 500.
    try:
        from postgrest.exceptions import APIError as _PostgrestAPIError
    except ImportError:  # supabase-py not installed (in-memory-only deployments)
        _PostgrestAPIError = None  # type: ignore[assignment]

    if _PostgrestAPIError is not None:

        @app.exception_handler(_PostgrestAPIError)
        async def _handle_postgrest_api_error(  # noqa: ANN202
            _request: Request, exc: _PostgrestAPIError
        ) -> JSONResponse:
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None)
            if code == "42501":
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "detail": (
                            "operation not permitted by row-level security; "
                            "your role does not allow this write"
                        ),
                        "code": "rls_denied",
                    },
                )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": message or "database error",
                    "code": code or "postgrest_error",
                },
            )

    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(templates_router)

    return app


app = create_app()
