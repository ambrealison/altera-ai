from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.review.bulk import (
    BulkChangeRequestPT,
    bulk_change_pt,
)
from altera_api.review.errors import MethodologyMismatchError


def _pt_item(product_idx: int, now: datetime) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=UUID(f"00000000-0000-0000-0000-{product_idx:012d}"),
        methodology=Methodology.PROTEIN_TRACKER,
        status=ManualReviewStatus.IN_QUEUE,
        reason=ManualReviewQueueReason.LOW_CONFIDENCE,
        queued_at=now,
    )


def _pt_current(product_idx: int, now: datetime) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=UUID(f"00000000-0000-0000-0000-{product_idx:012d}"),
        pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
        source=ClassificationSource.AI,
        confidence=Decimal("0.5"),
        ai_prompt_version="v1",
        ai_model="m",
        updated_at=now,
    )


class TestBulkPT:
    def test_applies_same_category_to_each(self, reviewer_a: UUID, now: datetime) -> None:
        items = tuple(_pt_item(i, now) for i in range(1, 4))
        currents = {_pt_current(i, now).product_id: _pt_current(i, now) for i in range(1, 4)}
        req = BulkChangeRequestPT(
            items=items,
            to_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            current_by_product=currents,
            reason="all ready meals",
        )
        outcomes = bulk_change_pt(req, reviewer_user_id=reviewer_a, now=now)
        assert len(outcomes) == 3
        for o in outcomes:
            assert o.item.status is ManualReviewStatus.CHANGED
            assert o.pt_classification is not None
            assert o.pt_classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
            assert o.decision.to_category == "composite_products"
            assert o.decision.reason == "all ready meals"

    def test_each_item_gets_individual_decision(self, reviewer_a: UUID, now: datetime) -> None:
        items = tuple(_pt_item(i, now) for i in (5, 6))
        req = BulkChangeRequestPT(
            items=items,
            to_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        )
        outcomes = bulk_change_pt(req, reviewer_user_id=reviewer_a, now=now)
        decision_ids = {o.decision.id for o in outcomes}
        assert len(decision_ids) == 2  # distinct ids

    def test_mixed_methodology_rejected(self, reviewer_a: UUID, now: datetime) -> None:
        bad = ManualReviewItem(
            product_id=UUID("00000000-0000-0000-0000-000000000099"),
            methodology=Methodology.WWF,
            status=ManualReviewStatus.IN_QUEUE,
            reason=ManualReviewQueueReason.LOW_CONFIDENCE,
            queued_at=now,
        )
        req = BulkChangeRequestPT(
            items=(_pt_item(1, now), bad),
            to_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        )
        with pytest.raises(MethodologyMismatchError):
            bulk_change_pt(req, reviewer_user_id=reviewer_a, now=now)

    def test_no_currents_supplied_works(self, reviewer_a: UUID, now: datetime) -> None:
        # When current_by_product is None, from_category on each decision is None.
        req = BulkChangeRequestPT(
            items=(_pt_item(1, now), _pt_item(2, now)),
            to_group=ProteinTrackerGroup.ANIMAL_CORE,
        )
        outcomes = bulk_change_pt(req, reviewer_user_id=reviewer_a, now=now)
        assert all(o.decision.from_category is None for o in outcomes)
