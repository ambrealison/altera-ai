"""Soft-lock query helpers.

A reviewer who opens an item is granted a soft lock for 15 minutes.
Other reviewers can still see the item but cannot submit a decision
until the lock expires. The lock semantics are entirely query-based —
no background timers, no cleanup jobs. Every state-changing call passes
``now`` so tests can drive the clock deterministically.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from altera_api.domain.review import SOFT_LOCK_DURATION, ManualReviewItem

__all__ = [
    "SOFT_LOCK_DURATION",
    "is_lock_expired",
    "is_lock_held_by_other",
]


def is_lock_expired(item: ManualReviewItem, *, now: datetime) -> bool:
    """True iff there is a soft lock and ``now`` is past its expiry.

    A missing lock is *not* expired — it simply doesn't exist.
    """
    if item.soft_lock_expires_at is None:
        return False
    return now >= item.soft_lock_expires_at


def is_lock_held_by_other(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> bool:
    """True iff the lock is unexpired and held by a different user."""
    if item.soft_lock_user_id is None:
        return False
    if item.soft_lock_user_id == reviewer_user_id:
        return False
    return not is_lock_expired(item, now=now)
