"""In-memory sliding-window rate limiter.

Disabled by default (``RATE_LIMIT_ENABLED=false``).  Enable for deployments
that run a single API process; for multi-process production deployments replace
with a Redis-backed implementation or push rate limiting to the API gateway.

Route groups and their default limits (requests per minute):

+----------+-----+-----------------------------------------------+
| Group    | RPM | Matched paths                                 |
+----------+-----+-----------------------------------------------+
| uploads  |  20 | POST …/uploads, …/uploads/prepare, …/ingest,  |
|          |     | …/jobs/validate, …/wwf-ingredients/upload     |
| classify |  10 | POST …/classify, …/jobs/classify              |
| exports  |  30 | GET …/export, POST …/jobs/export              |
| compute  |   5 | POST …/jobs/calculate, …/scenarios/{id}/run,  |
|          |     | GET …/comparisons                             |
| default  | 200 | everything else                               |
| skip     |   — | OPTIONS (preflight, never rate-limited)       |
+----------+-----+-----------------------------------------------+

Key selection (Phase 30C — safe IP-only):
- All requests are keyed by client IP.
- X-Forwarded-For is only trusted when the connecting peer is in the
  ``TRUSTED_PROXIES`` CIDR allowlist (empty by default).
- Unverified JWT sub is NOT used: before signature verification the sub
  claim is attacker-controlled and cannot be trusted for rate-limit keying.

Memory management:
- Buckets are evicted when their sliding window is empty and they have
  not been touched for more than one window length.
- Eviction runs opportunistically every ``CLEANUP_INTERVAL`` requests.
- Total bucket count is capped at ``RATE_LIMIT_MAX_BUCKETS`` (default
  100 000).  If the cap is hit, the oldest (by last_seen) bucket is
  dropped to make room.
"""

from __future__ import annotations

import ipaddress
import os
import threading
import time
from collections import OrderedDict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# ---------------------------------------------------------------------------
# Route-group classification
# ---------------------------------------------------------------------------

_CLASSIFY_SUFFIXES = ("/classify", "/jobs/classify")
_EXPORT_SUFFIXES = ("/export", "/jobs/export")


def _route_group(path: str, method: str) -> str:
    """Return the rate-limit group name for a (path, method) pair.

    ``"skip"`` means the request is exempt (e.g. OPTIONS preflight).
    """
    if method.upper() == "OPTIONS":
        return "skip"

    tail = path.split("/api/v1", 1)[-1]
    m = method.upper()

    if m == "POST":
        # Upload group — all routes that accept or process file data.
        if (
            tail.endswith("/uploads")
            or tail.endswith("/uploads/prepare")
            or tail.endswith("/ingest")
            or tail.endswith("/jobs/validate")
            or tail.endswith("/wwf-ingredients/upload")
        ):
            return "uploads"

        # Classify group.
        for sfx in _CLASSIFY_SUFFIXES:
            if tail.endswith(sfx):
                return "classify"

        # Export generation.
        for sfx in _EXPORT_SUFFIXES:
            if tail.endswith(sfx):
                return "exports"

        # Compute group — calculation jobs and scenario runs.
        if tail.endswith("/jobs/calculate"):
            return "compute"
        if tail.endswith("/run") and "/scenarios/" in tail:
            return "compute"

    if m == "GET":
        if tail.endswith("/export"):
            return "exports"
        if tail.endswith("/comparisons"):
            return "compute"

    return "default"


# ---------------------------------------------------------------------------
# Trusted proxy helpers
# ---------------------------------------------------------------------------

