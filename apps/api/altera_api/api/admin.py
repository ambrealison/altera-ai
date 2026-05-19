"""Admin API routes — organisation and user management.

All endpoints require the ``altera_admin`` role.  Supabase Auth Admin API
calls happen server-side only; the service-role key never reaches the
frontend.

Phase 32A: create org, invite user.
Phase 32B: list members, resend invite, change role, remove member.
           Audit events emitted for every mutating operation.
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
from altera_api.domain.audit import AuditEvent, AuditEventType
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType
from altera_api.domain.organisation import UserProfile
from altera_api.persistence.protocol import StoreProtocol
from altera_api.supabase_admin import get_supabase_admin_client

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _require_altera_admin(auth: AuthContext) -> None:
    if not (isinstance(auth.role, AlteraRole) and auth.role == AlteraRole.ALTERA_ADMIN):
        raise_forbidden("altera_admin role required", error_code="admin_required")


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _emit(
    store: StoreProtocol,
    *,
    organisation_id: UUID,
    actor_id: UUID,
    action: AuditEventType,
    target_id: UUID | None = None,
    metadata: dict | None = None,
) -> None:
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=organisation_id,
            actor_user_id=actor_id,
            action=action,
            target_id=target_id,
            metadata=metadata or {},
            created_at=datetime.now(UTC),
        )
    )


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


class MemberResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: str
    organisation_id: UUID


class UpdateMemberRequest(BaseModel):
    role: str


class ResendInviteResponse(BaseModel):
    user_id: UUID
    email: str
    organisation_id: UUID
    invite_sent: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _derive_redirect_to(body_redirect: str | None) -> str | None:
    """Derive redirect_to from CORS origins when not provided by caller."""
    if body_redirect:
        return body_redirect
    cors_raw = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
    return (origins[0].rstrip("/") + "/auth/callback") if origins else None


def _validate_client_role(role_str: str) -> ClientRole:
    try:
        return ClientRole(role_str)
    except ValueError:
        valid = ", ".join(r.value for r in ClientRole)
        raise_bad_request(
            f"role must be one of: {valid}",
            error_code="invalid_role",
        )


def _member_response(profile: UserProfile) -> MemberResponse:
    return MemberResponse(
        user_id=profile.user_id,
        email=profile.email,
        display_name=profile.display_name,
        role=profile.role.value,
        organisation_id=profile.organisation_id,
    )


# ---------------------------------------------------------------------------
# Organisations
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

    _emit(
        store,
        organisation_id=org.id,
        actor_id=auth.user_id,
        action=AuditEventType.ORG_CREATED,
        target_id=org.id,
        metadata={"name": org.name, "slug": org.slug},
    )

    return OrgResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        organisation_type=org.organisation_type.value,
        created_at=org.created_at,
    )


# ---------------------------------------------------------------------------
# Members — list
# ---------------------------------------------------------------------------


@admin_router.get(
    "/organisations/{org_id}/members",
    response_model=list[MemberResponse],
)
def list_members(
    org_id: UUID,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
) -> list[MemberResponse]:
    """List all members of a client organisation. Requires altera_admin."""
    _require_altera_admin(auth)
    if store.get_organisation(org_id) is None:
        raise_not_found(f"organisation {org_id} not found")
    return [_member_response(p) for p in store.list_members(org_id)]


# ---------------------------------------------------------------------------
# Members — invite
# ---------------------------------------------------------------------------


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
    """Invite a user to a client organisation.

    Calls the Supabase Auth Admin API server-side to create the auth.users
    row and send the invite email. Pre-provisions user_profiles + memberships
    so the user lands in the correct org and role on first login.

    In dev mode (no Supabase configured) a placeholder UUID is generated and
    invite_sent is returned as False.
    """
    _require_altera_admin(auth)

    org = store.get_organisation(org_id)
    if org is None:
        raise_not_found(f"organisation {org_id} not found")

    client_role = _validate_client_role(body.role)
    redirect_to = _derive_redirect_to(body.redirect_to)

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
        user_id = uuid4()

    profile = UserProfile(
        user_id=user_id,
        organisation_id=org_id,
        email=body.email,
        display_name=body.email.split("@")[0],
        role=client_role,
        created_at=datetime.now(UTC),
    )
    store.upsert_user(profile)

    _emit(
        store,
        organisation_id=org_id,
        actor_id=auth.user_id,
        action=AuditEventType.ORG_MEMBER_INVITED,
        target_id=user_id,
        metadata={"email": body.email, "role": client_role.value, "invite_sent": invite_sent},
    )

    return InviteUserResponse(
        user_id=user_id,
        email=body.email,
        organisation_id=org_id,
        role=client_role.value,
        invite_sent=invite_sent,
    )


# ---------------------------------------------------------------------------
# Members — resend invite
# ---------------------------------------------------------------------------


@admin_router.post(
    "/organisations/{org_id}/members/{user_id}/resend-invite",
    response_model=ResendInviteResponse,
)
def resend_invite(
    org_id: UUID,
    user_id: UUID,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
    settings: AuthSettings = Depends(get_auth_settings),
) -> ResendInviteResponse:
    """Resend an invite (password-recovery link) to an existing member.

    Uses generate_link(type="recovery") so it works for both pending and
    confirmed Supabase Auth users.  Returns invite_sent=False in dev mode.
    """
    _require_altera_admin(auth)

    if store.get_organisation(org_id) is None:
        raise_not_found(f"organisation {org_id} not found")

    members = store.list_members(org_id)
    member = next((m for m in members if m.user_id == user_id), None)
    if member is None:
        raise_not_found(f"member {user_id} not found in organisation {org_id}")

    redirect_to = _derive_redirect_to(None)
    invite_sent = False
    admin_client = get_supabase_admin_client(
        settings.supabase_url, settings.supabase_service_role_key
    )

    if admin_client is not None:
        try:
            admin_client.resend_invite(member.email, redirect_to=redirect_to)
            invite_sent = True
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" in msg or "does not exist" in msg or "422" in msg:
                raise_not_found(
                    f"no Supabase Auth user found for {member.email!r}; "
                    "they may need to be re-invited from scratch"
                )
            raise

    _emit(
        store,
        organisation_id=org_id,
        actor_id=auth.user_id,
        action=AuditEventType.ORG_MEMBER_INVITED,
        target_id=user_id,
        metadata={"email": member.email, "resend": True, "invite_sent": invite_sent},
    )

    return ResendInviteResponse(
        user_id=user_id,
        email=member.email,
        organisation_id=org_id,
        invite_sent=invite_sent,
    )


# ---------------------------------------------------------------------------
# Members — change role
# ---------------------------------------------------------------------------


@admin_router.patch(
    "/organisations/{org_id}/members/{user_id}",
    response_model=MemberResponse,
)
def update_member_role(
    org_id: UUID,
    user_id: UUID,
    body: UpdateMemberRequest,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
) -> MemberResponse:
    """Change a member's role within a client organisation.

    Only ClientRole values are accepted; Altera roles cannot be assigned
    through this endpoint.
    """
    _require_altera_admin(auth)

    if store.get_organisation(org_id) is None:
        raise_not_found(f"organisation {org_id} not found")

    new_role = _validate_client_role(body.role)

    members = store.list_members(org_id)
    member = next((m for m in members if m.user_id == user_id), None)
    if member is None:
        raise_not_found(f"member {user_id} not found in organisation {org_id}")

    old_role = member.role

    updated = UserProfile(
        user_id=member.user_id,
        organisation_id=org_id,
        email=member.email,
        display_name=member.display_name,
        role=new_role,
        created_at=member.created_at,
    )
    store.upsert_user(updated)

    _emit(
        store,
        organisation_id=org_id,
        actor_id=auth.user_id,
        action=AuditEventType.ORG_ROLE_CHANGED,
        target_id=user_id,
        metadata={"old_role": old_role.value, "new_role": new_role.value},
    )

    return _member_response(updated)


# ---------------------------------------------------------------------------
# Members — remove
# ---------------------------------------------------------------------------


@admin_router.delete(
    "/organisations/{org_id}/members/{user_id}",
    status_code=204,
)
def remove_member(
    org_id: UUID,
    user_id: UUID,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_store),
) -> None:
    """Remove a user's membership from a client organisation.

    The Supabase Auth user and user_profiles row are preserved; only the
    memberships row is deleted.  The user can be re-invited at any time.
    """
    _require_altera_admin(auth)

    if store.get_organisation(org_id) is None:
        raise_not_found(f"organisation {org_id} not found")

    members = store.list_members(org_id)
    member = next((m for m in members if m.user_id == user_id), None)
    if member is None:
        raise_not_found(f"member {user_id} not found in organisation {org_id}")

    store.remove_member(user_id, org_id)

    _emit(
        store,
        organisation_id=org_id,
        actor_id=auth.user_id,
        action=AuditEventType.ORG_MEMBER_REMOVED,
        target_id=user_id,
        metadata={"email": member.email, "role": member.role.value},
    )
