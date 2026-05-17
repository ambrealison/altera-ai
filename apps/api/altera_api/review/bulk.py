"""Bulk-operation helpers.

A reviewer can select multiple items in the queue and apply a single
category to all of them, provided every selected item is for the same
methodology. Each item still receives an individual
``ManualReviewDecision`` for the audit trail.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import ManualReviewItem
from altera_api.domain.wwf import WWFProductClassification
from altera_api.review.errors import MethodologyMismatchError
from altera_api.review.workflow import ReviewOutcome, change_pt_item, change_wwf_item


@dataclass(frozen=True)
class BulkChangeRequestPT:
    """Apply the same PT group to a batch of items."""

    items: tuple[ManualReviewItem, ...]
    to_group: ProteinTrackerGroup
    current_by_product: Mapping[UUID, ProteinTrackerProductClassification] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BulkChangeRequestWWF:
    """Apply the same WWF classification to a batch of items."""

    items: tuple[ManualReviewItem, ...]
    target: WWFProductClassification
    current_by_product: Mapping[UUID, WWFProductClassification] | None = None
    reason: str | None = None


def _assert_single_methodology(
    items: Sequence[ManualReviewItem], expected: Methodology
) -> None:
    for item in items:
        if item.methodology is not expected:
            raise MethodologyMismatchError(
                f"bulk operation expected methodology={expected.value}, "
                f"got an item with methodology={item.methodology.value}."
            )


def bulk_change_pt(
    request: BulkChangeRequestPT,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> tuple[ReviewOutcome, ...]:
    """Apply ``request.to_group`` to every item in ``request.items``.

    Items that already carry ``to_group`` are accepted (not changed) so
    the per-item state machine guards still hold.
    """
    _assert_single_methodology(request.items, Methodology.PROTEIN_TRACKER)
    outcomes: list[ReviewOutcome] = []
    for item in request.items:
        current = (
            request.current_by_product.get(item.product_id)
            if request.current_by_product is not None
            else None
        )
        outcomes.append(
            change_pt_item(
                item,
                current=current,
                to_group=request.to_group,
                reviewer_user_id=reviewer_user_id,
                reason=request.reason,
                now=now,
            )
        )
    return tuple(outcomes)


def bulk_change_wwf(
    request: BulkChangeRequestWWF,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> tuple[ReviewOutcome, ...]:
    _assert_single_methodology(request.items, Methodology.WWF)
    outcomes: list[ReviewOutcome] = []
    for item in request.items:
        current = (
            request.current_by_product.get(item.product_id)
            if request.current_by_product is not None
            else None
        )
        outcomes.append(
            change_wwf_item(
                item,
                current=current,
                target=request.target,
                reviewer_user_id=reviewer_user_id,
                reason=request.reason,
                now=now,
            )
        )
    return tuple(outcomes)
