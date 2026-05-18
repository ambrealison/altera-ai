"""Exception types for the review workflow."""

from __future__ import annotations


class ReviewError(RuntimeError):
    """Base class for all manual-review workflow errors."""


class IllegalTransitionError(ReviewError):
    """The requested state transition is not valid from the current state."""


class SoftLockHeldError(ReviewError):
    """Another reviewer holds an unexpired soft lock on the item."""


class MethodologyMismatchError(ReviewError):
    """A bulk operation received items spanning more than one methodology."""
