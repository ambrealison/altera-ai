"""Project model + Protein Tracker validation state machine + internal lifecycle."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from altera_api.domain.common import DomainBase, Methodology, NonEmptyStr


class ProjectStatus(StrEnum):
    """Internal project lifecycle.

    Transitions are enforced by the pure functions in
    `altera_api.domain.project_lifecycle`. Clients never see this enum
    directly; `client_facing_status()` maps it to `ClientProjectStatus`.
    """

    CREATED = "created"
    WAITING_FOR_CLIENT_UPLOAD = "waiting_for_client_upload"
    UPLOADED = "uploaded"
    VALIDATION = "validation"
    CLASSIFICATION = "classification"
    ALTERA_REVIEW_REQUIRED = "altera_review_required"
    CALCULATION = "calculation"
    REPORT_DRAFT = "report_draft"
    REPORT_UNDER_ALTERA_REVIEW = "report_under_altera_review"
    REPORT_APPROVED = "report_approved"
    DELIVERED_TO_CLIENT = "delivered_to_client"
    ARCHIVED = "archived"


class ClientProjectStatus(StrEnum):
    """Simplified status shown to gms_client users."""

    WAITING_FOR_UPLOAD = "waiting_for_upload"
    PROCESSING = "processing"
    UNDER_ALTERA_REVIEW = "under_altera_review"
    REPORT_READY = "report_ready"
    ARCHIVED = "archived"


def client_facing_status(status: ProjectStatus) -> ClientProjectStatus:
    """Map the internal 12-state lifecycle to the 5-state client view."""
    match status:
        case ProjectStatus.CREATED | ProjectStatus.WAITING_FOR_CLIENT_UPLOAD:
            return ClientProjectStatus.WAITING_FOR_UPLOAD
        case (
            ProjectStatus.UPLOADED
            | ProjectStatus.VALIDATION
            | ProjectStatus.CLASSIFICATION
            | ProjectStatus.ALTERA_REVIEW_REQUIRED
            | ProjectStatus.CALCULATION
            | ProjectStatus.REPORT_DRAFT
        ):
            return ClientProjectStatus.PROCESSING
        case ProjectStatus.REPORT_UNDER_ALTERA_REVIEW:
            return ClientProjectStatus.UNDER_ALTERA_REVIEW
        case ProjectStatus.REPORT_APPROVED | ProjectStatus.DELIVERED_TO_CLIENT:
            return ClientProjectStatus.REPORT_READY
        case ProjectStatus.ARCHIVED:
            return ClientProjectStatus.ARCHIVED


class PTValidationStatus(StrEnum):
    """External validation lifecycle for Protein Tracker results.

    The PT methodology requires GPA/ProVeg sign-off before a number can
    be published as a validated PT figure. This is independent of any
    internal review queue.
    """

    NONE = "none"
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATED = "validated"


class Project(DomainBase):
    """A unit of work within an organisation."""

    id: UUID
    organisation_id: UUID
    name: NonEmptyStr
    methodologies_enabled: frozenset[Methodology] = Field(min_length=1)
    reporting_period_label: NonEmptyStr
    reporting_period_start: date | None = None
    reporting_period_end: date | None = None
    pinned_pt_version: str | None = None
    pinned_wwf_version: str | None = None
    pinned_taxonomy_version: str | None = None
    pinned_rules_version: str | None = None
    project_status: ProjectStatus = ProjectStatus.CREATED
    pt_validation_status: PTValidationStatus = PTValidationStatus.NONE
    created_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _validation_status_consistent_with_enabled_methodologies(self) -> Self:
        if (
            self.pt_validation_status is not PTValidationStatus.NONE
            and Methodology.PROTEIN_TRACKER not in self.methodologies_enabled
        ):
            raise ValueError(
                "pt_validation_status may only be non-`none` when protein_tracker "
                "is enabled on the project."
            )
        return self

    @model_validator(mode="after")
    def _pt_pin_only_when_pt_enabled(self) -> Self:
        if (
            self.pinned_pt_version is not None
            and Methodology.PROTEIN_TRACKER not in self.methodologies_enabled
        ):
            raise ValueError("pinned_pt_version requires protein_tracker to be enabled.")
        if (
            self.pinned_wwf_version is not None
            and Methodology.WWF not in self.methodologies_enabled
        ):
            raise ValueError("pinned_wwf_version requires wwf to be enabled.")
        return self

    @model_validator(mode="after")
    def _reporting_period_ordered(self) -> Self:
        if (
            self.reporting_period_start is not None
            and self.reporting_period_end is not None
            and self.reporting_period_end < self.reporting_period_start
        ):
            raise ValueError("reporting_period_end must be on or after reporting_period_start.")
        return self
