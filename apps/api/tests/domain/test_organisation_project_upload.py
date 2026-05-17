from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology, OrganisationType, Role
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.project import Project, ProjectStatus, PTValidationStatus
from altera_api.domain.upload import Upload, UploadStatus


class TestOrganisation:
    def test_creates(self, org_id: UUID, now: datetime) -> None:
        o = Organisation(id=org_id, name="Retailer X", slug="retailer-x", created_at=now)
        assert o.slug == "retailer-x"

    def test_defaults_to_gms_client(self, org_id: UUID, now: datetime) -> None:
        o = Organisation(id=org_id, name="Retailer X", slug="retailer-x", created_at=now)
        assert o.organisation_type is OrganisationType.GMS_CLIENT

    def test_accepts_altera_internal_type(self, org_id: UUID, now: datetime) -> None:
        o = Organisation(
            id=org_id,
            name="Altera AI",
            slug="altera-ai",
            organisation_type=OrganisationType.ALTERA_INTERNAL,
            created_at=now,
        )
        assert o.organisation_type is OrganisationType.ALTERA_INTERNAL

    def test_rejects_invalid_slug(self, org_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            Organisation(id=org_id, name="X", slug="Not A Slug!", created_at=now)

    def test_rejects_extra_fields(self, org_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            Organisation(id=org_id, name="X", slug="x", created_at=now, founder="z")  # type: ignore[call-arg]


class TestUserProfile:
    def test_creates(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        u = UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email="user@example.com",
            display_name="Test User",
            role=Role.ANALYST,
            created_at=now,
        )
        assert u.role is Role.ANALYST

    def test_rejects_bad_email(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            UserProfile(
                user_id=user_id,
                organisation_id=org_id,
                email="not-an-email",
                display_name="x",
                role=Role.VIEWER,
                created_at=now,
            )

    def test_rejects_invalid_role(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            UserProfile(
                user_id=user_id,
                organisation_id=org_id,
                email="x@y.com",
                display_name="x",
                role="superuser",  # type: ignore[arg-type]
                created_at=now,
            )


class TestProject:
    def _base(self, org_id: UUID, user_id: UUID, now: datetime) -> dict:
        return dict(
            id=UUID("00000000-0000-0000-0000-000000000099"),
            organisation_id=org_id,
            name="FY 2024 review",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER, Methodology.WWF}),
            reporting_period_label="FY 2024",
            created_by=user_id,
            created_at=now,
        )

    def test_creates_with_both_methodologies(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        p = Project(**self._base(org_id, user_id, now))
        assert Methodology.PROTEIN_TRACKER in p.methodologies_enabled

    def test_rejects_empty_methodology_set(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        base = self._base(org_id, user_id, now)
        base["methodologies_enabled"] = frozenset()
        with pytest.raises(PydanticValidationError):
            Project(**base)

    def test_pt_validation_status_requires_pt_enabled(
        self, org_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        base = self._base(org_id, user_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.WWF})
        base["pt_validation_status"] = PTValidationStatus.DRAFT
        with pytest.raises(PydanticValidationError):
            Project(**base)

    def test_pinned_pt_requires_pt_enabled(self, org_id: UUID, user_id: UUID, now: datetime) -> None:
        base = self._base(org_id, user_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.WWF})
        base["pinned_pt_version"] = "1.0.0"
        with pytest.raises(PydanticValidationError):
            Project(**base)

    def test_reporting_period_must_be_ordered(
        self, org_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        base = self._base(org_id, user_id, now)
        base["reporting_period_start"] = date(2024, 12, 31)
        base["reporting_period_end"] = date(2024, 1, 1)
        with pytest.raises(PydanticValidationError):
            Project(**base)

    def test_project_status_defaults_to_created(
        self, org_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        p = Project(**self._base(org_id, user_id, now))
        assert p.project_status is ProjectStatus.CREATED

    def test_project_status_can_be_set(
        self, org_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        base = self._base(org_id, user_id, now)
        base["project_status"] = ProjectStatus.CALCULATION
        p = Project(**base)
        assert p.project_status is ProjectStatus.CALCULATION


class TestUpload:
    def test_creates_pending(
        self, org_id: UUID, project_id: UUID, upload_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        u = Upload(
            id=upload_id,
            organisation_id=org_id,
            project_id=project_id,
            storage_path="org/upload/2026-05-15.csv",
            original_filename="retailer.csv",
            status=UploadStatus.PENDING,
            uploaded_by=user_id,
            created_at=now,
        )
        assert u.status is UploadStatus.PENDING
        assert u.row_count is None

    def test_valid_status_requires_row_count(
        self, org_id: UUID, project_id: UUID, upload_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            Upload(
                id=upload_id,
                organisation_id=org_id,
                project_id=project_id,
                storage_path="x",
                original_filename="x.csv",
                status=UploadStatus.VALID,
                row_count=None,
                uploaded_by=user_id,
                created_at=datetime(2026, 5, 15, tzinfo=UTC),
            )

    def test_invalid_status_with_row_count_ok(
        self, org_id: UUID, project_id: UUID, upload_id: UUID, user_id: UUID, now: datetime
    ) -> None:
        u = Upload(
            id=upload_id,
            organisation_id=org_id,
            project_id=project_id,
            storage_path="x",
            original_filename="x.csv",
            status=UploadStatus.INVALID,
            row_count=12,
            dropped_columns=("revenue", "supplier_id"),
            uploaded_by=user_id,
            created_at=now,
        )
        assert u.row_count == 12
        assert "revenue" in u.dropped_columns
