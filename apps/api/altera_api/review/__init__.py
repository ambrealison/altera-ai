"""Manual-review workflow.

Pure-Python logic over the domain models from
:mod:`altera_api.domain.review`. No database, no HTTP — those land in
later phases. This module provides:

* **Routing** — turn a deterministic-rules verdict or AI verdict into a
  ``ManualReviewItem`` when one is needed.
* **Locks** — 15-minute soft-lock semantics (as specified in
  ``docs/classification/review.md``).
* **State machine** — claim / accept / change / defer transitions with
  guards on the current item state and the active lock.
* **Bulk** — apply a single change to a batch of same-methodology
  items, emitting one ``ManualReviewDecision`` per item.
* **Queue helpers** — in-memory filter/sort helpers used by the
  reviewer UI layer.

Every state-changing call returns a frozen :class:`ReviewOutcome`
carrying the updated item, the audit-trail decision, and (where
applicable) the new methodology-specific classification with
``source=MANUAL_REVIEW``.
"""
from __future__ import annotations

from altera_api.review.bulk import (
    BulkChangeRequestPT,
    BulkChangeRequestWWF,
    bulk_change_pt,
    bulk_change_wwf,
)
from altera_api.review.errors import (
    IllegalTransitionError,
    MethodologyMismatchError,
    ReviewError,
    SoftLockHeldError,
)
from altera_api.review.locks import (
    SOFT_LOCK_DURATION,
    is_lock_expired,
    is_lock_held_by_other,
)
from altera_api.review.queue import filter_queue, sort_queue_by_age
from altera_api.review.routing import route_pt_verdict, route_wwf_verdict
from altera_api.review.workflow import (
    ReviewOutcome,
    accept_pt_item,
    accept_wwf_item,
    change_pt_item,
    change_wwf_item,
    claim_item,
    defer_item,
    reopen_after_defer,
)

__all__ = [
    "BulkChangeRequestPT",
    "BulkChangeRequestWWF",
    "IllegalTransitionError",
    "MethodologyMismatchError",
    "ReviewError",
    "ReviewOutcome",
    "SOFT_LOCK_DURATION",
    "SoftLockHeldError",
    "accept_pt_item",
    "accept_wwf_item",
    "bulk_change_pt",
    "bulk_change_wwf",
    "change_pt_item",
    "change_wwf_item",
    "claim_item",
    "defer_item",
    "filter_queue",
    "is_lock_expired",
    "is_lock_held_by_other",
    "reopen_after_defer",
    "route_pt_verdict",
    "route_wwf_verdict",
    "sort_queue_by_age",
]
