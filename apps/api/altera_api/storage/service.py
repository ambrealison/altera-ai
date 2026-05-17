"""Supabase Storage thin wrapper.

Provides two operations used by the upload flow:
- ``generate_upload_url`` — returns a signed upload URL the browser can PUT to directly.
- ``download`` — downloads an object and returns its bytes (used by the ingest endpoint).
"""
from __future__ import annotations

from uuid import UUID


class StorageService:
    BUCKET = "uploads"
    EXPORT_BUCKET = "exports"

    def __init__(self, supabase_client) -> None:  # type: ignore[no-untyped-def]
        self._client = supabase_client

    def storage_path(
        self,
        organisation_id: UUID,
        project_id: UUID,
        upload_id: UUID,
        filename: str,
    ) -> str:
        return (
            f"organisations/{organisation_id}/projects/{project_id}"
            f"/uploads/{upload_id}/raw/{filename}"
        )

    def export_storage_path(
        self,
        organisation_id: UUID,
        run_id: UUID,
        export_id: UUID,
        filename: str,
    ) -> str:
        return f"organisations/{organisation_id}/exports/{run_id}/{export_id}/{filename}"

    def generate_upload_url(self, storage_path: str, expires_in: int = 300) -> str:
        """Return a signed URL the client can upload a file to."""
        result = (
            self._client.storage.from_(self.BUCKET)
            .create_signed_upload_url(storage_path)
        )
        # supabase-py returns {"signedUrl": "...", "token": "...", "path": "..."}
        return result["signedUrl"]

    def download(self, storage_path: str) -> bytes:
        """Download ``storage_path`` from the uploads bucket and return its bytes."""
        return self._client.storage.from_(self.BUCKET).download(storage_path)

    def upload_export(self, storage_path: str, content: bytes, filename: str) -> None:
        """Upload a rendered export to the exports bucket."""
        self._client.storage.from_(self.EXPORT_BUCKET).upload(
            storage_path,
            content,
            file_options={"content-type": "application/octet-stream", "upsert": "true"},
        )

    def generate_export_download_url(
        self, storage_path: str, filename: str, expires_in: int = 3600
    ) -> str:
        """Return a signed download URL for an export file."""
        result = self._client.storage.from_(self.EXPORT_BUCKET).create_signed_url(
            storage_path,
            expires_in,
            options={"download": filename},
        )
        return result["signedUrl"]
