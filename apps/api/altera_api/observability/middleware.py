"""HTTP request logging middleware.

Assigns a ``request_id`` (from ``X-Request-ID`` or generated), propagates
it to the structured log context, and emits a single ``request.complete``
log line per request with method, path, status code, and duration_ms.

Sensitive headers (Authorization, Cookie) and request bodies are never
logged.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from altera_api.observability.logging import get_logger, set_request_context

logger = get_logger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with structured fields.

    Sets ``X-Request-ID`` on the response so callers can correlate
    client-side errors with backend log lines.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.monotonic()

        set_request_context(request_id=request_id)

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "request.complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        )

        response.headers["x-request-id"] = request_id
        return response
