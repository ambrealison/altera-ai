# supabase/

Production database foundation for Altera AI: schema, RLS policies,
storage buckets, audit-immutability triggers, seed data, and
pgTAP-style RLS tests.

This directory covers **Phase 13A** (the SQL contract). All four Phase
13 sub-phases are now complete:

- **13A** (done): This directory — schema, RLS, storage bucket creation, seed.
- **13B** (done): Postgres-backed store in `apps/api/altera_api/persistence/`.
- **13C** (done): Supabase Auth + JWT verification + frontend login.
  See `apps/api/altera_api/auth/` and `apps/web/lib/auth-context.tsx`.
- **13D** (done): Supabase Storage for raw CSV uploads and generated
  exports. See `apps/api/altera_api/storage/`.

See `docs/development/phase_13_status.md` for the full audit record.

Notes on each at the bottom.

> **Backend auto-provisioning:** The `handle_new_user()` SQL trigger
> creates a `user_profiles` row when a new auth user signs up via
> Supabase. The 13C backend mirrors this in application code so that
> a JWT minted before the trigger ran (or against an in-memory dev
> store that has no profile yet) still authenticates — the
> `authed_user` dependency auto-provisions a profile on the demo org
> when one is missing.

## Layout

```
supabase/
├── README.md                                this file
├── config.toml                              Supabase CLI project config
├── seed.sql                                 version registry + demo org/user
├── migrations/
│   ├── 0001_extensions_and_helpers.sql      pgcrypto, citext, pg_trgm; helper stubs
│   ├── 0002_tenants.sql                     organisations, memberships, reserved slugs
│   ├── 0003_projects.sql
│   ├── 0004_uploads.sql
│   ├── 0005_products.sql                    products + product_composite_ingredients
│   ├── 0006_classifications.sql             current + event log
│   ├── 0007_manual_reviews.sql              soft-lock review queue
│   ├── 0008_calculation_runs.sql            calculation_runs + calculation_rows
│   ├── 0009_audit_events.sql                audit_events + report_exports
│   ├── 0010_storage_buckets.sql             uploads + exports buckets
│   ├── 0011_rls_policies.sql                helper bodies + RLS on every multi-tenant table
│   ├── 0012_audit_immutability.sql          append-only triggers
│   ├── 0013_user_profiles.sql               user_profiles + auth.users signup trigger
│   └── 0014_version_registry.sql            methodology / taxonomy / rules versions
└── tests/rls/                               pgTAP tests
    ├── 001_org_isolation.sql
    ├── 002_audit_immutability.sql
    └── 003_role_permissions.sql
```

## Tables created

| Table | Tenant-scoped | Notes |
|---|:--:|---|
| `organisations` | (root) | Top-level tenant. |
| `memberships` | yes | Joins `auth.users` × organisations with a role. |
| `user_profiles` | no | One row per `auth.users` (display name, email, locale). |
| `projects` | yes | Pins methodologies + reporting period. |
| `uploads` | yes | One CSV per row; `storage_path` points into Supabase Storage. |
| `products` | yes | Normalised products; PT + WWF column blocks. |
| `product_composite_ingredients` | yes | Step-2 ingredient weights for own-brand composites. |
| `classifications` | yes | Current classification per (product, methodology). |
| `classification_events` | yes | Append-only history (UPDATE/DELETE rejected). |
| `manual_reviews` | yes | Soft-lock review queue. |
| `calculation_runs` | yes | Versioned run header. |
| `calculation_rows` | yes | Per-product calculation outputs for a run. |
| `audit_events` | yes | Append-only general audit trail. |
| `report_exports` | yes | One row per generated CSV/JSON/Markdown artefact. |
| `methodology_versions` | **global** | Read-only registry; service-role manages writes. |
| `taxonomy_versions` | **global** | ditto |
| `rules_versions` | **global** | ditto |
| `reserved_slugs` | — | Static allow-list lookup for org slug validation. |

## Apply locally

```bash
# One-time: install the Supabase CLI.
brew install supabase/tap/supabase

# Start the local stack (Postgres + Auth + Storage on 54321..54323).
supabase start

# Apply migrations + seed.
supabase db reset
```

After `db reset` the local Postgres has the full schema, RLS enabled
on every multi-tenant table, the three version registries populated,
and a demo organisation. Create the demo user:

```bash
supabase auth users create demo@altera-ai.local --password demo-password
supabase db reset      # attaches the demo membership to the new user
```

## Apply to a hosted Supabase project

```bash
supabase link --project-ref <your-project-ref>
supabase db push       # applies pending migrations
```

Storage buckets `uploads` and `exports` are created by migration 0010.

## Service-role usage

A handful of operations bypass RLS by definition:

- **`organisations` INSERT** — creating a new tenant. No RLS policy
  permits an authenticated user to insert an organisation; a
  service-role-only signup endpoint (Phase 13B) handles it.
- **`methodology_versions` / `taxonomy_versions` / `rules_versions`
  INSERT** — these are shipped with the application, not tenant data.
  A migration adds new versions; the service role can also INSERT
  directly during a rolling upgrade.
- **`user_profiles` INSERT** — handled automatically by the
  `handle_new_user()` `SECURITY DEFINER` trigger on `auth.users`. No
  application code touches `user_profiles` insert directly.
- **Backups, soft-delete sweeps, hard purge** — administrative
  operations.

Every other RLS-protected table requires a user JWT and is scoped to
the user's memberships. The FastAPI backend's worker code (Phase 13B+)
must NOT use the service role for tenant operations; instead it sets
`request.jwt.claims` to impersonate the originating user, so RLS
applies normally. See `docs/saas/auth.md`.

