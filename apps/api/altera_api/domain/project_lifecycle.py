"""Project lifecycle state machine.

All transition logic lives here as pure functions with no I/O.
The API layer calls ``validate_transition`` before persisting a status
change; invalid transitions raise ``InvalidTransition``.
"""
from __future__ import annotations

from altera_api.domain.project import ClientProjectStatus, ProjectStatus, client_facing_status

# Adjacency map: every valid (from, to) pair.
_ALLOWED: dict[ProjectStatus, frozenset[ProjectStatus]] = {
    ProjectStatus.CREATED: frozenset({
        ProjectStatus.WAITING_FOR_CLIENT_UPLOAD,
    }),
    ProjectStatus.WAITING_FOR_CLIENT_UPLOAD: frozenset({
        ProjectStatus.UPLOADED,
    }),
    ProjectStatus.UPLOADED: frozenset({
        ProjectStatus.VALIDATION,
    }),
    ProjectStatus.VALIDATION: frozenset({
        ProjectStatus.CLASSIFICATION,
        # Hard validation failure routes back to Altera review.
        ProjectStatus.ALTERA_REVIEW_REQUIRED,
    }),
    ProjectStatus.CLASSIFICATION: frozenset({
        ProjectStatus.ALTERA_REVIEW_REQUIRED,
        # If classification produces no review items, go straight to calc.
        ProjectStatus.CALCULATION,
    }),
    ProjectStatus.ALTERA_REVIEW_REQUIRED: frozenset({
        ProjectStatus.CALCULATION,
        # Reviewer may need to request a fresh upload from the client.
        ProjectStatus.WAITING_FOR_CLIENT_UPLOAD,
    }),
    ProjectStatus.CALCULATION: frozenset({
        ProjectStatus.REPORT_DRAFT,
    }),
    ProjectStatus.REPORT_DRAFT: frozenset({
        ProjectStatus.REPORT_UNDER_ALTERA_REVIEW,
        # Analyst may rework before submitting for review.
        ProjectStatus.CALCULATION,
    }),
    ProjectStatus.REPORT_UNDER_ALTERA_REVIEW: frozenset({
        ProjectStatus.REPORT_APPROVED,
        # Lead rejects; analyst reworks.
        ProjectStatus.REPORT_DRAFT,
    }),
    ProjectStatus.REPORT_APPROVED: frozenset({
        ProjectStatus.DELIVERED_TO_CLIENT,
    }),
    ProjectStatus.DELIVERED_TO_CLIENT: frozenset({
        ProjectStatus.ARCHIVED,
    }),
    ProjectStatus.ARCHIVED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when a requested status transition is not in the allowed set."""

    def __init__(self, from_status: ProjectStatus, to_status: ProjectStatus) -> None:
        super().__init__(
            f"Transition from '{from_status}' to '{to_status}' is not allowed."
        )
        self.from_status = from_status
        self.to_status = to_status


def allowed_transitions(status: ProjectStatus) -> frozenset[ProjectStatus]:
    """Return the set of statuses reachable from *status* in one step."""
    return _ALLOWED.get(status, frozenset())


def validate_transition(from_status: ProjectStatus, to_status: ProjectStatus) -> None:
    """Raise ``InvalidTransition`` if the transition is not allowed.

    This is a pure assertion; it does not persist anything.
    """
    if to_status not in _ALLOWED.get(from_status, frozenset()):
        raise InvalidTransition(from_status, to_status)


__all__ = [
    "InvalidTransition",
    "ProjectStatus",
    "ClientProjectStatus",
    "allowed_transitions",
    "client_facing_status",
    "validate_transition",
]
