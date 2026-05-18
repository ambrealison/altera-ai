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
    ManualReviewPriority,
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


def filter_by_priority(
    items: Iterable[ManualReviewItem],
    *,
    priority: ManualReviewPriority,
) -> tuple[ManualReviewItem, ...]:
    """Keep only items whose computed priority matches ``priority``."""
    from altera_api.review.priority import assign_priority

    return tuple(item for item in items if assign_priority(item.reason)[0] is priority)


def sort_by_priority(
    items: Iterable[ManualReviewItem],
    *,
    highest_first: bool = True,
) -> tuple[ManualReviewItem, ...]:
    """Sort by priority weight, then by ``queued_at`` as a stable tiebreaker."""
    from altera_api.review.priority import assign_priority, priority_weight

    def _key(item: ManualReviewItem) -> tuple[int, object, str]:
        w = priority_weight(assign_priority(item.reason)[0])
        age = item.queued_at
        return (-w if highest_first else w, age, str(item.product_id))

    return tuple(sorted(items, key=_key))
