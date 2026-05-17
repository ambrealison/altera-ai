# Multi-tenancy

Altera AI is a multi-tenant SaaS. This document describes the tenancy
model, the lifecycle of a tenant, and the cross-cutting consequences.

## Tenancy hierarchy

```
organisation
   └── members (users with a role)
   └── projects
         └── uploads
               └── products
                     └── classifications (per methodology)
         └── runs
               └── calculation_rows
```

The **organisation** is the root of all access control and resource
ownership. A user can belong to multiple organisations; their role
can differ per organisation. A project, upload, product,
classification, and run all belong to exactly one organisation.

## Organisation types

Every organisation has an `organisation_type`:

| Type                | Who           | Role namespace                                                                       |
|---------------------|---------------|--------------------------------------------------------------------------------------|
| `gms_client`        | A retailer    | `client_owner`, `client_admin`, `client_viewer`                                      |
| `altera_internal`   | Altera staff  | `altera_admin`, `altera_analyst`, `altera_reviewer`, `altera_methodology_lead`       |

A membership's role must match its organisation's namespace; this is
enforced by a CHECK constraint and an `ENUM`-backed role column. See
[../project/roles.md](../project/roles.md) for the full permission
matrix.

## Cross-organisation read access

`altera_internal` organisations can be granted scoped read+operate
access to specific `gms_client` projects via
`altera_project_assignments` (one row per Altera user × client
project). RLS joins to this table so that, by default, an
`altera_analyst` sees only assigned client projects. `altera_admin`
and `altera_methodology_lead` see all client projects by virtue of
their role, not by per-row assignment.

`gms_client` organisations never get visibility into anything
outside themselves — neither other clients nor Altera-internal
tables.

## Tenant lifecycle

- **Onboarding (gms_client).** An `altera_admin` creates the client
  organisation and invites the first `client_owner` by email. Clients
  do not self-sign-up in v1.
- **Sign-up (altera_internal).** An `altera_admin` invites new Altera
  staff by email; first Altera org and first `altera_admin` are
  bootstrapped at deployment time.
- **Invite.** Owners and admins (in their respective namespaces)
  invite further users by email. The invite carries the intended
  role.
- **Suspension.** An organisation may be suspended (no reads or writes
  from any member) without being deleted.
- **Deletion.** Deletion is staged: a 30-day soft delete during which
  the data is unreadable but recoverable, then hard purge. Hard purge
  removes uploads, classifications, runs, and audit logs for that
  organisation.

## Storage isolation

- **PostgreSQL.** Every multi-tenant table carries `organisation_id` and
  is covered by RLS policies that scope to the authenticated user's
  organisations; see [rls.md](rls.md).
- **Supabase Storage.** Uploaded files are stored under
  `organisations/<org_id>/uploads/<upload_id>/<filename>`. Storage
  policies enforce the same boundary.
- **Caches.** Any in-process cache key is prefixed with the
  organisation id so a process can serve multiple tenants without
  cross-tenant leak.

## Naming

The `organisations.slug` column is globally unique and is used in URLs
(`/orgs/{slug}/...`). Slugs are case-insensitive, contain only
lowercase letters, digits, and hyphens, and are reserved against a
denylist of well-known names (`admin`, `api`, etc.).

## Billing (deferred)

Billing is not implemented at MVP. The schema reserves columns for it
on `organisations` so that adding billing later is additive, not a
breaking change.

## Cross-tenant features (none at MVP)

There is no cross-organisation sharing of products, taxonomies, or
runs at MVP. The taxonomy is global to Altera AI; an organisation may
choose to override mappings per project but cannot edit the canonical
tree from the UI.