def _parse_trusted_proxies(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse a comma-separated CIDR/IP list into network objects.

    Silently skips invalid entries so a typo does not crash startup.
    """
    result = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            pass
    return result


def _is_trusted_proxy(
    host: str,
    trusted: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    if not trusted or not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in trusted)
    except ValueError:
        return False


def _extract_ip(
    request: Request,
    trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> str:
    """Return the effective client IP for rate-limit keying.

    Uses ``X-Forwarded-For`` only when the direct peer is a trusted proxy.
    Never falls back to unverified JWT claims.
    """
    peer = request.client.host if request.client else ""

    if _is_trusted_proxy(peer, trusted_proxies):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                try:
                    ipaddress.ip_address(first_hop)
                    return f"ip:{first_hop}"
                except ValueError:
                    pass  # malformed; fall through to peer

    return f"ip:{peer}" if peer else "ip:unknown"


# ---------------------------------------------------------------------------
# Sliding-window bucket
# ---------------------------------------------------------------------------

_CLEANUP_INTERVAL = 500  # eviction pass runs every N check() calls


class _Bucket:
    """Sliding-window token tracker for a single (key, group) pair."""

    __slots__ = ("_limit", "_window", "_timestamps", "last_seen")

    def __init__(self, limit: int, window: int = 60) -> None:
        self._limit = limit
        self._window = window
        self._timestamps: deque[float] = deque()
        self.last_seen: float = 0.0

    def is_stale(self, now: float) -> bool:
        """True when empty and untouched for a full window."""
        return not self._timestamps and (now - self.last_seen) > self._window

    def check_and_record(self, now: float) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``.

        Prunes expired entries, checks capacity, and records the request if
        allowed.  ``retry_after_seconds`` is 0 when allowed.
        """
        self.last_seen = now
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
    "compute": 5,
    "default": 200,
}

_ENV_KEYS: dict[str, str] = {
    "uploads": "RATE_LIMIT_UPLOADS_PER_MINUTE",
    "classify": "RATE_LIMIT_CLASSIFY_PER_MINUTE",
    "exports": "RATE_LIMIT_EXPORTS_PER_MINUTE",
    "compute": "RATE_LIMIT_COMPUTE_PER_MINUTE",
    "default": "RATE_LIMIT_DEFAULT_PER_MINUTE",
}

_DEFAULT_MAX_BUCKETS = 100_000


class RateLimiter:
    """Thread-safe in-memory rate limiter with bucket eviction."""

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        trusted_proxies: list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None = None,
        max_buckets: int = _DEFAULT_MAX_BUCKETS,
    ) -> None:
        self._limits: dict[str, int] = limits or dict(_DEFAULT_LIMITS)
        self._trusted_proxies = trusted_proxies or []
        self._max_buckets = max_buckets
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()
        self._lock = threading.Lock()
        self._check_count = 0

    def _evict(self, now: float) -> None:
        """Remove stale buckets; if cap exceeded, drop least-recently-used."""
        stale = [k for k, b in self._buckets.items() if b.is_stale(now)]
        for k in stale:
            del self._buckets[k]

        if len(self._buckets) >= self._max_buckets:
            # LRU order is maintained by move_to_end() in check(); first item
            # is the least-recently-used bucket — O(1) pop.
            self._buckets.popitem(last=False)

    def extract_key(self, request: Request) -> str:
        return _extract_ip(request, self._trusted_proxies)

    def check(self, key: str, group: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``."""
        limit = self._limits.get(group, self._limits.get("default", 200))
        bucket_key = (key, group)
        now = time.monotonic()
        with self._lock:
            self._check_count += 1
            if self._check_count % _CLEANUP_INTERVAL == 0:
                self._evict(now)

            if bucket_key not in self._buckets:
                if len(self._buckets) >= self._max_buckets:
                    self._evict(now)
                self._buckets[bucket_key] = _Bucket(limit)
            self._buckets.move_to_end(bucket_key)
            return self._buckets[bucket_key].check_and_record(now)


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

    trusted_proxies = _parse_trusted_proxies(os.getenv("TRUSTED_PROXIES", ""))

    max_buckets = _DEFAULT_MAX_BUCKETS
    raw_max = os.getenv("RATE_LIMIT_MAX_BUCKETS")
    if raw_max is not None:
        try:
            max_buckets = int(raw_max)
        except ValueError:
            pass

    return RateLimiter(limits=limits, trusted_proxies=trusted_proxies, max_buckets=max_buckets)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limit middleware.

    ``limiter`` can be injected directly (for tests).  When ``read_env`` is
    ``True`` and ``limiter`` is ``None``, the limiter is built from env vars
    at construction time.

    Keys by client IP only.  ``X-Forwarded-For`` is only trusted when the
    direct peer is in the ``TRUSTED_PROXIES`` allowlist.
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

        key = self._limiter.extract_key(request)
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
