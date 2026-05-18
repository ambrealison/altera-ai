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
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from altera_api.api import api_router
from altera_api.observability import RequestLoggingMiddleware, configure_logging, init_sentry
from altera_api.version import VersionInfo, get_version_info


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

    # RequestLoggingMiddleware must be added before CORSMiddleware so it
    # wraps every request (including preflight OPTIONS).
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/version", response_model=VersionInfo, tags=["meta"])
    def version() -> VersionInfo:
        return get_version_info()

    app.include_router(api_router)

    return app


app = create_app()
