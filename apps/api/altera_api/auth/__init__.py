"""Authentication and per-request organisation scoping.

Phase 13C ships:

* Supabase JWT bearer-token verification. HS256 tokens are verified
  against ``SUPABASE_JWT_SECRET``; ES256/RS256 tokens are verified
  against the project JWKS at ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.
* An :class:`AuthContext` attached to every authenticated request,
  carrying ``user_id``, ``email``, ``organisation_id``, and ``role``.
* An explicit dev fallback (``ALTERA_DEV_AUTH_ENABLED=true``) for
  local development and CI. Disabled by default; never enable in
  production.

The Postgres-backed persistence layer that would let us look up
profiles + memberships against the schema landed in Phase 13A still
sits behind the in-memory store — that swap is a later phase. The
auth layer here works against either backend because it asks the
``Store`` for ``user_profile`` + ``membership`` records via the same
interface.
"""

from __future__ import annotations

from altera_api.auth.config import AuthSettings, get_auth_settings
from altera_api.auth.dependency import authed_user, optional_authed_user
from altera_api.auth.errors import AuthError
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.auth.verifier import verify_supabase_jwt

__all__ = [
    "AuthContext",
    "AuthError",
    "AuthProvider",
    "AuthSettings",
    "authed_user",
    "get_auth_settings",
    "optional_authed_user",
    "verify_supabase_jwt",
]
