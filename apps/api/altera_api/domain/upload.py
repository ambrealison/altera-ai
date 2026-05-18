"""Upload model + upload lifecycle status."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import DomainBase, NonEmptyStr


class UploadStatus(StrEnum):
    # Phase 15 lifecycle
    CREATED = "created"
    UPLOAD_URL_CREATED = "upload_url_created"
    UPLOADED_TO_STORAGE = "uploaded_to_storage"
    VALIDATION_PENDING = "validation_pending"
    VALIDATION_RUNNING = "validation_running"
    VALIDATION_FAILED = "validation_failed"
    VALIDATION_COMPLETED = "validation_completed"
    INGESTION_RUNNING = "ingestion_running"
    INGESTION_FAILED = "ingestion_failed"
    INGESTION_COMPLETED = "ingestion_completed"
    READY_FOR_CLASSIFICATION = "ready_for_classification"
    # Legacy values kept for backward compatibility with older records
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


#: Statuses where we expect row_count to be populated (processing completed).
_ROW_COUNT_REQUIRED = frozenset(
    {
        UploadStatus.VALIDATION_COMPLETED,
        UploadStatus.INGESTION_COMPLETED,
        UploadStatus.READY_FOR_CLASSIFICATION,
        UploadStatus.VALID,
        UploadStatus.INVALID,
    }
)


class Upload(DomainBase):
    id: UUID
    organisation_id: UUID
    project_id: UUID
    storage_path: NonEmptyStr
    original_filename: NonEmptyStr
    status: UploadStatus
    row_count: int | None = Field(default=None, ge=0)
    dropped_columns: tuple[str, ...] = ()
    # Phase 15 metadata
    content_type: str | None = None
    file_size_bytes: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = None
    validation_started_at: datetime | None = None
    validation_completed_at: datetime | None = None
    ingestion_started_at: datetime | None = None
    ingestion_completed_at: datetime | None = None
    uploaded_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _row_count_required_when_resolved(self) -> Self:
        if self.status in _ROW_COUNT_REQUIRED and self.row_count is None:
            raise ValueError("row_count must be set once an upload reaches a resolved status.")
        return self
