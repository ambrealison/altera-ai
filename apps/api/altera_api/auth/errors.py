"""Auth-specific exceptions surfaced as HTTP 401/403."""
from __future__ import annotations


class AuthError(Exception):
    """Base class for authentication / authorisation failures."""


class InvalidTokenError(AuthError):
    """JWT signature, expiry, audience, or claim invalid."""


class MissingProfileError(AuthError):
    """Token verified but no matching ``user_profile`` exists in the store."""


class NoMembershipError(AuthError):
    """User has no organisation membership."""


class DevAuthDisabledError(AuthError):
    """The dev fallback was attempted but the env flag is off."""
