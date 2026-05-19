"""Admin API routes — organisation and user management.

All endpoints require the ``altera_admin`` role.  The invite endpoint
calls the Supabase Auth Admin API server-side so the service-role key is
never sent to the browser.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from altera_api.api.dependencies import get_store
from altera_api.api.errors import (
    raise_bad_request,
    raise_conflict,
    raise_forbidden,
    raise_not_found,
)
from altera_api.auth import AuthContext, authed_user
from altera_api.auth.config import AuthSettings, get_auth_settings
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType
from altera_api.domain.organisation import UserProfile
from altera_api.persistence.protocol import StoreProtocol
from altera_api.supabase_admin import get_supabase_admin_client

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _require_altera_admin(auth: AuthContext) -> None:
    if not (isinstance(auth.role, AlteraRole) and auth.role == AlteraRole.ALTERA_ADMIN):
        raise_forbidden("altera_admin role required", error_code="admin_required")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateOrgRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    slug: str = Field(..., min_length=1, max_length=80)


class OrgResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    organisation_type: str
    created_at: datetime


class InviteUserRequest(BaseModel):
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(default="client_owner")
    redirect_to: str | None = None


class InviteUserResponse(BaseModel):
    user_id: UUID
    email: str
    organisation_id: UUID
    role: str
    invite_sent: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@admin_router.get("/organisations", response_model=list[OrgResponse])
def list_organisations(
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
) -> list[OrgResponse]:
    """List all organisations. Requires altera_admin."""
    _require_altera_admin(auth)
    return [
        OrgResponse(
            id=o.id,
            name=o.name,
            slug=o.slug,
            organisation_type=o.organisation_type.value,
            created_at=o.created_at,
        )
        for o in store.list_organisations()
    ]


@admin_router.post("/organisations", response_model=OrgResponse, status_code=201)
def create_organisation(
    body: CreateOrgRequest,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
) -> OrgResponse:
    """Create a new client organisation. Requires altera_admin."""
    _require_altera_admin(auth)
    if not _SLUG_RE.match(body.slug):
        raise_bad_request(
            f"invalid slug {body.slug!r} — lowercase alphanumeric with hyphens only",
            error_code="invalid_slug",
        )
    try:
        org = store.create_organisation(
            name=body.name,
            slug=body.slug,
            organisation_type=OrganisationType.GMS_CLIENT,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg or "already exists" in msg or "23505" in msg:
            raise_conflict(
                f"an organisation with slug {body.slug!r} already exists",
                error_code="slug_conflict",
            )
        raise
    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        organisation_type=org.organisation_type.value,
        created_at=org.created_at,
    )


@admin_router.post(
    "/organisations/{org_id}/invite",
    response_model=InviteUserResponse,
    status_code=201,
)
def invite_user(
    org_id: UUID,
    body: InviteUserRequest,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
    settings: AuthSettings = Depends(get_auth_settings),
) -> InviteUserResponse:
    """Invite a user to a client organisation. Requires altera_admin.

    Calls the Supabase Auth Admin API server-side to create the auth.users
    row and send the invite email. Pre-provisions user_profiles + memberships
    so the user lands in the correct org and role on first login.
    """
    _require_altera_admin(auth)

    org = store.get_organisation(org_id)
    if org is None:
        raise_not_found(f"organisation {org_id} not found")

    try:
        client_role = ClientRole(body.role)
    except ValueError:
        valid = ", ".join(r.value for r in ClientRole)
        raise_bad_request(
            f"role must be one of: {valid}",
            error_code="invalid_role",
        )

    # Derive redirect URL from CORS origins when the caller doesn't specify one.
    redirect_to = body.redirect_to
    if not redirect_to:
        cors_raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
        origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
        if origins:
            redirect_to = origins[0].rstrip("/") + "/auth/callback"

    invite_sent = False
    user_id: UUID
    admin_client = get_supabase_admin_client(
        settings.supabase_url, settings.supabase_service_role_key
    )

    if admin_client is not None:
        try:
            result = admin_client.invite_user_by_email(body.email, redirect_to=redirect_to)
            user_id = result.user_id
            invite_sent = True
        except Exception as exc:
            msg = str(exc)
            if "already been registered" in msg or "already exists" in msg or "422" in msg:
                raise_conflict(
                    f"a user with email {body.email!r} is already registered",
                    error_code="user_already_exists",
                )
            raise
    else:
        # Dev/memory mode: generate a placeholder UUID; no email is sent.
        user_id = uuid4()

    # Pre-provision profile + membership so the first login resolves correctly.
    profile = UserProfile(
        user_id=user_id,
        organisation_id=org_id,
        email=body.email,
        display_name=body.email.split("@")[0],
        role=client_role,
        created_at=datetime.now(UTC),
    )
    store.upsert_user(profile)

    return InviteUserResponse(
        user_id=user_id,
        email=body.email,
        organisation_id=org_id,
        role=client_role.value,
        invite_sent=invite_sent,
    )