## RLS policy summary (table-by-table)

| Table | SELECT | INSERT | UPDATE | DELETE |
|---|---|---|---|---|
| `organisations` | members of org | service role | owner/admin | service role |
| `memberships` | self **or** org members | owner/admin | owner/admin | owner/admin |
| `user_profiles` | self **or** shared-org members | trigger on `auth.users` | self | cascade |
| `projects` | members of org | analyst+ | analyst+ | owner/admin |
| `uploads` | members of org | analyst+ | analyst+ | (cascade) |
| `products` | members of org | analyst+ | analyst+ | (cascade) |
| `product_composite_ingredients` | members of org | analyst+ | analyst+ | (cascade) |
| `classifications` | members of org | analyst+/reviewer | analyst+/reviewer | (cascade) |
| `classification_events` | members of org | analyst+/reviewer | **trigger rejects** | **trigger rejects** |
| `manual_reviews` | members of org | analyst+/reviewer | analyst+/reviewer | analyst+/reviewer |
| `calculation_runs` | members of org | analyst+ | analyst+ | (cascade) |
| `calculation_rows` | members of org | analyst+ | — | (cascade) |
| `audit_events` | analyst+ in org | members of org | **trigger rejects** | **trigger rejects** |
| `report_exports` | members of org | any role | service role | (cascade) |
| `methodology_versions` | any authenticated | service role | service role | service role |
| `taxonomy_versions` | any authenticated | service role | service role | service role |
| `rules_versions` | any authenticated | service role | service role | service role |
| `storage.objects` (uploads bucket) | members of org (path-prefix) | analyst+ (path-prefix) | analyst+ | analyst+ |
| `storage.objects` (exports bucket) | members of org (path-prefix) | service role (worker) | service role | service role |

"analyst+" = `{owner, admin, analyst}`. "analyst+/reviewer" =
`{owner, admin, analyst, reviewer}`. "(cascade)" means the table has
no explicit DELETE policy because rows are deleted via `ON DELETE
CASCADE` from the parent. Storage paths are
`organisations/<org_id>/(uploads|exports)/...`.

## Forbidden commercial fields

No table in this schema carries `revenue`, `margin`, `cost_price`,
`sales_value`, `supplier_*`, `store_*`, `promotion_*`, `confidential_*`,
or `internal_*` columns. The ingestion pipeline drops these at the
boundary (Phase 5), and `tests/supabase/test_migrations.py` has a
regression test that scans every migration for any of those names.

`items_purchased`, `items_sold`, `weight_per_item_kg`, and `protein_pct`
**are** stored — they are physical methodology quantities, not
commercial data. They live in the database for calculation but never
appear in an outbound AI prompt (Phase 7's allow-list enforces that).

## Run RLS tests

```bash
pg_prove --host=localhost --port=54322 --username=postgres --dbname=postgres \
         --recurse supabase/tests/rls
```

The Python migration-loader test
(`apps/api/tests/supabase/test_migrations.py`) catches schema-shape
regressions without needing a running Postgres; the pgTAP suite is the
behavioural contract and runs in CI on every PR touching
`supabase/migrations/`.

## What 13A does NOT include

13A is a pure schema / RLS phase. The following shipped in later phases:

- **Backend wiring to Postgres** — landed in 13B. See
  `apps/api/altera_api/persistence/`.
- **Supabase Auth in the frontend** — landed in 13C. See
  `apps/api/altera_api/auth/` and `apps/web/lib/auth-context.tsx`.
- **Storage upload/download via signed URLs** — landed in 13D. See
  `apps/api/altera_api/storage/`.
- **Background workers** — deferred past Phase 13.

## Phase 13B (done): Postgres persistence

- `StoreProtocol` (typing.Protocol) in `persistence/protocol.py`.
- `MemoryRepository` alias for `InMemoryStore` — default dev/test path.
- `PostgresRepository` backed by supabase-py v2 (service-role key).
- Feature flag `ALTERA_USE_IN_MEMORY_STORE` (default `true`).
- Integration tests in `apps/api/tests/integration/` (skipped without
  `SUPABASE_URL`).

## Phase 13C (done): Supabase Auth

- Supabase Auth + email-password sign-in in `apps/web` (`/login`).
- `authed_user` FastAPI dependency: verifies HS256 JWTs against
  `SUPABASE_JWT_SECRET`, exposes an `AuthContext`, and falls back to
  a dev user only when `ALTERA_DEV_AUTH_ENABLED=true` AND no
  `Authorization` header is present.
- `/api/v1/me` returns the current `AuthContext`.
- Cross-tenant resource access returns 404 (not 403) to avoid
  disclosing other-tenant rows.
- See `apps/api/README.md` and `apps/web/README.md` for setup.

## Phase 13D (done): Supabase Storage

- Two-step upload: `POST /uploads/prepare` → signed URL → browser PUT
  → `POST /uploads/{id}/ingest` reads from storage.
- Export route (`GET /runs/{id}/export`) uploads rendered bytes to the
  `exports` bucket and returns a signed download URL (302 redirect).
- `ExportRecord` persisted in `report_exports` table.
- Storage paths: `organisations/{org_id}/uploads/...` and
  `organisations/{org_id}/exports/...`.
- RLS policies on both buckets in `0011_rls_policies.sql`.
- See `apps/api/altera_api/storage/` and
  `docs/development/phase_13_status.md`.
