"""Persistence factory — selects MemoryRepository or PostgresRepository.

The environment variable ``ALTERA_USE_IN_MEMORY_STORE`` (default ``true``)
controls which implementation is returned.  Both are singletons: the
in-memory store is created once at import time; the Postgres client is
created once on the first request that needs it.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from altera_api.persistence.memory import MemoryRepository
from altera_api.persistence.protocol import StoreProtocol

_memory_repo: MemoryRepository | None = None


class PersistenceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    altera_use_in_memory_store: bool = True
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None
    # Anon key is used to create per-request JWT-scoped clients for RLS.
    supabase_anon_key: str | None = None


def get_repository(user_jwt: str | None = None) -> StoreProtocol:
    """Return the active repository.

    - ``ALTERA_USE_IN_MEMORY_STORE=true`` (default) → ``MemoryRepository``
      singleton (``user_jwt`` is ignored).
    - ``ALTERA_USE_IN_MEMORY_STORE=false`` → ``PostgresRepository``.
      When ``user_jwt`` and ``SUPABASE_ANON_KEY`` are both present a
      second, JWT-scoped Supabase client is created so Postgres RLS
      policies apply to data operations.  The service-role client is
      still used for identity bootstrap and audit writes.
    """
    global _memory_repo
    settings = PersistenceSettings()

    if settings.altera_use_in_memory_store:
        if _memory_repo is None:
            _memory_repo = MemoryRepository()
        return _memory_repo

    # Postgres path — lazy import to avoid loading supabase-py in tests.
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set "
            "when ALTERA_USE_IN_MEMORY_STORE=false."
        )
    from supabase import create_client

    from altera_api.persistence.postgres import PostgresRepository

    svc_client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    rls_client = None
    if user_jwt and settings.supabase_anon_key:
        from supabase.lib.client_options import SyncClientOptions

        rls_client = create_client(
            settings.supabase_url,
            settings.supabase_anon_key,
            options=SyncClientOptions(headers={"Authorization": f"Bearer {user_jwt}"}),
        )

    return PostgresRepository(svc_client, rls_client=rls_client)
