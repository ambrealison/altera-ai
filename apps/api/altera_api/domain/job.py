"""Job domain model — tracks async pipeline work.

A Job represents a unit of background work (validation, ingestion,
classification, calculation, export). The in-process SyncDevRunner
executes jobs synchronously; future workers (Celery, RQ, Dramatiq)
pick up queued jobs from a broker.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from altera_api.domain.common import DomainBase


class JobType(StrEnum):
    VALIDATE_UPLOAD = "validate_upload"
    INGEST_UPLOAD = "ingest_upload"
    CLASSIFY_UPLOAD = "classify_upload"
    RUN_CALCULATION = "run_calculation"
    GENERATE_EXPORT = "generate_export"
    GENERATE_REPORT = "generate_report"  # placeholder for future phase


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class Job(DomainBase):
    """Immutable snapshot of one pipeline job."""

    job_id: UUID
    organisation_id: UUID
    project_id: UUID
    upload_id: UUID | None = None
    run_id: UUID | None = None
    job_type: JobType
    status: JobStatus = JobStatus.QUEUED
    progress_pct: int | None = None
    created_by: UUID
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0
    idempotency_key: str | None = None
    # Carries input parameters and, after completion, a "result" sub-key.
    payload: dict[str, Any] = Field(default_factory=dict)
