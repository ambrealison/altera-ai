"""FastAPI dependencies for authentication.

The single source of truth for "who is this request?" is
:func:`authed_user`. Every protected route depends on it; the
``current_user_id`` and ``get_project`` helpers in
``altera_api.api.dependencies`` re-export from it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, Header, HTTPException, status

from altera_api.api.store_factory import get_store
from altera_api.auth.config import AuthSettings, get_auth_settings
from altera_api.auth.errors import (
    DevAuthDisabledError,
    InvalidTokenError,
    MissingProfileError,
    NoMembershipError,
)
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.auth.verifier import verify_supabase_jwt
from altera_api.domain.common import AlteraRole, ClientRole, OrganisationType, Role
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.persistence.protocol import StoreProtocol


def _extract_bearer(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip() or None


def _ensure_profile_and_membership(
    store: StoreProtocol,
    *,
    user_id: UUID,
    email: str,
    fallback_organisation_id: UUID | None = None,
) -> tuple[UserProfile, UUID, Role | ClientRole | AlteraRole, OrganisationType]:
    """Load the user's profile + organisation, auto-provisioning when
    a token is valid but the store has no record yet.

    Returns ``(profile, org_id, role, organisation_type)``.
    """
    profile = store.get_user(user_id)
    if profile is None:
        organisation_id = fallback_organisation_id or store.default_org_id
        organisation: Organisation | None = store.get_organisation(organisation_id)
        if organisation is None:
            raise MissingProfileError(
                f"no organisation {organisation_id} for user {user_id}"
            )
        try:
            default_org_id = store.default_org_id
        except RuntimeError:
            default_org_id = None
        profile = UserProfile(
            user_id=user_id,
            organisation_id=organisation.id,
            email=email or "unknown@example.com",
            display_name=(email or "unknown").split("@", 1)[0],
            role=Role.OWNER if organisation_id == default_org_id else Role.ANALYST,
            created_at=datetime.now(UTC),
        )
        store.upsert_user(profile)

    org = store.get_organisation(profile.organisation_id)
    org_type = org.organisation_type if org is not None else OrganisationType.GMS_CLIENT
    return profile, profile.organisation_id, profile.role, org_type


def _dev_context(settings: AuthSettings, store: StoreProtocol) -> AuthContext:
    if not settings.altera_dev_auth_enabled:
        raise DevAuthDisabledError(
            "ALTERA_DEV_AUTH_ENABLED is not set; dev auth fallback is disabled"
        )
    user_id = settings.altera_dev_user_id or store.default_user_id
    email = settings.altera_dev_user_email or "demo@altera-ai.local"
    fallback_org = settings.altera_dev_organisation_id
    _profile, org_id, role, org_type = _ensure_profile_and_membership(
        store,
        user_id=user_id,
        email=email,
        fallback_organisation_id=fallback_org,
    )
    return AuthContext(
        user_id=user_id,
        email=email,
        organisation_id=org_id,
        role=role,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=org_type,
        raw_token=None,
    )


def _supabase_context(token: str, store: StoreProtocol) -> AuthContext:
    claims = verify_supabase_jwt(token)
    try:
        user_id = UUID(claims["sub"])
    except (ValueError, KeyError) as exc:
        raise InvalidTokenError("token missing valid 'sub' claim") from exc
    email: str = str(claims.get("email") or "")
    profile, org_id, role, org_type = _ensure_profile_and_membership(
        store, user_id=user_id, email=email
    )
    return AuthContext(
        user_id=user_id,
        email=email or profile.email,
        organisation_id=org_id,
        role=role,
        auth_provider=AuthProvider.SUPABASE,
        is_dev_auth=False,
        organisation_type=org_type,
        raw_token=token,
    )


def authed_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: AuthSettings = Depends(get_auth_settings),
    store: StoreProtocol = Depends(get_store),
) -> AuthContext:
    """The authenticated user for this request.

    Resolution order:

    1. ``Authorization: Bearer <token>`` → verify as a Supabase JWT.
    2. No header AND ``ALTERA_DEV_AUTH_ENABLED=true`` → dev fallback.
    3. Otherwise → 401.

    A header with an invalid token always returns 401 — the dev
    fallback is only used when no token is supplied at all, to avoid
    accidentally hiding bad-token bugs.
    """
    token = _extract_bearer(authorization)
    try:
        if token is not None:
            return _supabase_context(token, store)
        return _dev_context(settings, store)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except DevAuthDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (MissingProfileError, NoMembershipError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


def optional_authed_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: AuthSettings = Depends(get_auth_settings),
    store: StoreProtocol = Depends(get_store),
) -> AuthContext | None:
    """Like ``authed_user`` but returns ``None`` instead of raising.

    Reserved for routes that may render an anonymous landing page;
    none of the Phase 12 routes use this — it's here so the contract
    is explicit for later.
    """
    _ = uuid4  # silence "unused" if the import is pruned later
    try:
        return authed_user(authorization=authorization, settings=settings, store=store)
    except HTTPException:
        return None
