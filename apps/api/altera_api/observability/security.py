"""Security response-headers middleware.

Adds a conservative set of security headers to every HTTP response.
Does NOT set Strict-Transport-Security (HSTS) — that belongs at the
reverse-proxy / CDN layer where ``includeSubDomains`` and preloading
can be managed correctly.

Headers applied:
- ``X-Content-Type-Options: nosniff`` — prevents MIME-type sniffing.
- ``X-Frame-Options: DENY`` — disallows framing (clickjacking defence).
- ``Referrer-Policy: strict-origin-when-cross-origin`` — limits origin
  leakage on cross-origin navigations.
- ``Permissions-Policy`` — conservative deny-list for sensitive browser
  features.
- ``Cache-Control: no-store`` for all ``/api/`` paths — prevents
  proxies and browsers from caching user-specific API responses.

All headers are set with ``setdefault`` so route handlers can override
them if needed (e.g. a public resource that is intentionally cacheable).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach security headers to every response."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
