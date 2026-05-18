from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.audit import AuditEvent, AuditEventType
from altera_api.domain.common import Methodology
from altera_api.domain.report_exports import ReviewOwnerType
from altera_api.domain.review import (
    ManualReviewDecision,
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)


class TestManualReviewItem:
    def test_in_queue_no_soft_lock(self, product_id: UUID, now: datetime) -> None:
        item = ManualReviewItem(
            product_id=product_id,
            methodology=Methodology.WWF,
            status=ManualReviewStatus.IN_QUEUE,
            reason=ManualReviewQueueReason.LOW_CONFIDENCE,
            queued_at=now,
        )
        assert item.soft_lock_user_id is None

    def test_reviewing_requires_soft_lock(self, product_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            ManualReviewItem(
                product_id=product_id,
                methodology=Methodology.WWF,
                status=ManualReviewStatus.REVIEWING,
                reason=ManualReviewQueueReason.AI_PARSE_FAILED,
                queued_at=now,
            )

    def test_reviewing_with_soft_lock_ok(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        item = ManualReviewItem(
            product_id=product_id,
            methodology=Methodology.WWF,
            status=ManualReviewStatus.REVIEWING,
            reason=ManualReviewQueueReason.LOW_CONFIDENCE,
            queued_at=now,
            soft_lock_user_id=user_id,
            soft_lock_expires_at=now + timedelta(minutes=15),
        )
        assert item.soft_lock_user_id == user_id

    def test_in_queue_with_soft_lock_rejected(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            ManualReviewItem(
                product_id=product_id,
                methodology=Methodology.WWF,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.REQUESTED,
                queued_at=now,
                soft_lock_user_id=user_id,
                soft_lock_expires_at=now + timedelta(minutes=15),
            )

    def test_soft_lock_fields_must_pair(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            ManualReviewItem(
                product_id=product_id,
                methodology=Methodology.WWF,
                status=ManualReviewStatus.REVIEWING,
                reason=ManualReviewQueueReason.LOW_CONFIDENCE,
                queued_at=now,
                soft_lock_user_id=user_id,
                # soft_lock_expires_at missing
            )

    def test_terminal_status_helpers(self, product_id: UUID, now: datetime) -> None:
        for s in (
            ManualReviewStatus.ACCEPTED,
            ManualReviewStatus.CHANGED,
            ManualReviewStatus.DEFERRED,
        ):
            assert s.is_terminal
        assert not ManualReviewStatus.IN_QUEUE.is_terminal

    def test_owner_type_defaults_to_altera_internal(self, product_id: UUID, now: datetime) -> None:
        item = ManualReviewItem(
            product_id=product_id,
            methodology=Methodology.PROTEIN_TRACKER,
            status=ManualReviewStatus.IN_QUEUE,
            reason=ManualReviewQueueReason.LOW_CONFIDENCE,
            queued_at=now,
        )
        assert item.owner_type is ReviewOwnerType.ALTERA_INTERNAL


class TestManualReviewDecision:
    def test_changed_decision_requires_different_categories(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            ManualReviewDecision(
                id=UUID("00000000-0000-0000-0000-000000007777"),
                product_id=product_id,
                methodology=Methodology.PROTEIN_TRACKER,
                decision=ManualReviewDecisionType.CHANGED,
                reviewer_user_id=user_id,
                from_category="plant_based_core",
                to_category="plant_based_core",
                created_at=now,
            )

    def test_accepted_decision_requires_to_category(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            ManualReviewDecision(
                id=UUID("00000000-0000-0000-0000-000000007778"),
                product_id=product_id,
                methodology=Methodology.WWF,
                decision=ManualReviewDecisionType.ACCEPTED,
                reviewer_user_id=user_id,
                from_category="FG1",
                to_category=None,
                created_at=now,
            )

    def test_deferred_allows_missing_to_category(
        self, product_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        d = ManualReviewDecision(
            id=UUID("00000000-0000-0000-0000-000000007779"),
            product_id=product_id,
            methodology=Methodology.WWF,
            decision=ManualReviewDecisionType.DEFERRED,
            reviewer_user_id=user_id,
            from_category="unknown",
            to_category=None,
            reason="awaiting nutrition panel",
            created_at=now,
        )
        assert d.decision is ManualReviewDecisionType.DEFERRED


class TestAuditEvent:
    def _base(self, org_id: UUID, user_id: UUID, now: datetime) -> dict:
        return dict(
            id=UUID("00000000-0000-0000-0000-00000000ee01"),
            organisation_id=org_id,
            actor_user_id=user_id,
            action=AuditEventType.PROJECT_CREATED,
            target_table="projects",
            target_id=UUID("00000000-0000-0000-0000-00000000bb01"),
            metadata={"name": "FY 2024"},
            created_at=now,
        )

    def test_user_event_requires_actor(self, org_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            AuditEvent(
                id=UUID("00000000-0000-0000-0000-00000000ee02"),
                organisation_id=org_id,
                actor_user_id=None,
                action=AuditEventType.PROJECT_CREATED,
                metadata={},
                created_at=now,
            )

    def test_user_event_with_actor_ok(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        e = AuditEvent(**self._base(org_id, user_id, now))
        assert e.action is AuditEventType.PROJECT_CREATED

    def test_system_event_must_have_no_actor(
        self, org_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            AuditEvent(
                id=UUID("00000000-0000-0000-0000-00000000ee03"),
                organisation_id=org_id,
                actor_user_id=user_id,
                action=AuditEventType.RUN_SUCCEEDED,
                metadata={"run_id": "00000000-0000-0000-0000-000000005555"},
                created_at=now,
            )

    def test_commercial_data_block_requires_field_name(self, org_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            AuditEvent(
                id=UUID("00000000-0000-0000-0000-00000000ee04"),
                organisation_id=org_id,
                actor_user_id=None,
                action=AuditEventType.COMMERCIAL_DATA_BLOCK,
                metadata={},  # no field_name
                created_at=now,
            )

    def test_commercial_data_block_with_field_name_ok(self, org_id: UUID, now: datetime) -> None:
        e = AuditEvent(
            id=UUID("00000000-0000-0000-0000-00000000ee05"),
            organisation_id=org_id,
            actor_user_id=None,
            action=AuditEventType.COMMERCIAL_DATA_BLOCK,
            metadata={"field_name": "supplier_id", "upload_id": "abc"},
            created_at=datetime(2026, 5, 15, tzinfo=UTC),
        )
        assert e.metadata["field_name"] == "supplier_id"
