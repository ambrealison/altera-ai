"""Request-scoped authentication context."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType, Role


class AuthProvider(StrEnum):
    SUPABASE = "supabase"
    DEV = "dev"


@dataclass(frozen=True)
class AuthContext:
    """What every authenticated request has access to.

    Phase 13C ships single-org-per-user contexts. Multi-org users still
    have one ``AuthContext`` per request; switching orgs is a separate
    sign-in concern handled by the frontend (later phase).

    Phase 14 adds:
    - ``organisation_type`` — whether this is an Altera-internal or
      gms_client org, allowing route-level visibility decisions.
    - Namespaced roles — ``AlteraRole`` / ``ClientRole`` alongside the
      legacy ``Role`` values.
    """

    user_id: UUID
    email: str
    organisation_id: UUID
    role: Role | ClientRole | AlteraRole
    auth_provider: AuthProvider
    is_dev_auth: bool
    organisation_type: OrganisationType = field(default=OrganisationType.GMS_CLIENT)
    raw_token: str | None = None  # None for dev auth; the bearer JWT for Supabase auth

    # ------------------------------------------------------------------
    # Convenience predicates
    # ------------------------------------------------------------------

    @property
    def is_altera_internal(self) -> bool:
        return self.organisation_type == OrganisationType.ALTERA_INTERNAL

    @property
    def is_owner_or_admin(self) -> bool:
        if isinstance(self.role, AlteraRole):
            return self.role == AlteraRole.ALTERA_ADMIN
        if isinstance(self.role, ClientRole):
            return self.role in {ClientRole.CLIENT_OWNER, ClientRole.CLIENT_ADMIN}
        return self.role in {Role.OWNER, Role.ADMIN}

    @property
    def can_write_data(self) -> bool:
        if isinstance(self.role, AlteraRole):
            return self.role in {AlteraRole.ALTERA_ADMIN, AlteraRole.ALTERA_ANALYST}
        if isinstance(self.role, ClientRole):
            return self.role in {ClientRole.CLIENT_OWNER, ClientRole.CLIENT_ADMIN}
        return self.role in {Role.OWNER, Role.ADMIN, Role.ANALYST}

    @property
    def can_review(self) -> bool:
        if isinstance(self.role, AlteraRole):
            return True  # all Altera roles can work the review queue
        if isinstance(self.role, ClientRole):
            return False  # clients do not review — Altera does
        return self.role in {Role.OWNER, Role.ADMIN, Role.ANALYST, Role.REVIEWER}

    @property
    def can_approve_report(self) -> bool:
        return isinstance(self.role, AlteraRole) and self.role == AlteraRole.ALTERA_METHODOLOGY_LEAD

    @property
    def can_deliver_report(self) -> bool:
        return isinstance(self.role, AlteraRole) and self.role in {
            AlteraRole.ALTERA_METHODOLOGY_LEAD,
            AlteraRole.ALTERA_ADMIN,
        }

    @property
    def can_propose_recommendation(self) -> bool:
        return isinstance(self.role, AlteraRole) and self.role in {
            AlteraRole.ALTERA_METHODOLOGY_LEAD,
            AlteraRole.ALTERA_ADMIN,
        }

    @property
    def can_create_scenario(self) -> bool:
        """Scenario creation, operations, and execution — any Altera-internal user."""
        return self.is_altera_internal

    @property
    def can_apply_enrichment(self) -> bool:
        """Manual and category-average nutrition enrichment — any Altera-internal user."""
        return self.is_altera_internal

    @property
    def can_generate_recommendations(self) -> bool:
        """Recommendation generation — any Altera-internal user."""
        return self.is_altera_internal
