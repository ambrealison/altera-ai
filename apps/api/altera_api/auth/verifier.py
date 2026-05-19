"""Supabase JWT verification.

Supabase issues two kinds of access tokens depending on the project's
auth signing configuration:

* **HS256** with a shared secret — the original ("Legacy JWT Secret")
  scheme. Verified against ``SUPABASE_JWT_SECRET``.
* **ES256 / RS256** with asymmetric keys exposed via JWKS — the current
  default for projects that enabled the new key-based signing. Verified
  against ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.

We choose the verification path from the token's ``alg`` header so a
single deployment can accept either flavour. The JWKS client is cached
per Supabase URL so we don't refetch on every request.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from altera_api.auth.config import get_auth_settings
from altera_api.auth.errors import InvalidTokenError

#: Algorithms verified against the JWKS endpoint.
_ASYMMETRIC_ALGORITHMS = frozenset({"ES256", "RS256"})

#: Algorithms verified against ``SUPABASE_JWT_SECRET``.
_SYMMETRIC_ALGORITHMS = frozenset({"HS256"})

#: Process-wide JWKS client cache keyed by JWKS URL. PyJWKClient already
#: caches the fetched keys internally (default 5 minutes); we just want
#: to avoid recreating the client object on every request.
_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_url(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _issuer(supabase_url: str) -> str:
    return f"{supabase_url.rstrip('/')}/auth/v1"


def _get_jwks_client(supabase_url: str) -> PyJWKClient:
    url = _jwks_url(supabase_url)
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(url, cache_keys=True, lifespan=600)
        _jwks_clients[url] = client
    return client


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return the decoded claims.

    Raises :class:`InvalidTokenError` on any failure: missing
    configuration, unsupported algorithm, unknown signing key,
    malformed token, bad signature, expired, wrong audience, wrong
    issuer.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(f"invalid token: {exc}") from exc

    alg_raw = header.get("alg")
    alg = str(alg_raw).upper() if alg_raw else ""

    settings = get_auth_settings()

    key: Any
    if alg in _SYMMETRIC_ALGORITHMS:
        if settings.supabase_jwt_secret is None:
            raise InvalidTokenError(
                "server is not configured for HS256 auth (SUPABASE_JWT_SECRET missing)"
            )
        key = settings.supabase_jwt_secret
        expect_issuer: str | None = None
    elif alg in _ASYMMETRIC_ALGORITHMS:
        if not settings.supabase_url:
            raise InvalidTokenError(
                f"server is not configured for {alg} auth (SUPABASE_URL missing)"
            )
        try:
            jwks_client = _get_jwks_client(settings.supabase_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientError as exc:
            raise InvalidTokenError(f"could not resolve signing key: {exc}") from exc
        key = signing_key.key
        expect_issuer = _issuer(settings.supabase_url)
    else:
        raise InvalidTokenError(f"unsupported alg: {alg_raw!r}")

    decode_kwargs: dict[str, Any] = {
        "algorithms": [alg],
        "audience": settings.supabase_jwt_audience,
        "options": {"require": ["exp", "sub"]},
    }
    if expect_issuer is not None:
        decode_kwargs["issuer"] = expect_issuer

    try:
        claims: dict[str, Any] = jwt.decode(token, key, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise InvalidTokenError("token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise InvalidTokenError("token has wrong audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise InvalidTokenError("token has wrong issuer") from exc
    except jwt.InvalidTokenError as exc:
        raise InvalidTokenError(f"invalid token: {exc}") from exc
    return claims
