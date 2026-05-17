from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewStatus,
)
from altera_api.review.errors import IllegalTransitionError, SoftLockHeldError
from altera_api.review.locks import (
    SOFT_LOCK_DURATION,
    is_lock_expired,
    is_lock_held_by_other,
)
from altera_api.review.workflow import claim_item


class TestLockQueries:
    def test_no_lock_not_expired(
        self, pt_item_in_queue: ManualReviewItem, now: datetime
    ) -> None:
        assert is_lock_expired(pt_item_in_queue, now=now) is False

    def test_unexpired_lock(
        self, pt_item_in_queue: ManualReviewItem, reviewer_a: UUID, now: datetime
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        assert is_lock_expired(claimed, now=now) is False
        assert is_lock_expired(claimed, now=now + timedelta(minutes=14)) is False

    def test_expired_lock(
        self, pt_item_in_queue: ManualReviewItem, reviewer_a: UUID, now: datetime
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        assert is_lock_expired(claimed, now=now + SOFT_LOCK_DURATION) is True
        assert is_lock_expired(claimed, now=now + timedelta(minutes=30)) is True

    def test_held_by_other(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        reviewer_b: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        assert is_lock_held_by_other(claimed, reviewer_user_id=reviewer_b, now=now) is True
        assert is_lock_held_by_other(claimed, reviewer_user_id=reviewer_a, now=now) is False

    def test_expired_lock_not_held_by_other(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        reviewer_b: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        assert (
            is_lock_held_by_other(
                claimed,
                reviewer_user_id=reviewer_b,
                now=now + SOFT_LOCK_DURATION,
            )
            is False
        )


class TestClaim:
    def test_claim_from_in_queue(
        self, pt_item_in_queue: ManualReviewItem, reviewer_a: UUID, now: datetime
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        assert claimed.status is ManualReviewStatus.REVIEWING
        assert claimed.soft_lock_user_id == reviewer_a
        assert claimed.soft_lock_expires_at == now + SOFT_LOCK_DURATION

    def test_other_user_blocked_during_lock(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        reviewer_b: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        with pytest.raises(SoftLockHeldError):
            claim_item(claimed, reviewer_user_id=reviewer_b, now=now)

    def test_other_user_can_steal_expired_lock(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        reviewer_b: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        # Same reviewer still works
        same = claim_item(
            claimed, reviewer_user_id=reviewer_a, now=now + timedelta(minutes=5)
        )
        assert same.soft_lock_user_id == reviewer_a
        # After 15 minutes, B can claim
        later = claim_item(
            claimed, reviewer_user_id=reviewer_b, now=now + SOFT_LOCK_DURATION
        )
        assert later.soft_lock_user_id == reviewer_b

    def test_cannot_claim_terminal(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        terminated = pt_item_in_queue.model_copy(
            update={"status": ManualReviewStatus.ACCEPTED}
        )
        with pytest.raises(IllegalTransitionError):
            claim_item(terminated, reviewer_user_id=reviewer_a, now=now)
