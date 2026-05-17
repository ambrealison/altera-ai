"""Auth configuration loaded from environment variables.

We re-read the environment on every ``get_auth_settings()`` call (no
caching). Tests use ``monkeypatch.setenv(...)`` and the next request
picks the new values up — no need to bust a cache or restart workers.
"""
from __future__ import annotations

from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Auth-related env vars.

    All fields are optional so a freshly-cloned repo boots without a
    configured Supabase project; tests that need real verification
    set the env vars they need explicitly.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=None,  # tests drive via monkeypatch, not .env
        case_sensitive=False,
        extra="ignore",
    )

    # Supabase project config
    supabase_url: str | None = None
    supabase_jwt_secret: str | None = None
    supabase_service_role_key: str | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_algorithm: str = "HS256"

    # Development fallback — off unless explicitly enabled.
    altera_dev_auth_enabled: bool = False
    altera_dev_user_id: UUID | None = None
    altera_dev_organisation_id: UUID | None = None
    altera_dev_user_email: str | None = None


def get_auth_settings() -> AuthSettings:
    """Construct a fresh settings instance from the environment.

    No lru_cache: tests must be able to flip ``ALTERA_DEV_AUTH_ENABLED``
    between calls via monkeypatch.
    """
    return AuthSettings()
