# Implementation plan

This is the working plan for the next slice of work. It is the
concrete companion to [ROADMAP.md](ROADMAP.md): the roadmap lists
phases through pilot, this document specifies what we actually pick
up next and how.

## Current state (verified 2026-05-16)

- **Phase 13A** (Supabase schema + RLS + auth trigger): done.
- **Phase 13B** (Postgres persistence): **not done**. All routes
  still use `InMemoryStore` via `apps/api/altera_api/api/store_factory.py`.
- **Phase 13C** (Supabase Auth): ~95% done. Backend JWT verification,
  `/me`, cross-tenant 404, dev fallback, frontend Bearer attachment,
  AuthGate, login page all merged. 512 backend tests pass; ruff clean.
- **Phase 13D** (Storage): not started.

## Outstanding before the next big phase

### A. Phase 13C polish (~30 min, low risk)

Required to make the codebase honestly describe what it is.

1. **`apps/api/altera_api/version.py`** — update phase string from
   `phase_13a_supabase_schema` to `phase_13c_supabase_auth`.
2. **`apps/api/tests/api/test_health.py`** — update the
   corresponding assertion.
3. **`apps/api/.env.example`** — add `SUPABASE_URL`,
   `SUPABASE_JWT_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`,
   `ALTERA_DEV_AUTH_ENABLED`, `ALTERA_DEV_USER_ID`,
   `ALTERA_DEV_ORGANISATION_ID`, `ALTERA_DEV_USER_EMAIL`.
4. **`apps/web/.env.example`** — add `NEXT_PUBLIC_SUPABASE_URL`,
   `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_BASE_URL`.
5. **`apps/api/README.md`** — auth setup section (env vars, dev
   fallback, JWT verification).
6. **`apps/web/README.md`** — Supabase client setup, login flow, dev
   fallback note.
7. **`supabase/README.md`** — note that the `handle_new_user` trigger
   is mirrored by backend auto-provisioning so JWTs minted before
   the trigger ran still work.
8. Run `pytest`, `ruff`, `tsc --noEmit`, `eslint`; one browser smoke.

### B. Domain model groundwork for the product direction (low risk, test-covered)

These can land **before or alongside** Phase 13B because they are
additive schema and pure-function code. They unlock Phases 14–17
without changing route behaviour yet.

1. **`organisations.organisation_type`** enum
   (`gms_client | altera_internal`), default `gms_client` for
   existing rows; backfill the singleton dev org accordingly.
2. **`projects.project_status`** enum (the 12-state lifecycle in
   [../saas/workflow.md](../saas/workflow.md)), default `created`.
3. **`report_exports`** — add `approval_status`, `approved_by`,
   `approved_at`, `delivered_to_client_at`. Default
   `approval_status = 'draft'`.
4. **`manual_reviews.owner_type`** enum (`altera_internal` only in
   v1), default `altera_internal`.
5. **Pure functions in `altera_api/domain/project_lifecycle.py`** —
   `allowed_transitions(status)`, `validate_transition(from, to)`,
   `client_facing_status(internal_status)`.
6. **Pure function in `altera_api/domain/report_approval.py`** —
   `can_approve(role)`, `can_download(role, approval_status)`.
7. **Tests** for each pure function (every transition; every
   denied transition; every namespace/role combination).

No route changes yet; the API surface for these models lands with
Phase 15/17.

## Phase 13B — Postgres persistence (next big phase)

### Outcome
- Every route that today reads/writes `InMemoryStore` reads/writes
  Supabase Postgres via a repository layer.
- `InMemoryStore` survives only as a test/dev fallback behind a
  feature flag (`ALTERA_USE_IN_MEMORY_STORE=true`), used by unit
  tests and the dev-auth path.

### Approach
- Add a `Repository` Protocol in `altera_api/persistence/__init__.py`
  describing the operations every route needs.
- Implement `PostgresRepository` using `supabase-py` (or `asyncpg`
  for lower latency); the choice will be made after a 1-hour spike.
- Implement `InMemoryRepository` wrapping the existing
  `InMemoryStore` against the same Protocol.
- Replace `Depends(get_store)` with `Depends(get_repository)`;
  remove direct `InMemoryStore` references from route code.
- Service-role key is used for writes, with `request.jwt.claims` set
  per-request so RLS still applies (mirrors the pattern in
  [../saas/auth.md](../saas/auth.md)).

### Testing
- Existing API tests run against `InMemoryRepository` (fast path).
- New integration suite under `apps/api/tests/integration/` runs
  against a real local Supabase Postgres with the RLS migrations
  applied, exercising cross-tenant isolation at the database layer.
- CI runs both suites.

### Out of scope for 13B
- Storage (raw CSVs) — that is 13D.
- Worker / background job runner — calculations stay synchronous
  for now.

## Phase 13D — Supabase Storage (after 13B)

### Outcome
- CSV uploads land in Supabase Storage under
  `organisations/<org_id>/uploads/<upload_id>/<filename>` via signed
  URLs from the frontend.
- Report exports are written to Storage and downloaded via signed
  URLs.
- The `InMemoryStore`'s "bytes column" goes away.

### Approach
- Frontend obtains a signed upload URL from the API
  (`POST /uploads`), uploads directly to Supabase Storage, then
  notifies the API (`POST /uploads/{id}/parse`).
- API parses by streaming the Storage object, not by holding raw
  bytes in memory.
- Report-export endpoints write to Storage and return signed
  download URLs; the download endpoint validates approval before
  issuing the URL.

## Product-workflow milestone (Phases 14–17, after 13D)

These four phases share schema migrations and a feature-flag rollout.
They land together behind `ALTERA_MANAGED_SAAS=true` (default off
until cut over) so that the in-progress changes don't break the
current single-namespace API:

- **Phase 14** — `organisation_type` + role-namespace split.
- **Phase 15** — Project lifecycle state machine on the API
  (`POST /projects/{id}/transitions`).
- **Phase 16** — Internal-operator UI (review queue + lifecycle
  board).
- **Phase 17** — Report approval gate (approve / reject / deliver,
  download gating).

Section **B** above (domain model groundwork) lands the schema and
the pure functions; this milestone lands the routes, the RLS
policies that depend on `organisation_type`, and the UI.

## Open questions

- **supabase-py vs asyncpg for the repository layer.** Trade-off is
  ergonomics (supabase-py composes with RLS via JWT pass-through)
  vs. latency (asyncpg is faster). Resolved by a 1-hour spike at the
  start of 13B.
- **How impersonation works for Altera staff.** An `altera_admin`
  needs to occasionally see the client UI as the client would. The
  current plan is an explicit "view as client" toggle that sets a
  banner and limits actions to read; details deferred to Phase 18.
- **Multi-project Altera assignments.** `altera_project_assignments`
  is described in `multi-tenancy.md` but not yet in the schema;
  added in Phase 14.

## What is explicitly NOT in this plan

- Recommendation engine — see
  [../future/recommendation-engine.md](../future/recommendation-engine.md).
  Not before Phase 30.
- AI changes — the AI classifier and its strict-JSON contract are
  unchanged.
- Methodology calculation changes — PT and WWF calculation modules
  are frozen for this milestone.
- Removing the `InMemoryStore` entirely — it survives as the
  fast-path test repository.
