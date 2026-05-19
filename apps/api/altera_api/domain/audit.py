"""Audit-log event types and entries.

The action vocabulary mirrors docs/saas/audit-logs.md. Events are
immutable: a correction is a new event, never an in-place edit.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import DomainBase, NonEmptyStr


class AuditEventType(StrEnum):
    """The closed set of audit actions Altera AI emits."""

    # Organisation lifecycle
    ORG_CREATED = "organisation.created"
    ORG_MEMBER_INVITED = "organisation.member_invited"
    ORG_ROLE_CHANGED = "organisation.role_changed"
    ORG_MEMBER_REMOVED = "organisation.member_removed"

    # Project lifecycle
    PROJECT_CREATED = "project.created"

    # Upload lifecycle
    UPLOAD_CREATED = "upload.created"
    UPLOAD_DROPPED_COLUMNS = "upload.dropped_columns"

    # Classification batch lifecycle
    CLASSIFICATION_BATCH_STARTED = "classification.batch_started"
    CLASSIFICATION_BATCH_FINISHED = "classification.batch_finished"

    # Run lifecycle
    RUN_CREATED = "run.created"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"

    # Export lifecycle (Phase 20)
    EXPORT_GENERATED = "export.generated"
    EXPORT_SUBMITTED_FOR_REVIEW = "export.submitted_for_review"
    EXPORT_APPROVED = "export.approved"
    EXPORT_REJECTED = "export.rejected"
    EXPORT_DELIVERED = "export.delivered"
    EXPORT_DOWNLOADED = "export.downloaded"

    # Auth
    AUTH_SIGNED_IN = "auth.signed_in"

    # PT validation lifecycle
    PT_VALIDATION_SUBMITTED = "pt_validation.submitted"
    PT_VALIDATION_VALIDATED = "pt_validation.validated"

    # Hardened-policy guards
    COMMERCIAL_DATA_BLOCK = "commercial_data_block"

    # Job lifecycle (Phase 16)
    JOB_CREATED = "job.created"
    JOB_STARTED = "job.started"
    JOB_SUCCEEDED = "job.succeeded"
    JOB_FAILED = "job.failed"
    JOB_RETRYING = "job.retrying"
    JOB_CANCELLED = "job.cancelled"

    # Review decisions (Phase 19C)
    REVIEW_DECISION_MADE = "review.decision_made"
    REVIEW_BULK_ACTION = "review.bulk_action"

    # Recommendation lifecycle (Phase 25B)
    RECOMMENDATION_GENERATED = "recommendation.generated"
    RECOMMENDATION_PROPOSED = "recommendation.proposed"
    RECOMMENDATION_ACCEPTED = "recommendation.accepted"
    RECOMMENDATION_DISMISSED = "recommendation.dismissed"
    RECOMMENDATION_ARCHIVED = "recommendation.archived"

    # Scenario lifecycle (Phase 26A)
    SCENARIO_RUN = "scenario.run"

    # Comparison lifecycle (Phase 27A)
    COMPARISON_REQUESTED = "comparison.requested"

    # Nutrition enrichment lifecycle (Phase 23A)
    ENRICHMENT_APPLIED = "enrichment.applied"


_SYSTEM_EVENT_TYPES = frozenset(
    {
        AuditEventType.RUN_SUCCEEDED,
        AuditEventType.RUN_FAILED,
        AuditEventType.COMMERCIAL_DATA_BLOCK,
        # Job lifecycle events are emitted by the worker, not the user.
        AuditEventType.JOB_STARTED,
        AuditEventType.JOB_SUCCEEDED,
        AuditEventType.JOB_FAILED,
        AuditEventType.JOB_RETRYING,
    }
)


class AuditEvent(DomainBase):
    """A single immutable audit-log row.

    `actor_user_id` is null for system-emitted events (e.g. a run that
    finishes asynchronously, or the outbound-HTTP guard firing).
    `metadata` is an event-type-specific JSON object — its shape is
    validated at write time by the application layer, not here.
    """

    id: UUID
    organisation_id: UUID
    actor_user_id: UUID | None = None
    action: AuditEventType
    target_table: NonEmptyStr | None = None
    target_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def _system_events_have_no_actor(self) -> Self:
        if self.action in _SYSTEM_EVENT_TYPES and self.actor_user_id is not None:
            raise ValueError(f"{self.action.value} is a system event; actor_user_id must be null.")
        if self.action not in _SYSTEM_EVENT_TYPES and self.actor_user_id is None:
            raise ValueError(f"{self.action.value} requires actor_user_id.")
        return self

    @model_validator(mode="after")
    def _commercial_data_block_carries_field_name(self) -> Self:
        if (
            self.action is AuditEventType.COMMERCIAL_DATA_BLOCK
            and "field_name" not in self.metadata
        ):
            raise ValueError(
                "commercial_data_block events must carry the offending field_name in metadata."
            )
        return self
