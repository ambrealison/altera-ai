"""Priority assignment for manual review items.

Priority is derived solely from the queue reason — no commercial fields
are accessed, making it safe to expose to Altera reviewers.

``assign_priority`` is a pure function: given a queue reason it returns
a ``(priority_level, priority_reasons)`` pair.
"""
from __future__ import annotations

from altera_api.domain.review import ManualReviewPriority, ManualReviewQueueReason

# Maps each queue reason to (priority, tuple-of-string-reasons).
_REASON_MAP: dict[ManualReviewQueueReason, tuple[ManualReviewPriority, tuple[str, ...]]] = {
    ManualReviewQueueReason.CONTRADICTION_DETECTED: (
        ManualReviewPriority.CRITICAL,
        ("contradiction_detected",),
    ),
    ManualReviewQueueReason.AI_PARSE_FAILED: (
        ManualReviewPriority.CRITICAL,
        ("ai_parse_failed",),
    ),
    ManualReviewQueueReason.AI_PROVIDER_ERROR: (
        ManualReviewPriority.CRITICAL,
        ("ai_provider_error",),
    ),
    ManualReviewQueueReason.RULE_COLLISION: (
        ManualReviewPriority.HIGH,
        ("rule_collision",),
    ),
    ManualReviewQueueReason.LOW_CONFIDENCE: (
        ManualReviewPriority.MEDIUM,
        ("low_confidence",),
    ),
    ManualReviewQueueReason.REQUESTED: (
        ManualReviewPriority.LOW,
        (),
    ),
}

_WEIGHT: dict[ManualReviewPriority, int] = {
    ManualReviewPriority.CRITICAL: 3,
    ManualReviewPriority.HIGH: 2,
    ManualReviewPriority.MEDIUM: 1,
    ManualReviewPriority.LOW: 0,
}


def assign_priority(
    reason: ManualReviewQueueReason,
) -> tuple[ManualReviewPriority, tuple[str, ...]]:
    """Return ``(priority_level, priority_reasons)`` for a review queue reason."""
    return _REASON_MAP.get(reason, (ManualReviewPriority.LOW, ()))


def priority_weight(priority: ManualReviewPriority) -> int:
    """Numeric weight for sorting: higher = more urgent."""
    return _WEIGHT.get(priority, 0)
