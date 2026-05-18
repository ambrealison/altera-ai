"""Manual review queue, status, and decision models."""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import model_validator

from altera_api.domain.common import DomainBase, Methodology, NonEmptyStr
from altera_api.domain.report_exports import ReviewOwnerType


class ManualReviewStatus(StrEnum):
    """State machine for one item in the review queue.

    Transitions: in_queue → reviewing → (accepted | changed | deferred).
    The reviewer-side validations live in the application layer; this
    enum is the persisted state of the item.
    """

    IN_QUEUE = "in_queue"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    CHANGED = "changed"
    DEFERRED = "deferred"

    @property
    def is_terminal(self) -> bool:
        return self in {
            ManualReviewStatus.ACCEPTED,
            ManualReviewStatus.CHANGED,
            ManualReviewStatus.DEFERRED,
        }


class ManualReviewQueueReason(StrEnum):
    """Why a product is in the review queue."""

    LOW_CONFIDENCE = "low_confidence"
    AI_PARSE_FAILED = "ai_parse_failed"
    AI_PROVIDER_ERROR = "ai_provider_error"
    RULE_COLLISION = "rule_collision"
    CONTRADICTION_DETECTED = "contradiction_detected"
    REQUESTED = "requested"


class ManualReviewDecisionType(StrEnum):
    """The reviewer's verdict on an item."""

    ACCEPTED = "accepted"
    CHANGED = "changed"
    DEFERRED = "deferred"


#: Soft-lock window once a reviewer opens an item; matches docs/classification/review.md.
SOFT_LOCK_DURATION = timedelta(minutes=15)


class ManualReviewItem(DomainBase):
    """One product awaiting (or being worked on by) a reviewer."""

    product_id: UUID
    methodology: Methodology
    status: ManualReviewStatus
    reason: ManualReviewQueueReason
    owner_type: ReviewOwnerType = ReviewOwnerType.ALTERA_INTERNAL
    queued_at: datetime
    soft_lock_user_id: UUID | None = None
    soft_lock_expires_at: datetime | None = None
    # Human-readable notes from the classifier that explain WHY this item is
    # in the queue. Populated for contradiction_detected (contradiction notes)
    # and rule_collision (conflicting rule IDs). Empty for other reasons.
    rationale_notes: tuple[str, ...] = ()
    # Phase 19D — assignment
    assigned_to_user_id: UUID | None = None

    @model_validator(mode="after")
    def _soft_lock_fields_paired(self) -> Self:
        if (self.soft_lock_user_id is None) != (self.soft_lock_expires_at is None):
            raise ValueError(
                "soft_lock_user_id and soft_lock_expires_at must be set together "
                "or both omitted."
            )
        return self

    @model_validator(mode="after")
    def _soft_lock_only_while_reviewing(self) -> Self:
        if self.status is ManualReviewStatus.REVIEWING and self.soft_lock_user_id is None:
            raise ValueError("status=reviewing requires a soft lock.")
        if self.status is not ManualReviewStatus.REVIEWING and self.soft_lock_user_id is not None:
            raise ValueError("soft lock may only be held while status=reviewing.")
        return self


class ManualReviewDecision(DomainBase):
    """The reviewer's recorded action.

    Each decision writes an immutable row to `classification_events`
    (modelled here) and updates the active classification's `source` to
    `manual_review`.
    """

    id: UUID
    product_id: UUID
    methodology: Methodology
    decision: ManualReviewDecisionType
    reviewer_user_id: UUID
    from_category: NonEmptyStr | None
    to_category: NonEmptyStr | None
    reason: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _to_category_required_when_changed_or_accepted(self) -> Self:
        if self.decision in {
            ManualReviewDecisionType.ACCEPTED,
            ManualReviewDecisionType.CHANGED,
        } and self.to_category is None:
            raise ValueError(
                "to_category is required when decision is accepted or changed."
            )
        if self.decision is ManualReviewDecisionType.CHANGED and self.from_category == self.to_category:
            raise ValueError(
                "decision=changed requires from_category != to_category."
            )
        return self
