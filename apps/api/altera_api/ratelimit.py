"""In-memory sliding-window rate limiter.

Disabled by default (``RATE_LIMIT_ENABLED=false``).  Enable for deployments
that run a single API process; for multi-process production deployments replace
with a Redis-backed implementation or push rate limiting to the API gateway.

Route groups and their default limits (requests per minute):

+----------+-----+-----------------------------------------------+
| Group    | RPM | Matched paths                                 |
+----------+-----+-----------------------------------------------+
| uploads  |  20 | POST …/uploads/prepare, …/ingest,             |
|          |     | …/wwf-ingredients/upload                      |
| classify |  10 | POST …/classify, …/jobs/classify              |
| exports  |  30 | GET …/export, POST …/jobs/export              |
| default  | 200 | everything else                               |
| skip     |   — | OPTIONS (preflight, never rate-limited)       |
+----------+-----+-----------------------------------------------+

Key selection:
- Authenticated requests: base64-decoded JWT ``sub`` claim.
- Unauthenticated requests: IP from ``X-Forwarded-For`` (first hop) or
  ``request.client.host``.

The raw Authorization token is never stored, logged, or included in error
responses.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# ---------------------------------------------------------------------------
# Route-group classification
# ---------------------------------------------------------------------------

_UPLOAD_SUFFIXES = (
    "/uploads/prepare",
    "/uploads/",           # POST …/uploads (legacy direct-upload)
    "/wwf-ingredients/upload",
)
_INGEST_SUFFIX = "/ingest"
_CLASSIFY_SUFFIXES = ("/classify", "/jobs/classify")
_EXPORT_SUFFIXES = ("/export", "/jobs/export")


def _route_group(path: str, method: str) -> str:
    """Return the rate-limit group name for a (path, method) pair.

    ``"skip"`` means the request is exempt (OPTIONS preflight).
    """
    if method.upper() == "OPTIONS":
        return "skip"

    # Strip the /api/v1 prefix for matching.
    tail = path.split("/api/v1", 1)[-1]

    m = method.upper()
    if m == "POST":
        if tail.endswith("/uploads/prepare"):
            return "uploads"
        if tail.endswith(_INGEST_SUFFIX):
            return "uploads"
        if tail.endswith("/wwf-ingredients/upload"):
            return "uploads"
        for sfx in _CLASSIFY_SUFFIXES:
            if tail.endswith(sfx):
                return "classify"
        for sfx in _EXPORT_SUFFIXES:
            if tail.endswith(sfx):
                return "exports"
    if m == "GET":
        if tail.endswith("/export"):
            return "exports"

    return "default"


# ---------------------------------------------------------------------------
# Sliding-window bucket
# ---------------------------------------------------------------------------

class _Bucket:
    """Sliding-window token tracker for a single (key, group) pair."""

    __slots__ = ("_limit", "_window", "_timestamps")

    def __init__(self, limit: int, window: int = 60) -> None:
        self._limit = limit
        self._window = window
        self._timestamps: deque[float] = deque()

    def check_and_record(self, now: float) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``.

        Prunes expired entries, checks capacity, and records the request if
        allowed.  ``retry_after_seconds`` is 0 when allowed.
        """
        cutoff = now - self._window
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) >= self._limit:
            oldest = self._timestamps[0]
            retry_after = int(oldest - cutoff) + 1
            return False, retry_after

        self._timestamps.append(now)
        return True, 0


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_DEFAULT_LIMITS: dict[str, int] = {
    "uploads": 20,
    "classify": 10,
    "exports": 30,
    "default": 200,
}

_ENV_KEYS: dict[str, str] = {
    "uploads": "RATE_LIMIT_UPLOADS_PER_MINUTE",
    "classify": "RATE_LIMIT_CLASSIFY_PER_MINUTE",
    "exports": "RATE_LIMIT_EXPORTS_PER_MINUTE",
    "default": "RATE_LIMIT_DEFAULT_PER_MINUTE",
}


class RateLimiter:
    """Thread-safe in-memory rate limiter."""

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._limits: dict[str, int] = limits or dict(_DEFAULT_LIMITS)
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, group: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        limit = self._limits.get(group, self._limits["default"])
        bucket_key = (key, group)
        now = time.monotonic()
        with self._lock:
            if bucket_key not in self._buckets:
                self._buckets[bucket_key] = _Bucket(limit)
            return self._buckets[bucket_key].check_and_record(now)


# ---------------------------------------------------------------------------
# Key extraction
# ---------------------------------------------------------------------------

def _extract_key(request: Request) -> str:
    """Return a stable, non-secret key for rate-limit bucketing.

    Prefers the JWT ``sub`` claim (base64-decoded from the Authorization
    header — no signature verification, safe for keying only).  Falls back to
    client IP.  The raw token is never stored or logged.
    """
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        parts = token.split(".")
        if len(parts) == 3:
            try:
                payload_b64 = parts[1]
                # Add padding so base64 doesn't complain.
                padding = 4 - len(payload_b64) % 4
                if padding != 4:
                    payload_b64 += "=" * padding
                payload = json.loads(base64.urlsafe_b64decode(payload_b64))
                sub = payload.get("sub")
                if sub:
                    return f"user:{sub}"
            except Exception:  # noqa: BLE001
                pass

    # Fall back to IP.
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )
    return f"ip:{ip}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _build_limiter() -> RateLimiter | None:
    """Return a ``RateLimiter`` configured from env vars, or ``None`` if disabled."""
    enabled = os.getenv("RATE_LIMIT_ENABLED", "false").lower()
    if enabled not in ("1", "true", "yes"):
        return None

    limits = dict(_DEFAULT_LIMITS)
    for group, env_key in _ENV_KEYS.items():
        raw = os.getenv(env_key)
        if raw is not None:
            try:
                limits[group] = int(raw)
            except ValueError:
                pass
    return RateLimiter(limits=limits)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit middleware.

    ``limiter`` can be injected directly (for tests).  When ``read_env`` is
    ``True`` and ``limiter`` is ``None``, the limiter is built from env vars
    at construction time.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter | None = None,
        read_env: bool = True,
    ) -> None:
        super().__init__(app)
        if limiter is not None:
            self._limiter: RateLimiter | None = limiter
        elif read_env:
            self._limiter = _build_limiter()
        else:
            self._limiter = None

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if self._limiter is None:
            return await call_next(request)

        group = _route_group(request.url.path, request.method)
        if group == "skip":
            return await call_next(request)

        key = _extract_key(request)
        allowed, retry_after = self._limiter.check(key, group)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "error_code": "rate_limited",
                        "message": "Too many requests. Please slow down.",
                        "details": {"retry_after_seconds": retry_after},
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
