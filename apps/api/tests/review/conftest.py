"""Shared fixtures for review-workflow tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)


def _ids(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def reviewer_a() -> UUID:
    return _ids(0xA)


@pytest.fixture
def reviewer_b() -> UUID:
    return _ids(0xB)


@pytest.fixture
def pt_product_id() -> UUID:
    return _ids(1)


@pytest.fixture
def wwf_product_id() -> UUID:
    return _ids(2)


@pytest.fixture
def pt_item_in_queue(pt_product_id: UUID, now: datetime) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=pt_product_id,
        methodology=Methodology.PROTEIN_TRACKER,
        status=ManualReviewStatus.IN_QUEUE,
        reason=ManualReviewQueueReason.LOW_CONFIDENCE,
        queued_at=now - timedelta(minutes=5),
    )


@pytest.fixture
def wwf_item_in_queue(wwf_product_id: UUID, now: datetime) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=wwf_product_id,
        methodology=Methodology.WWF,
        status=ManualReviewStatus.IN_QUEUE,
        reason=ManualReviewQueueReason.RULE_COLLISION,
        queued_at=now - timedelta(minutes=2),
    )


@pytest.fixture
def pt_current_animal_core(
    pt_product_id: UUID, now: datetime
) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=pt_product_id,
        pt_group=ProteinTrackerGroup.ANIMAL_CORE,
        source=ClassificationSource.AI,
        confidence=Decimal("0.6"),
        ai_prompt_version="classifier_v1",
        ai_model="fake-model",
        updated_at=now - timedelta(minutes=3),
    )


@pytest.fixture
def wwf_current_fg1_red_meat(
    wwf_product_id: UUID, now: datetime
) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=wwf_product_id,
        wwf_food_group=WWFFoodGroup.FG1,
        wwf_is_composite=False,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        source=ClassificationSource.AI,
        confidence=Decimal("0.6"),
        ai_prompt_version="classifier_v1",
        ai_model="fake-model",
        updated_at=now - timedelta(minutes=3),
    )
