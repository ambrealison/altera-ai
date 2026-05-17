# Authentication

Altera AI uses Supabase Auth. This document describes the user flows,
the session model, and how the FastAPI backend validates requests.

## Identity provider

- **Email + password.** Default at MVP.
- **Magic link.** Available at MVP.
- **OAuth (Google).** Available at MVP if the deployment configures
  Google as a Supabase Auth provider.
- **SAML / SSO.** Deferred past MVP.

## Sessions

Supabase issues a JWT access token and a refresh token. The frontend
holds them in `httpOnly` cookies via Supabase's session-handling
helpers; the JWT is sent to the FastAPI backend as a bearer token.

## Backend verification

Every protected FastAPI route depends on an `authed_user` dependency
that:

1. Reads the bearer token from the `Authorization` header.
2. Validates the JWT signature against Supabase's JWKS.
3. Loads the user's organisation memberships from the database in a
   single query (with a short-lived process cache keyed by user id).
4. Returns a `RequestUser` object containing `user_id`,
   `organisations` (with role per org), and `email`.

A request without a valid token returns `401`. A request whose user has
no membership in the organisation referenced by the route returns
`404` (not `403`) to avoid revealing the existence of organisations the
user cannot see.

## Role checks

After authentication, role checks happen via route guards. Altera AI
has **two disjoint role namespaces**, keyed by the organisation's
`organisation_type` (`gms_client` or `altera_internal`); see
[../project/roles.md](../project/roles.md). Guards are role-namespace
aware:

```python
@router.post("/projects")
async def create_project(
    body: CreateProject,
    user: RequestUser = Depends(authed_user),
):
    require_client_role(user, org_id=body.organisation_id, min_role="client_admin")
    ...

@router.post("/projects/{id}/approve")
async def approve_report(
    id: UUID,
    user: RequestUser = Depends(authed_user),
):
    require_altera_role(user, project_id=id, role="altera_methodology_lead")
    ...
```

`require_client_role` and `require_altera_role` both:

- Confirm the user's organisation type matches the namespace.
- Confirm the user's role meets the minimum.
- Return `403` if the role is insufficient.
- Return `404` if the user has no membership in the target
  organisation at all (to avoid leaking existence).

Role checks happen in addition to RLS, not instead of it. Some
sensitive operations (notably report approval) are restricted to a
single role (`altera_methodology_lead`) and not granted to
`altera_admin`; this separation of duties is deliberate.

## Service tokens

For background workers (classification, calculation, exports), the
backend uses a Supabase service role key with **no RLS bypass**. The
worker still authenticates as a specific user-on-behalf-of, by setting
the `request.jwt.claims` PostgreSQL setting to the user's claims
before each operation. This ensures the worker sees the same rows the
triggering user does. RLS is never bypassed in normal operation.

## Password and session security

- Passwords are managed by Supabase Auth; the backend never sees
  them.
- Refresh tokens are rotated on each use.
- A session inactivity timeout is configurable per organisation (MVP
  default: 7 days).
