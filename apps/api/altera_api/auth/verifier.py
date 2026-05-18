"""Supabase JWT verification.

Supabase signs project JWTs with the project's ``JWT secret`` (HS256
by default). The secret is available in the Supabase Studio under
*Project Settings → API*; we read it from ``SUPABASE_JWT_SECRET``.

For projects using asymmetric signing (RS256 with JWKS), swap the
``decode`` call below for ``PyJWKClient`` — the rest of the layer
doesn't change.
"""

from __future__ import annotations

from typing import Any

import jwt

from altera_api.auth.config import get_auth_settings
from altera_api.auth.errors import InvalidTokenError


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return the decoded claims.

    Raises :class:`InvalidTokenError` on any failure: missing secret in
    config, malformed token, bad signature, expired, wrong audience.
    """
    settings = get_auth_settings()
    if settings.supabase_jwt_secret is None:
        raise InvalidTokenError(
            "server is not configured for Supabase auth (SUPABASE_JWT_SECRET missing)"
        )
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=[settings.supabase_jwt_algorithm],
            audience=settings.supabase_jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidTokenError("token has wrong audience") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(f"invalid token: {exc}") from exc
    return claims
