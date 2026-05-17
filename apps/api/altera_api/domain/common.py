"""Common types, enums, and the strict domain base model."""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


#: Strict, frozen base for every domain model. No coercion, no extras, no mutation.
class DomainBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_assignment=True,
        populate_by_name=True,
    )


#: Non-empty trimmed string used for free-text identifiers and labels.
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

#: URL slug: lowercase, alphanumeric, dashes only.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1)]

#: ISO 639-1 lowercase two-letter language code (e.g. `en`, `fr`, `nl`).
Language = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$")]

#: ISO 3166-1 alpha-2 uppercase country code (e.g. `GB`, `FR`, `NL`).
Country = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]

#: Non-negative decimal physical quantity (kilogrammes, items, percentages, …).
Quantity = Annotated[Decimal, Field(ge=Decimal("0"))]


class Methodology(StrEnum):
    """The two methodologies the platform supports."""

    PROTEIN_TRACKER = "protein_tracker"
    WWF = "wwf"


class OrganisationType(StrEnum):
    """The two kinds of organisation on the platform.

    GMS clients upload catalogues and download approved reports.
    Altera-internal organisations operate the pipeline, work the review
    queue, and approve reports.
    """

    GMS_CLIENT = "gms_client"
    ALTERA_INTERNAL = "altera_internal"


class Role(StrEnum):
    """Legacy single-namespace roles (Phase 12). Kept for backward
    compatibility while the route layer is migrated to the two-namespace
    model. See docs/project/roles.md."""

    OWNER = "owner"
    ADMIN = "admin"
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class ClientRole(StrEnum):
    """Roles for gms_client organisations. See docs/project/roles.md."""

    CLIENT_OWNER = "client_owner"
    CLIENT_ADMIN = "client_admin"
    CLIENT_VIEWER = "client_viewer"


class AlteraRole(StrEnum):
    """Roles for altera_internal organisations. See docs/project/roles.md."""

    ALTERA_ADMIN = "altera_admin"
    ALTERA_ANALYST = "altera_analyst"
    ALTERA_REVIEWER = "altera_reviewer"
    ALTERA_METHODOLOGY_LEAD = "altera_methodology_lead"


class ClassificationSource(StrEnum):
    """How a classification was produced."""

    DETERMINISTIC = "deterministic"
    AI = "ai"
    MANUAL_REVIEW = "manual_review"
