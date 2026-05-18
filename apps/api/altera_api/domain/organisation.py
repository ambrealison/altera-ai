"""Organisation and user-profile models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BeforeValidator, StringConstraints

from altera_api.domain.common import (
    AlteraRole,
    ClientRole,
    DomainBase,
    NonEmptyStr,
    OrganisationType,
    Role,
    Slug,
)

#: Lightweight email validation. We deliberately use a regex rather than
#: pulling in `email-validator` — the boundary that needs RFC 5321 strictness
#: is Supabase Auth, not the domain model.
Email = Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


def _parse_any_role(v: object) -> Role | ClientRole | AlteraRole:
    if isinstance(v, (Role, ClientRole, AlteraRole)):
        return v
    s = str(v)
    for cls in (Role, ClientRole, AlteraRole):
        try:
            return cls(s)
        except ValueError:
            pass
    raise ValueError(f"unknown role: {s!r}")


#: Union of all role namespaces; validated from any string via _parse_any_role.
AnyRole = Annotated[Role | ClientRole | AlteraRole, BeforeValidator(_parse_any_role)]


class Organisation(DomainBase):
    """The top-level tenant.

    Every multi-tenant entity (project, upload, product, run, audit log,
    …) carries `organisation_id` and is RLS-scoped by it.
    """

    id: UUID
    name: NonEmptyStr
    slug: Slug
    organisation_type: OrganisationType = OrganisationType.GMS_CLIENT
    created_at: datetime


class UserProfile(DomainBase):
    """An authenticated user's profile and their role in a given organisation.

    A user may belong to multiple organisations with different roles in
    each; the profile is therefore scoped to one membership at a time.
    """

    user_id: UUID
    organisation_id: UUID
    email: Email
    display_name: NonEmptyStr
    role: AnyRole
    created_at: datetime
