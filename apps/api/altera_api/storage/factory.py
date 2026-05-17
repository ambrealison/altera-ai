"""FastAPI dependency that returns a ``StorageService`` (or ``None`` in dev mode)."""
from __future__ import annotations

from pydantic_settings import BaseSettings

from altera_api.storage.service import StorageService


class StorageSettings(BaseSettings):
    supabase_url: str | None = None
    supabase_service_role_key: str | None = None


def get_storage_service() -> StorageService | None:
    settings = StorageSettings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    from supabase import create_client

    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return StorageService(client)
