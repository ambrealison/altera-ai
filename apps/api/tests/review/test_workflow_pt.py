from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewStatus,
)
from altera_api.review.errors import IllegalTransitionError, SoftLockHeldError
from altera_api.review.workflow import (
    accept_pt_item,
    change_pt_item,
    claim_item,
    defer_item,
    reopen_after_defer,
)


class TestAcceptPT:
    def test_accepts_keeps_category_but_promotes_source(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = accept_pt_item(
            claimed,
            current=pt_current_animal_core,
            reviewer_user_id=reviewer_a,
            reason="checked",
            now=now + timedelta(minutes=2),
        )
        assert outcome.item.status is ManualReviewStatus.ACCEPTED
        assert outcome.item.soft_lock_user_id is None
        assert outcome.decision.decision is ManualReviewDecisionType.ACCEPTED
        assert outcome.decision.from_category == "animal_core"
        assert outcome.decision.to_category == "animal_core"
        assert outcome.pt_classification is not None
        new = outcome.pt_classification
        assert new.pt_group is ProteinTrackerGroup.ANIMAL_CORE
        assert new.source is ClassificationSource.MANUAL_REVIEW
        assert new.confidence == Decimal("1")
        assert new.reviewer_user_id == reviewer_a
        assert new.review_reason == "checked"
        # AI metadata is dropped on the new manual-review classification.
        assert new.ai_prompt_version is None
        assert new.ai_model is None

    def test_other_user_cannot_submit_under_lock(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        reviewer_b: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        with pytest.raises(SoftLockHeldError):
            accept_pt_item(
                claimed,
                current=pt_current_animal_core,
                reviewer_user_id=reviewer_b,
                now=now,
            )

    def test_cannot_accept_terminal(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        terminated = pt_item_in_queue.model_copy(update={"status": ManualReviewStatus.ACCEPTED})
        with pytest.raises(IllegalTransitionError):
            accept_pt_item(
                terminated,
                current=pt_current_animal_core,
                reviewer_user_id=reviewer_a,
                now=now,
            )

    def test_methodology_must_match(
        self,
        wwf_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        with pytest.raises(IllegalTransitionError, match="expected protein_tracker"):
            accept_pt_item(
                wwf_item_in_queue,
                current=pt_current_animal_core,
                reviewer_user_id=reviewer_a,
                now=now,
            )


class TestChangePT:
    def test_changes_category(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = change_pt_item(
            claimed,
            current=pt_current_animal_core,
            to_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            reviewer_user_id=reviewer_a,
            reason="ready meal",
            now=now,
        )
        assert outcome.item.status is ManualReviewStatus.CHANGED
        assert outcome.decision.decision is ManualReviewDecisionType.CHANGED
        assert outcome.decision.from_category == "animal_core"
        assert outcome.decision.to_category == "composite_products"
        assert outcome.pt_classification is not None
        assert outcome.pt_classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        assert outcome.pt_classification.source is ClassificationSource.MANUAL_REVIEW

    def test_same_category_rejected_for_change(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        with pytest.raises(IllegalTransitionError, match="different category"):
            change_pt_item(
                claimed,
                current=pt_current_animal_core,
                to_group=ProteinTrackerGroup.ANIMAL_CORE,
                reviewer_user_id=reviewer_a,
                now=now,
            )

    def test_cannot_change_to_system_state(
        self,
        pt_item_in_queue: ManualReviewItem,
        pt_current_animal_core: ProteinTrackerProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        with pytest.raises(IllegalTransitionError, match="system states"):
            change_pt_item(
                pt_item_in_queue,
                current=pt_current_animal_core,
                to_group=ProteinTrackerGroup.UNKNOWN,
                reviewer_user_id=reviewer_a,
                now=now,
            )

    def test_change_without_prior_classification(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        # AI parse-failed → no prior classification. The reviewer assigns one.
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = change_pt_item(
            claimed,
            current=None,
            to_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            reviewer_user_id=reviewer_a,
            now=now,
        )
        assert outcome.decision.from_category is None
        assert outcome.decision.to_category == "plant_based_core"


class TestDeferAndReopen:
    def test_defer_keeps_classification_untouched(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = defer_item(
            claimed,
            reviewer_user_id=reviewer_a,
            reason="awaiting nutrition data",
            now=now,
        )
        assert outcome.item.status is ManualReviewStatus.DEFERRED
        assert outcome.pt_classification is None
        assert outcome.wwf_classification is None
        assert outcome.decision.decision is ManualReviewDecisionType.DEFERRED
        assert outcome.decision.from_category is None
        assert outcome.decision.to_category is None

    def test_reopen_creates_fresh_in_queue_item(
        self,
        pt_item_in_queue: ManualReviewItem,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(pt_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = defer_item(claimed, reviewer_user_id=reviewer_a, now=now)
        new_item = reopen_after_defer(outcome.item, now=now + timedelta(days=1))
        assert new_item.status is ManualReviewStatus.IN_QUEUE
        assert new_item.methodology is Methodology.PROTEIN_TRACKER
        assert new_item.product_id == pt_item_in_queue.product_id
        assert new_item.soft_lock_user_id is None

    def test_reopen_rejects_non_deferred(
        self, pt_item_in_queue: ManualReviewItem, now: datetime
    ) -> None:
        with pytest.raises(IllegalTransitionError):
            reopen_after_defer(pt_item_in_queue, now=now)
