"""Standard error response helpers.

All HTTP errors raised by route handlers should use these helpers so
that every 4xx response carries a consistent JSON shape:

    {
        "error_code": "not_found",
        "message": "project abc not found",
        "details": null,
        "request_id": null
    }

The ``request_id`` is null here because FastAPI serialises HTTPException
detail as-is; the ``RequestLoggingMiddleware`` always echoes the ID in
the ``X-Request-ID`` response header, which is the canonical correlation
point for clients.
"""

from __future__ import annotations

from typing import Any, Never

from fastapi import HTTPException, status
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Standard error envelope returned on all 4xx responses."""

    error_code: str
    message: str
    details: Any = None
    request_id: str | None = None


def _http(status_code: int, error_code: str, message: str, details: Any = None) -> Never:
    raise HTTPException(
        status_code=status_code,
        detail=ErrorDetail(
            error_code=error_code,
            message=message,
            details=details,
        ).model_dump(exclude_none=True),
    )


def raise_not_found(message: str, *, error_code: str = "not_found", details: Any = None) -> Never:
    """Raise HTTP 404 with a standard error envelope."""
    _http(status.HTTP_404_NOT_FOUND, error_code, message, details)


def raise_forbidden(message: str, *, error_code: str = "forbidden", details: Any = None) -> Never:
    """Raise HTTP 403 with a standard error envelope."""
    _http(status.HTTP_403_FORBIDDEN, error_code, message, details)


def raise_conflict(message: str, *, error_code: str = "conflict", details: Any = None) -> Never:
    """Raise HTTP 409 with a standard error envelope."""
    _http(status.HTTP_409_CONFLICT, error_code, message, details)


def raise_bad_request(
    message: str, *, error_code: str = "bad_request", details: Any = None
) -> Never:
    """Raise HTTP 400 with a standard error envelope."""
    _http(status.HTTP_400_BAD_REQUEST, error_code, message, details)


def raise_unprocessable(
    message: str, *, error_code: str = "unprocessable", details: Any = None
) -> Never:
    """Raise HTTP 422 with a standard error envelope."""
    _http(status.HTTP_422_UNPROCESSABLE_ENTITY, error_code, message, details)
