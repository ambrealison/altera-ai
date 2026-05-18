"""State-machine transitions for one review item.

Every state-changing helper returns a frozen :class:`ReviewOutcome`:

* the updated item (terminal status, lock cleared);
* a ``ManualReviewDecision`` for the audit trail;
* a new methodology-specific classification with ``source=MANUAL_REVIEW``
  (when applicable — deferrals don't change classification).

The helpers are pure functions: no mutation, no side effects, no I/O.
The orchestrator above this layer persists what they return.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewDecision,
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.wwf import WWFProductClassification
from altera_api.review.errors import IllegalTransitionError, SoftLockHeldError
from altera_api.review.locks import (
    SOFT_LOCK_DURATION,
    is_lock_expired,
    is_lock_held_by_other,
)


@dataclass(frozen=True)
class ReviewOutcome:
    item: ManualReviewItem
    decision: ManualReviewDecision
    pt_classification: ProteinTrackerProductClassification | None = None
    wwf_classification: WWFProductClassification | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_submittable(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> None:
    """Raise if the reviewer cannot submit a decision on this item right now."""
    if item.status.is_terminal:
        raise IllegalTransitionError(
            f"item is in terminal state {item.status.value}; cannot submit a decision."
        )
    if is_lock_held_by_other(item, reviewer_user_id=reviewer_user_id, now=now):
        raise SoftLockHeldError(
            f"item is locked by user {item.soft_lock_user_id} until "
            f"{item.soft_lock_expires_at!s}."
        )


def _release_lock(item: ManualReviewItem, *, status: ManualReviewStatus) -> ManualReviewItem:
    return item.model_copy(
        update={
            "status": status,
            "soft_lock_user_id": None,
            "soft_lock_expires_at": None,
        }
    )


def _new_decision_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Claim (open) an item
# ---------------------------------------------------------------------------
def claim_item(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> ManualReviewItem:
    """Set status=REVIEWING with a fresh 15-minute lock for ``reviewer_user_id``.

    Allowed from:
      * ``IN_QUEUE`` — first claim.
      * ``REVIEWING`` with an expired lock — the next reviewer steals it.
      * ``REVIEWING`` with a lock already held by *this* reviewer — refresh.

    Rejected with :class:`SoftLockHeldError` when another reviewer holds
    an unexpired lock; rejected with :class:`IllegalTransitionError`
    from any terminal state.
    """
    if item.status.is_terminal:
        raise IllegalTransitionError(
            f"cannot claim a terminal item (status={item.status.value})."
        )
    if is_lock_held_by_other(item, reviewer_user_id=reviewer_user_id, now=now):
        raise SoftLockHeldError(
            f"item is locked by user {item.soft_lock_user_id} until "
            f"{item.soft_lock_expires_at!s}."
        )
    return item.model_copy(
        update={
            "status": ManualReviewStatus.REVIEWING,
            "soft_lock_user_id": reviewer_user_id,
            "soft_lock_expires_at": now + SOFT_LOCK_DURATION,
        }
    )


# ---------------------------------------------------------------------------
# Accept / change / defer — Protein Tracker
# ---------------------------------------------------------------------------
def accept_pt_item(
    item: ManualReviewItem,
    *,
    current: ProteinTrackerProductClassification,
    reviewer_user_id: UUID,
    reason: str | None = None,
    now: datetime,
) -> ReviewOutcome:
    """Reviewer agrees with the existing PT classification.

    Promotes the classification's ``source`` to ``MANUAL_REVIEW`` while
    keeping the category unchanged. Writes a ``ManualReviewDecision``
    with ``from_category == to_category``.
    """
    if item.methodology is not Methodology.PROTEIN_TRACKER:
        raise IllegalTransitionError(
            f"item methodology is {item.methodology.value}; expected protein_tracker."
        )
    _require_submittable(item, reviewer_user_id=reviewer_user_id, now=now)

    new_classification = ProteinTrackerProductClassification(
        product_id=current.product_id,
        pt_group=current.pt_group,
        source=ClassificationSource.MANUAL_REVIEW,
        confidence=Decimal("1"),
        reviewer_user_id=reviewer_user_id,
        review_reason=reason,
        updated_at=now,
    )
    decision = ManualReviewDecision(
        id=_new_decision_id(),
        product_id=item.product_id,
        methodology=item.methodology,
        decision=ManualReviewDecisionType.ACCEPTED,
        reviewer_user_id=reviewer_user_id,
        from_category=current.pt_group.value,
        to_category=current.pt_group.value,
        reason=reason,
        created_at=now,
    )
    return ReviewOutcome(
        item=_release_lock(item, status=ManualReviewStatus.ACCEPTED),
        decision=decision,
        pt_classification=new_classification,
    )


def change_pt_item(
    item: ManualReviewItem,
    *,
    current: ProteinTrackerProductClassification | None,
    to_group: ProteinTrackerGroup,
    reviewer_user_id: UUID,
    reason: str | None = None,
    now: datetime,
) -> ReviewOutcome:
    """Reviewer assigns a different PT group.

    ``current`` may be ``None`` when the product had no prior
    classification (e.g. a parse-failed AI call). In that case
    ``from_category`` on the decision is ``None``.
    """
    if item.methodology is not Methodology.PROTEIN_TRACKER:
        raise IllegalTransitionError(
            f"item methodology is {item.methodology.value}; expected protein_tracker."
        )
    if not to_group.is_methodology_group:
        raise IllegalTransitionError(
            "manual reviewers cannot set system states (out_of_scope/unknown) as a "
            "category."
        )
    _require_submittable(item, reviewer_user_id=reviewer_user_id, now=now)

    if current is not None and current.pt_group is to_group:
        raise IllegalTransitionError(
            "change requires a different category; use accept_pt_item to keep "
            "the existing one."
        )

    new_classification = ProteinTrackerProductClassification(
        product_id=item.product_id,
        pt_group=to_group,
        source=ClassificationSource.MANUAL_REVIEW,
        confidence=Decimal("1"),
        reviewer_user_id=reviewer_user_id,
        review_reason=reason,
        updated_at=now,
    )
    decision = ManualReviewDecision(
        id=_new_decision_id(),
        product_id=item.product_id,
        methodology=item.methodology,
        decision=ManualReviewDecisionType.CHANGED,
        reviewer_user_id=reviewer_user_id,
        from_category=current.pt_group.value if current is not None else None,
        to_category=to_group.value,
        reason=reason,
        created_at=now,
    )
    return ReviewOutcome(
        item=_release_lock(item, status=ManualReviewStatus.CHANGED),
        decision=decision,
        pt_classification=new_classification,
    )


# ---------------------------------------------------------------------------
# Accept / change — WWF
# ---------------------------------------------------------------------------
def accept_wwf_item(
    item: ManualReviewItem,
    *,
    current: WWFProductClassification,
    reviewer_user_id: UUID,
    reason: str | None = None,
    now: datetime,
) -> ReviewOutcome:
    if item.methodology is not Methodology.WWF:
        raise IllegalTransitionError(
            f"item methodology is {item.methodology.value}; expected wwf."
        )
    _require_submittable(item, reviewer_user_id=reviewer_user_id, now=now)

    new_classification = WWFProductClassification(
        product_id=current.product_id,
        wwf_food_group=current.wwf_food_group,
        wwf_is_composite=current.wwf_is_composite,
        fg1_subgroup=current.fg1_subgroup,
        fg2_subgroup=current.fg2_subgroup,
        fg3_subgroup=current.fg3_subgroup,
        fg5_grain_kind=current.fg5_grain_kind,
        fg7_snack_kind=current.fg7_snack_kind,
        composite_step1_bucket=current.composite_step1_bucket,
        source=ClassificationSource.MANUAL_REVIEW,
        confidence=Decimal("1"),
        reviewer_user_id=reviewer_user_id,
        review_reason=reason,
        updated_at=now,
    )
    decision = ManualReviewDecision(
        id=_new_decision_id(),
        product_id=item.product_id,
        methodology=item.methodology,
        decision=ManualReviewDecisionType.ACCEPTED,
        reviewer_user_id=reviewer_user_id,
        from_category=current.wwf_food_group.value,
        to_category=current.wwf_food_group.value,
        reason=reason,
        created_at=now,
    )
    return ReviewOutcome(
        item=_release_lock(item, status=ManualReviewStatus.ACCEPTED),
        decision=decision,
        wwf_classification=new_classification,
    )


def change_wwf_item(
    item: ManualReviewItem,
    *,
    current: WWFProductClassification | None,
    target: WWFProductClassification,
    reviewer_user_id: UUID,
    reason: str | None = None,
    now: datetime,
) -> ReviewOutcome:
    """Reviewer assigns a different WWF classification.

    ``target`` is a fully-populated :class:`WWFProductClassification`
    constructed by the caller (typically the orchestration layer, which
    knows the methodology card to validate against). The cross-field
    constraints in the domain model guarantee FG/subgroup consistency.

    The ``source`` field on ``target`` is overridden to ``MANUAL_REVIEW``
    and ``confidence`` to 1.0 — the caller doesn't have to remember.
    """
    if item.methodology is not Methodology.WWF:
        raise IllegalTransitionError(
            f"item methodology is {item.methodology.value}; expected wwf."
        )
    if not target.wwf_food_group.is_methodology_group:
        raise IllegalTransitionError(
            "manual reviewers cannot set system states (out_of_scope/unknown)."
        )
    _require_submittable(item, reviewer_user_id=reviewer_user_id, now=now)

    if current is not None and _wwf_categories_equal(current, target):
        raise IllegalTransitionError(
            "change requires a different category; use accept_wwf_item to keep "
            "the existing one."
        )

    new_classification = target.model_copy(
        update={
            "product_id": item.product_id,
            "source": ClassificationSource.MANUAL_REVIEW,
            "confidence": Decimal("1"),
            "reviewer_user_id": reviewer_user_id,
            "review_reason": reason,
            "updated_at": now,
            # Strip any AI metadata that may have ridden on the target.
            "ai_prompt_version": None,
            "ai_model": None,
            "rule_id": None,
        }
    )
    decision = ManualReviewDecision(
        id=_new_decision_id(),
        product_id=item.product_id,
        methodology=item.methodology,
        decision=ManualReviewDecisionType.CHANGED,
        reviewer_user_id=reviewer_user_id,
        from_category=current.wwf_food_group.value if current is not None else None,
        to_category=target.wwf_food_group.value,
        reason=reason,
        created_at=now,
    )
    return ReviewOutcome(
        item=_release_lock(item, status=ManualReviewStatus.CHANGED),
        decision=decision,
        wwf_classification=new_classification,
    )


def _wwf_categories_equal(
    a: WWFProductClassification, b: WWFProductClassification
) -> bool:
    keys = (
        "wwf_food_group",
        "wwf_is_composite",
        "fg1_subgroup",
        "fg2_subgroup",
        "fg3_subgroup",
        "fg5_grain_kind",
        "fg7_snack_kind",
        "composite_step1_bucket",
    )
    return all(getattr(a, k) == getattr(b, k) for k in keys)


# ---------------------------------------------------------------------------
# Defer (no classification change)
# ---------------------------------------------------------------------------
def defer_item(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    reason: str | None = None,
    now: datetime,
) -> ReviewOutcome:
    """Reviewer flags the item as needing more information.

    The classification is **not** touched. The item enters the
    ``DEFERRED`` terminal state; callers may invoke
    :func:`reopen_after_defer` to push a fresh queue entry back into
    play once new information is available.
    """
    _require_submittable(item, reviewer_user_id=reviewer_user_id, now=now)
    decision = ManualReviewDecision(
        id=_new_decision_id(),
        product_id=item.product_id,
        methodology=item.methodology,
        decision=ManualReviewDecisionType.DEFERRED,
        reviewer_user_id=reviewer_user_id,
        from_category=None,
        to_category=None,
        reason=reason,
        created_at=now,
    )
    return ReviewOutcome(
        item=_release_lock(item, status=ManualReviewStatus.DEFERRED),
        decision=decision,
    )


def release_item(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> ManualReviewItem:
    """Release the soft lock held by *reviewer_user_id*.

    Reverts the item to ``IN_QUEUE`` so other reviewers can claim it.
    Only the current lock holder (or anyone if the lock has expired) may
    release. Raises :class:`SoftLockHeldError` if another reviewer holds
    an active lock.
    """
    if item.status.is_terminal:
        raise IllegalTransitionError(
            f"cannot release lock on a terminal item (status={item.status.value})."
        )
    if is_lock_held_by_other(item, reviewer_user_id=reviewer_user_id, now=now):
        raise SoftLockHeldError(
            f"item is locked by user {item.soft_lock_user_id}; "
            "only that reviewer can release it."
        )
    return item.model_copy(
        update={
            "status": ManualReviewStatus.IN_QUEUE,
            "soft_lock_user_id": None,
            "soft_lock_expires_at": None,
        }
    )


def refresh_lock(
    item: ManualReviewItem,
    *,
    reviewer_user_id: UUID,
    now: datetime,
) -> ManualReviewItem:
    """Extend the soft lock held by *reviewer_user_id* by :data:`SOFT_LOCK_DURATION`.

    Only the current lock holder may refresh. If the lock is expired the
    caller must re-claim via :func:`claim_item` instead.
    """
    if item.status.is_terminal:
        raise IllegalTransitionError(
            f"cannot refresh lock on a terminal item (status={item.status.value})."
        )
    if item.soft_lock_user_id != reviewer_user_id:
        raise SoftLockHeldError(
            "only the current lock holder can refresh the lock."
        )
    if is_lock_expired(item, now=now):
        raise SoftLockHeldError(
            "lock has expired; re-claim the item to start reviewing."
        )
    return item.model_copy(
        update={"soft_lock_expires_at": now + SOFT_LOCK_DURATION}
    )


def reopen_after_defer(
    deferred: ManualReviewItem,
    *,
    now: datetime,
    reason: ManualReviewQueueReason = ManualReviewQueueReason.REQUESTED,
) -> ManualReviewItem:
    """Create a fresh ``IN_QUEUE`` item for a product whose review was deferred.

    Strictly a constructor — the deferred item itself stays terminal so
    the audit trail is intact. The returned item shares the same
    ``product_id`` and ``methodology``, with a new ``queued_at`` and the
    caller-chosen reason (``REQUESTED`` by default).
    """
    if deferred.status is not ManualReviewStatus.DEFERRED:
        raise IllegalTransitionError(
            "reopen_after_defer requires an item in the DEFERRED state."
        )
    # Use a helper to compute "expired now" cheaply; if the lock is somehow
    # still recorded, surface that as a defensive guard.
    if deferred.soft_lock_user_id is not None and not is_lock_expired(deferred, now=now):
        raise IllegalTransitionError(
            "deferred item still holds an unexpired lock; that is a bug elsewhere."
        )
    return ManualReviewItem(
        product_id=deferred.product_id,
        methodology=deferred.methodology,
        status=ManualReviewStatus.IN_QUEUE,
        reason=reason,
        queued_at=now,
    )
