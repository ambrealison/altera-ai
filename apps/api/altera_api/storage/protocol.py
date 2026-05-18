"""Protocol for storage backends used by background jobs.

Structural (duck-typed) so both ``StorageService`` and ``FakeStorageService``
satisfy it without inheriting from a common base class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class StorageProtocol(Protocol):
    """Minimal interface job handlers need from a storage backend."""

    def download(self, storage_path: str) -> bytes: ...

    def upload_export(self, storage_path: str, content: bytes, filename: str) -> None: ...

    def export_storage_path(
        self,
        organisation_id: UUID,
        run_id: UUID,
        export_id: UUID,
        filename: str,
    ) -> str: ...

    def generate_export_download_url(
        self, storage_path: str, filename: str, expires_in: int = 600
    ) -> str: ...
