"""Report approval domain helpers.

Pure functions with no I/O. The API layer calls these before persisting
approval decisions and before serving download requests.
"""

from __future__ import annotations

from altera_api.domain.common import AlteraRole, ClientRole
from altera_api.domain.report_exports import ReportApprovalStatus


class ApprovalPermissionDenied(Exception):
    """Raised when a role is not permitted to perform an approval action."""

    def __init__(self, action: str, role: str) -> None:
        super().__init__(f"Role '{role}' may not perform '{action}'.")
        self.action = action
        self.role = role


def can_submit_for_review(role: AlteraRole | ClientRole | str) -> bool:
    """Any Altera-internal role may submit a report for review."""
    return isinstance(role, AlteraRole)


def can_approve(role: AlteraRole | ClientRole | str) -> bool:
    """Only ``altera_methodology_lead`` may approve a report."""
    return role == AlteraRole.ALTERA_METHODOLOGY_LEAD


def can_reject(role: AlteraRole | ClientRole | str) -> bool:
    """Only ``altera_methodology_lead`` may reject a report."""
    return role == AlteraRole.ALTERA_METHODOLOGY_LEAD


def can_deliver(role: AlteraRole | ClientRole | str) -> bool:
    """``altera_methodology_lead`` and ``altera_admin`` may deliver a report."""
    return role in {AlteraRole.ALTERA_METHODOLOGY_LEAD, AlteraRole.ALTERA_ADMIN}


def assert_can_submit_for_review(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot submit for review."""
    if not can_submit_for_review(role):
        raise ApprovalPermissionDenied("submit_for_review", str(role))


def assert_can_approve(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot approve."""
    if not can_approve(role):
        raise ApprovalPermissionDenied("approve", str(role))


def assert_can_reject(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot reject."""
    if not can_reject(role):
        raise ApprovalPermissionDenied("reject", str(role))


def assert_can_deliver(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot deliver."""
    if not can_deliver(role):
        raise ApprovalPermissionDenied("deliver", str(role))


def can_client_download(approval_status: ReportApprovalStatus) -> bool:
    """True for ``approved`` or ``delivered`` exports.

    Clients may download once Altera has approved — delivery is the
    explicit act of making the report visible to the client.
    """
    return approval_status in {
        ReportApprovalStatus.APPROVED,
        ReportApprovalStatus.DELIVERED,
    }


def assert_client_can_download(approval_status: ReportApprovalStatus) -> None:
    """Raise ``PermissionError`` if the report is not yet approved or delivered."""
    if not can_client_download(approval_status):
        raise PermissionError(
            f"Report export with status '{approval_status}' is not available "
            "for client download. Only approved or delivered reports may be downloaded."
        )


__all__ = [
    "ApprovalPermissionDenied",
    "assert_can_approve",
    "assert_can_deliver",
    "assert_can_reject",
    "assert_can_submit_for_review",
    "assert_client_can_download",
    "can_approve",
    "can_client_download",
    "can_deliver",
    "can_reject",
    "can_submit_for_review",
]
