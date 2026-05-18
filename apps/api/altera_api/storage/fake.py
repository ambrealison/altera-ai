"""In-memory StorageService double for use in tests."""

from __future__ import annotations

from uuid import UUID


class FakeStorageService:
    """Test double for StorageService.

    Pre-populate downloads with ``stage(path, content)`` before the job
    runs.  Inspect uploaded exports via ``get_export(path)`` or the
    ``exports`` property.
    """

    def __init__(self) -> None:
        self._uploads: dict[str, bytes] = {}
        self._exports: dict[str, bytes] = {}

    # ------------------------------------------------------------------
    # Test helpers
    # ------------------------------------------------------------------

    def stage(self, storage_path: str, content: bytes) -> None:
        """Make *content* available at *storage_path* for ``download()``."""
        self._uploads[storage_path] = content

    def get_export(self, storage_path: str) -> bytes | None:
        return self._exports.get(storage_path)

    @property
    def exports(self) -> dict[str, bytes]:
        return dict(self._exports)

    # ------------------------------------------------------------------
    # StorageProtocol interface
    # ------------------------------------------------------------------

    def download(self, storage_path: str) -> bytes:
        if storage_path not in self._uploads:
            raise FileNotFoundError(f"not staged in FakeStorageService: {storage_path!r}")
        return self._uploads[storage_path]

    def upload_export(self, storage_path: str, content: bytes, filename: str) -> None:
        self._exports[storage_path] = content

    def export_storage_path(
        self,
        organisation_id: UUID,
        run_id: UUID,
        export_id: UUID,
        filename: str,
    ) -> str:
        return f"organisations/{organisation_id}/exports/{run_id}/{export_id}/{filename}"

    def generate_export_download_url(
        self, storage_path: str, filename: str, expires_in: int = 600
    ) -> str:
        return f"https://fake-storage.test/{storage_path}?download={filename}"

    # ------------------------------------------------------------------
    # Full StorageService interface (for prepare/ingest route tests)
    # ------------------------------------------------------------------

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

    def generate_upload_url(self, storage_path: str, expires_in: int = 300) -> str:
        return f"https://fake-storage.test/upload?path={storage_path}"
