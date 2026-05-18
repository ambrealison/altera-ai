"""Report export domain model with Altera approval lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from altera_api.domain.common import DomainBase


class ReportApprovalStatus(StrEnum):
    """Approval state of a report export.

    Lifecycle: draft → under_review → approved → delivered
                              ↓ rejected (from draft or under_review)

    Clients may only download exports with status ``approved`` or ``delivered``.
    Draft, under_review, and rejected reports are visible to Altera staff only.
    """

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DELIVERED = "delivered"


class ReviewOwnerType(StrEnum):
    """Who owns the manual-review queue for this project.

    V1 is always ``altera_internal``. The column exists so a future
    tier can opt clients into self-service review.
    """

    ALTERA_INTERNAL = "altera_internal"


class ReportExport(DomainBase):
    """A generated report artefact (CSV, JSON, or Markdown).

    Clients may only access this when ``approval_status == 'approved'``.
    """

    id: UUID
    project_id: UUID
    organisation_id: UUID
    run_id: UUID
    format: str
    storage_path: str | None = None
    approval_status: ReportApprovalStatus = ReportApprovalStatus.DRAFT
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    release_note: str | None = None
    delivered_to_client_at: datetime | None = None
    created_at: datetime
