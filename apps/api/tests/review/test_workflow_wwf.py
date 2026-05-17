from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from altera_api.domain.common import ClassificationSource
from altera_api.domain.review import (
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewStatus,
)
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.review.errors import IllegalTransitionError
from altera_api.review.workflow import (
    accept_wwf_item,
    change_wwf_item,
    claim_item,
)


def _target_fg2_cheese(
    product_id: UUID, now: datetime
) -> WWFProductClassification:
    """Reviewer-proposed target — source/confidence will be overridden."""
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=WWFFoodGroup.FG2,
        wwf_is_composite=False,
        fg2_subgroup=WWFFG2Subgroup.CHEESE,
        source=ClassificationSource.MANUAL_REVIEW,
        confidence=Decimal("1"),
        updated_at=now,
        reviewer_user_id=UUID("00000000-0000-0000-0000-000000000999"),
    )


class TestAcceptWWF:
    def test_accept(
        self,
        wwf_item_in_queue: ManualReviewItem,
        wwf_current_fg1_red_meat: WWFProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        claimed = claim_item(wwf_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = accept_wwf_item(
            claimed,
            current=wwf_current_fg1_red_meat,
            reviewer_user_id=reviewer_a,
            reason="ok",
            now=now,
        )
        assert outcome.item.status is ManualReviewStatus.ACCEPTED
        assert outcome.wwf_classification is not None
        assert outcome.wwf_classification.wwf_food_group is WWFFoodGroup.FG1
        assert outcome.wwf_classification.fg1_subgroup is WWFFG1Subgroup.RED_MEAT
        assert outcome.wwf_classification.source is ClassificationSource.MANUAL_REVIEW
        assert outcome.wwf_classification.confidence == Decimal("1")
        assert outcome.decision.from_category == "FG1"
        assert outcome.decision.to_category == "FG1"


class TestChangeWWF:
    def test_change_to_fg2_cheese(
        self,
        wwf_item_in_queue: ManualReviewItem,
        wwf_current_fg1_red_meat: WWFProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        target = _target_fg2_cheese(wwf_item_in_queue.product_id, now)
        claimed = claim_item(wwf_item_in_queue, reviewer_user_id=reviewer_a, now=now)
        outcome = change_wwf_item(
            claimed,
            current=wwf_current_fg1_red_meat,
            target=target,
            reviewer_user_id=reviewer_a,
            reason="cheese not red meat",
            now=now,
        )
        assert outcome.item.status is ManualReviewStatus.CHANGED
        assert outcome.decision.decision is ManualReviewDecisionType.CHANGED
        assert outcome.decision.from_category == "FG1"
        assert outcome.decision.to_category == "FG2"
        assert outcome.wwf_classification is not None
        assert outcome.wwf_classification.fg2_subgroup is WWFFG2Subgroup.CHEESE
        assert outcome.wwf_classification.source is ClassificationSource.MANUAL_REVIEW
        assert outcome.wwf_classification.reviewer_user_id == reviewer_a
        # AI metadata stripped if any rode on the target.
        assert outcome.wwf_classification.ai_prompt_version is None
        assert outcome.wwf_classification.ai_model is None

    def test_change_same_category_rejected(
        self,
        wwf_item_in_queue: ManualReviewItem,
        wwf_current_fg1_red_meat: WWFProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        # Target identical to current → reject; use accept_wwf_item instead.
        with pytest.raises(IllegalTransitionError, match="different category"):
            change_wwf_item(
                wwf_item_in_queue,
                current=wwf_current_fg1_red_meat,
                target=wwf_current_fg1_red_meat,
                reviewer_user_id=reviewer_a,
                now=now,
            )

    def test_change_to_system_state_rejected(
        self,
        wwf_item_in_queue: ManualReviewItem,
        wwf_current_fg1_red_meat: WWFProductClassification,
        reviewer_a: UUID,
        now: datetime,
    ) -> None:
        target = WWFProductClassification(
            product_id=wwf_item_in_queue.product_id,
            wwf_food_group=WWFFoodGroup.OUT_OF_SCOPE,
            wwf_is_composite=False,
            source=ClassificationSource.MANUAL_REVIEW,
            confidence=Decimal("1"),
            reviewer_user_id=reviewer_a,
            updated_at=now,
        )
        with pytest.raises(IllegalTransitionError, match="system states"):
            change_wwf_item(
                wwf_item_in_queue,
                current=wwf_current_fg1_red_meat,
                target=target,
                reviewer_user_id=reviewer_a,
                now=now,
            )
