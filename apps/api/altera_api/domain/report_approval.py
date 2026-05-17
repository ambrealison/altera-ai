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


def can_approve(role: AlteraRole | ClientRole | str) -> bool:
    """Only ``altera_methodology_lead`` may approve a report."""
    return role == AlteraRole.ALTERA_METHODOLOGY_LEAD


def can_reject(role: AlteraRole | ClientRole | str) -> bool:
    """Only ``altera_methodology_lead`` may reject a report."""
    return role == AlteraRole.ALTERA_METHODOLOGY_LEAD


def assert_can_approve(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot approve."""
    if not can_approve(role):
        raise ApprovalPermissionDenied("approve", str(role))


def assert_can_reject(role: AlteraRole | ClientRole | str) -> None:
    """Raise ``ApprovalPermissionDenied`` if the role cannot reject."""
    if not can_reject(role):
        raise ApprovalPermissionDenied("reject", str(role))


def can_client_download(approval_status: ReportApprovalStatus) -> bool:
    """True only when the report has been approved.

    This is the gate enforced by the download endpoint for gms_client
    users.  Altera staff may preview any state from the internal UI.
    """
    return approval_status == ReportApprovalStatus.APPROVED


def assert_client_can_download(approval_status: ReportApprovalStatus) -> None:
    """Raise ``PermissionError`` if the report is not yet approved."""
    if not can_client_download(approval_status):
        raise PermissionError(
            f"Report export with status '{approval_status}' is not available "
            "for client download. Only approved reports may be downloaded."
        )


__all__ = [
    "ApprovalPermissionDenied",
    "assert_can_approve",
    "assert_can_reject",
    "assert_client_can_download",
    "can_approve",
    "can_client_download",
    "can_reject",
]
