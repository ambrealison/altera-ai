"""Tests for the project lifecycle state machine and client-facing status mapping."""
from __future__ import annotations

import pytest

from altera_api.domain.project import ClientProjectStatus, ProjectStatus, client_facing_status
from altera_api.domain.project_lifecycle import (
    InvalidTransition,
    allowed_transitions,
    validate_transition,
)

# ---------------------------------------------------------------------------
# client_facing_status mapping
# ---------------------------------------------------------------------------


class TestClientFacingStatus:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (ProjectStatus.CREATED, ClientProjectStatus.WAITING_FOR_UPLOAD),
            (ProjectStatus.WAITING_FOR_CLIENT_UPLOAD, ClientProjectStatus.WAITING_FOR_UPLOAD),
            (ProjectStatus.UPLOADED, ClientProjectStatus.PROCESSING),
            (ProjectStatus.VALIDATION, ClientProjectStatus.PROCESSING),
            (ProjectStatus.CLASSIFICATION, ClientProjectStatus.PROCESSING),
            (ProjectStatus.ALTERA_REVIEW_REQUIRED, ClientProjectStatus.PROCESSING),
            (ProjectStatus.CALCULATION, ClientProjectStatus.PROCESSING),
            (ProjectStatus.REPORT_DRAFT, ClientProjectStatus.PROCESSING),
            (ProjectStatus.REPORT_UNDER_ALTERA_REVIEW, ClientProjectStatus.UNDER_ALTERA_REVIEW),
            (ProjectStatus.REPORT_APPROVED, ClientProjectStatus.REPORT_READY),
            (ProjectStatus.DELIVERED_TO_CLIENT, ClientProjectStatus.REPORT_READY),
            (ProjectStatus.ARCHIVED, ClientProjectStatus.ARCHIVED),
        ],
    )
    def test_mapping(self, status: ProjectStatus, expected: ClientProjectStatus) -> None:
        assert client_facing_status(status) == expected

    def test_all_statuses_covered(self) -> None:
        for status in ProjectStatus:
            result = client_facing_status(status)
            assert isinstance(result, ClientProjectStatus), (
                f"client_facing_status({status!r}) returned {result!r}, "
                "expected a ClientProjectStatus"
            )


# ---------------------------------------------------------------------------
# allowed_transitions
# ---------------------------------------------------------------------------


class TestAllowedTransitions:
    def test_created_can_transition_to_waiting(self) -> None:
        assert ProjectStatus.WAITING_FOR_CLIENT_UPLOAD in allowed_transitions(ProjectStatus.CREATED)

    def test_archived_has_no_outgoing_transitions(self) -> None:
        assert allowed_transitions(ProjectStatus.ARCHIVED) == frozenset()

    def test_review_required_can_go_to_calculation_or_back_to_waiting(self) -> None:
        allowed = allowed_transitions(ProjectStatus.ALTERA_REVIEW_REQUIRED)
        assert ProjectStatus.CALCULATION in allowed
        assert ProjectStatus.WAITING_FOR_CLIENT_UPLOAD in allowed

    def test_report_draft_can_go_back_to_calculation(self) -> None:
        allowed = allowed_transitions(ProjectStatus.REPORT_DRAFT)
        assert ProjectStatus.CALCULATION in allowed
        assert ProjectStatus.REPORT_UNDER_ALTERA_REVIEW in allowed

    def test_report_under_review_can_be_approved_or_rejected(self) -> None:
        allowed = allowed_transitions(ProjectStatus.REPORT_UNDER_ALTERA_REVIEW)
        assert ProjectStatus.REPORT_APPROVED in allowed
        assert ProjectStatus.REPORT_DRAFT in allowed

    def test_every_status_has_an_entry(self) -> None:
        for status in ProjectStatus:
            # Should not raise; may return an empty frozenset for terminals.
            result = allowed_transitions(status)
            assert isinstance(result, frozenset)


# ---------------------------------------------------------------------------
# validate_transition
# ---------------------------------------------------------------------------


class TestValidateTransition:
    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            (ProjectStatus.CREATED, ProjectStatus.WAITING_FOR_CLIENT_UPLOAD),
            (ProjectStatus.WAITING_FOR_CLIENT_UPLOAD, ProjectStatus.UPLOADED),
            (ProjectStatus.UPLOADED, ProjectStatus.VALIDATION),
            (ProjectStatus.VALIDATION, ProjectStatus.CLASSIFICATION),
            (ProjectStatus.VALIDATION, ProjectStatus.ALTERA_REVIEW_REQUIRED),
            (ProjectStatus.CLASSIFICATION, ProjectStatus.ALTERA_REVIEW_REQUIRED),
            (ProjectStatus.CLASSIFICATION, ProjectStatus.CALCULATION),
            (ProjectStatus.ALTERA_REVIEW_REQUIRED, ProjectStatus.CALCULATION),
            (ProjectStatus.ALTERA_REVIEW_REQUIRED, ProjectStatus.WAITING_FOR_CLIENT_UPLOAD),
            (ProjectStatus.CALCULATION, ProjectStatus.REPORT_DRAFT),
            (ProjectStatus.REPORT_DRAFT, ProjectStatus.REPORT_UNDER_ALTERA_REVIEW),
            (ProjectStatus.REPORT_DRAFT, ProjectStatus.CALCULATION),
            (ProjectStatus.REPORT_UNDER_ALTERA_REVIEW, ProjectStatus.REPORT_APPROVED),
            (ProjectStatus.REPORT_UNDER_ALTERA_REVIEW, ProjectStatus.REPORT_DRAFT),
            (ProjectStatus.REPORT_APPROVED, ProjectStatus.DELIVERED_TO_CLIENT),
            (ProjectStatus.DELIVERED_TO_CLIENT, ProjectStatus.ARCHIVED),
        ],
    )
    def test_valid_transitions_do_not_raise(
        self, from_status: ProjectStatus, to_status: ProjectStatus
    ) -> None:
        validate_transition(from_status, to_status)  # must not raise

    @pytest.mark.parametrize(
        "from_status,to_status",
        [
            # Can't skip steps.
            (ProjectStatus.CREATED, ProjectStatus.UPLOADED),
            (ProjectStatus.CREATED, ProjectStatus.CALCULATION),
            (ProjectStatus.UPLOADED, ProjectStatus.REPORT_DRAFT),
            # Can't go backwards unless explicitly allowed.
            (ProjectStatus.CALCULATION, ProjectStatus.UPLOADED),
            (ProjectStatus.REPORT_APPROVED, ProjectStatus.REPORT_DRAFT),
            # Terminal state has no transitions.
            (ProjectStatus.ARCHIVED, ProjectStatus.CREATED),
            (ProjectStatus.ARCHIVED, ProjectStatus.DELIVERED_TO_CLIENT),
            # Self-transition never allowed.
            (ProjectStatus.CREATED, ProjectStatus.CREATED),
            (ProjectStatus.CALCULATION, ProjectStatus.CALCULATION),
        ],
    )
    def test_invalid_transitions_raise(
        self, from_status: ProjectStatus, to_status: ProjectStatus
    ) -> None:
        with pytest.raises(InvalidTransition) as exc_info:
            validate_transition(from_status, to_status)
        assert exc_info.value.from_status == from_status
        assert exc_info.value.to_status == to_status

    def test_error_message_names_both_statuses(self) -> None:
        with pytest.raises(InvalidTransition, match="created") as exc_info:
            validate_transition(ProjectStatus.CREATED, ProjectStatus.ARCHIVED)
        assert "archived" in str(exc_info.value)
