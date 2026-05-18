"""Tests for report approval domain helpers."""

from __future__ import annotations

import pytest

from altera_api.domain.common import AlteraRole, ClientRole
from altera_api.domain.report_approval import (
    ApprovalPermissionDenied,
    assert_can_approve,
    assert_can_reject,
    assert_client_can_download,
    can_approve,
    can_client_download,
    can_reject,
)
from altera_api.domain.report_exports import ReportApprovalStatus

# ---------------------------------------------------------------------------
# can_approve / assert_can_approve
# ---------------------------------------------------------------------------


class TestCanApprove:
    def test_methodology_lead_can_approve(self) -> None:
        assert can_approve(AlteraRole.ALTERA_METHODOLOGY_LEAD) is True

    @pytest.mark.parametrize(
        "role",
        [
            AlteraRole.ALTERA_ADMIN,
            AlteraRole.ALTERA_ANALYST,
            AlteraRole.ALTERA_REVIEWER,
            ClientRole.CLIENT_OWNER,
            ClientRole.CLIENT_ADMIN,
            ClientRole.CLIENT_VIEWER,
        ],
    )
    def test_other_roles_cannot_approve(self, role: AlteraRole | ClientRole) -> None:
        assert can_approve(role) is False

    def test_assert_passes_for_methodology_lead(self) -> None:
        assert_can_approve(AlteraRole.ALTERA_METHODOLOGY_LEAD)  # must not raise

    def test_assert_raises_for_admin(self) -> None:
        with pytest.raises(ApprovalPermissionDenied) as exc_info:
            assert_can_approve(AlteraRole.ALTERA_ADMIN)
        assert exc_info.value.action == "approve"
        assert "altera_admin" in str(exc_info.value)

    def test_assert_raises_for_client(self) -> None:
        with pytest.raises(ApprovalPermissionDenied):
            assert_can_approve(ClientRole.CLIENT_OWNER)


# ---------------------------------------------------------------------------
# can_reject / assert_can_reject
# ---------------------------------------------------------------------------


class TestCanReject:
    def test_methodology_lead_can_reject(self) -> None:
        assert can_reject(AlteraRole.ALTERA_METHODOLOGY_LEAD) is True

    def test_analyst_cannot_reject(self) -> None:
        assert can_reject(AlteraRole.ALTERA_ANALYST) is False

    def test_assert_raises_for_reviewer(self) -> None:
        with pytest.raises(ApprovalPermissionDenied) as exc_info:
            assert_can_reject(AlteraRole.ALTERA_REVIEWER)
        assert exc_info.value.action == "reject"


# ---------------------------------------------------------------------------
# can_client_download / assert_client_can_download
# ---------------------------------------------------------------------------


class TestCanClientDownload:
    def test_approved_report_is_downloadable(self) -> None:
        assert can_client_download(ReportApprovalStatus.APPROVED) is True

    @pytest.mark.parametrize(
        "status",
        [ReportApprovalStatus.DRAFT, ReportApprovalStatus.REJECTED],
    )
    def test_non_approved_report_is_not_downloadable(self, status: ReportApprovalStatus) -> None:
        assert can_client_download(status) is False

    def test_assert_passes_for_approved(self) -> None:
        assert_client_can_download(ReportApprovalStatus.APPROVED)  # must not raise

    def test_assert_raises_for_draft(self) -> None:
        with pytest.raises(PermissionError, match="draft"):
            assert_client_can_download(ReportApprovalStatus.DRAFT)

    def test_assert_raises_for_rejected(self) -> None:
        with pytest.raises(PermissionError, match="rejected"):
            assert_client_can_download(ReportApprovalStatus.REJECTED)
