"""Queue filtering and sorting helpers.

These operate over an in-memory collection of items. The persistence
layer (later phase) will mirror the same filter shape as SQL ``WHERE``
clauses against ``review_queue``; keeping the predicates as pure
Python here lets the orchestration tests run without a DB.
"""
from __future__ import annotations

from collections.abc import Iterable

from altera_api.domain.common import Methodology
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)


def filter_queue(
    items: Iterable[ManualReviewItem],
    *,
    methodology: Methodology | None = None,
    status: ManualReviewStatus | None = None,
    reason: ManualReviewQueueReason | None = None,
) -> tuple[ManualReviewItem, ...]:
    """Filter by methodology / status / reason. ``None`` means "any"."""

    def _ok(item: ManualReviewItem) -> bool:
        if methodology is not None and item.methodology is not methodology:
            return False
        if status is not None and item.status is not status:
            return False
        if reason is not None and item.reason is not reason:
            return False
        return True

    return tuple(item for item in items if _ok(item))


def sort_queue_by_age(
    items: Iterable[ManualReviewItem],
    *,
    oldest_first: bool = True,
) -> tuple[ManualReviewItem, ...]:
    """Order items by ``queued_at``. Tiebreaker is ``product_id`` (stable)."""
    return tuple(
        sorted(
            items,
            key=lambda i: (i.queued_at, str(i.product_id)),
            reverse=not oldest_first,
        )
    )
