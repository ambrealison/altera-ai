from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.review.queue import filter_queue, sort_queue_by_age


def _item(
    product_idx: int,
    *,
    methodology: Methodology,
    status: ManualReviewStatus,
    reason: ManualReviewQueueReason,
    queued_at: datetime,
) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=UUID(f"00000000-0000-0000-0000-{product_idx:012d}"),
        methodology=methodology,
        status=status,
        reason=reason,
        queued_at=queued_at,
    )


def test_filter_by_methodology(now: datetime) -> None:
    items = [
        _item(1, methodology=Methodology.PROTEIN_TRACKER, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now),
        _item(2, methodology=Methodology.WWF, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now),
    ]
    pts = filter_queue(items, methodology=Methodology.PROTEIN_TRACKER)
    assert len(pts) == 1 and pts[0].methodology is Methodology.PROTEIN_TRACKER


def test_filter_by_reason_and_status(now: datetime) -> None:
    items = [
        _item(1, methodology=Methodology.PROTEIN_TRACKER, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now),
        _item(2, methodology=Methodology.PROTEIN_TRACKER, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.RULE_COLLISION, queued_at=now),
        _item(3, methodology=Methodology.PROTEIN_TRACKER, status=ManualReviewStatus.ACCEPTED, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now),
    ]
    out = filter_queue(
        items,
        status=ManualReviewStatus.IN_QUEUE,
        reason=ManualReviewQueueReason.LOW_CONFIDENCE,
    )
    assert len(out) == 1
    assert out[0].product_id == UUID("00000000-0000-0000-0000-000000000001")


def test_filter_no_filters_returns_all(now: datetime) -> None:
    items = [
        _item(i, methodology=Methodology.WWF, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now)
        for i in range(3)
    ]
    assert len(filter_queue(items)) == 3


def test_sort_oldest_first(now: datetime) -> None:
    items = [
        _item(1, methodology=Methodology.WWF, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now),
        _item(2, methodology=Methodology.WWF, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now - timedelta(hours=1)),
        _item(3, methodology=Methodology.WWF, status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.LOW_CONFIDENCE, queued_at=now - timedelta(hours=2)),
    ]
    out = sort_queue_by_age(items, oldest_first=True)
    assert [i.product_id.int & 0xF for i in out] == [3, 2, 1]
    out = sort_queue_by_age(items, oldest_first=False)
    assert [i.product_id.int & 0xF for i in out] == [1, 2, 3]
