"""FastAPI dependencies.

* :func:`get_store` — service-role singleton repository.
* :func:`get_data_store` — per-request JWT-scoped repository for data ops.
* :func:`get_project` — project lookup scoped to the caller's visible orgs.
* :func:`current_user_id` — thin auth-context helper.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, status

from altera_api.api.store_factory import get_store
from altera_api.auth import AuthContext, authed_user
from altera_api.domain.project import Project
from altera_api.persistence.factory import PersistenceSettings, get_repository
from altera_api.persistence.protocol import StoreProtocol

__all__ = ["current_user_id", "get_data_store", "get_project", "get_store"]


def get_data_store(
    auth: AuthContext = Depends(authed_user),
    base_store: StoreProtocol = Depends(get_store),
) -> StoreProtocol:
    """Per-request JWT-scoped repository for tenant data operations.

    In memory mode returns *base_store* so test overrides propagate.
    In Postgres mode builds a RLS-enforced client from the user's JWT.
    """
    settings = PersistenceSettings()
    if settings.altera_use_in_memory_store:
        return base_store
    return get_repository(user_jwt=auth.raw_token)


def get_project(
    project_id: UUID,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_data_store),
) -> Project:
    """Fetch a project visible to the authenticated user.

    Altera staff can see projects in any organisation (cross-org
    visibility introduced in Phase 14).  Client users are scoped to
    their own organisation; a project in another org is indistinguishable
    from a missing project (returns 404, not 403).
    """
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id} not found",
        )
    if not auth.is_altera_internal and project.organisation_id != auth.organisation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id} not found",
        )
    return project


def current_user_id(auth: AuthContext = Depends(authed_user)) -> UUID:
    """Returns the authenticated user's id."""
    return auth.user_id
