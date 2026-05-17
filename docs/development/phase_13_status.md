# Phase 13 Status

Verified 2026-05-17.

## Summary

| Sub-phase | Scope | Status |
|-----------|-------|--------|
| 13A | Supabase schema, RLS, seed | **Complete** |
| 13B | Postgres persistence layer | **Complete** |
| 13C | Supabase Auth (backend + frontend) | **Complete** |
| 13D | Supabase Storage (uploads + exports) | **Complete** |

---

## 13A — Supabase schema, RLS, seed

**Complete.**

- Migrations 0001–0016 in `supabase/migrations/`.
- RLS enabled on every multi-tenant table.
- Storage buckets `uploads` and `exports` created (0010); upload bucket
  constraints tightened (0016).
- RLS policies on `storage.objects` for both buckets (0011).
- `supabase/seed.sql` seeds version registry + demo org.
- `supabase/tests/rls/` contains pgTAP isolation, immutability, and
  role-permission tests.
- No forbidden commercial fields (`revenue`, `margin`, `supplier_*`, etc.)
  in any table. Regression test in `tests/supabase/test_migrations.py`.

---

## 13B — Postgres persistence layer

**Complete.**

### What was done

- `altera_api/persistence/protocol.py` — `StoreProtocol` (typing.Protocol,
  runtime-checkable). Covers projects, uploads, products, classifications,
  review queue, runs, export records, and audit.
- `altera_api/persistence/memory.py` — `MemoryRepository = InMemoryStore`.
- `altera_api/persistence/postgres.py` — `PostgresRepository` backed by
  supabase-py v2 service-role client.
- `altera_api/persistence/mappers.py` — full bidirectional row↔domain conversions.
- `altera_api/persistence/factory.py` — `get_repository()` reads
  `ALTERA_USE_IN_MEMORY_STORE` (default `true`); `false` activates
  `PostgresRepository`.
- `altera_api/api/store_factory.py` — `get_store() → get_repository()`.
- All routes and orchestrator use `StoreProtocol`; no direct dict access.
- `tests/integration/test_postgres_repository.py` — 5 integration tests
  skipped unless `SUPABASE_URL` is set.

### Default behaviour

In-memory store is the default (`ALTERA_USE_IN_MEMORY_STORE=true`).
Set `false` + provide `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` for
Postgres.

---

## 13C — Supabase Auth

**Complete.**

### Backend

- `altera_api/auth/verifier.py` — HS256 JWT verification via PyJWT.
- `altera_api/auth/dependency.py` — `authed_user` FastAPI dependency:
  verifies JWT, loads/auto-provisions `UserProfile`, exposes `AuthContext`.
- Cross-tenant isolation: every resource lookup scoped to
  `auth.organisation_id`; mismatches return 404 (not 403).
- Dev fallback: only active when `ALTERA_DEV_AUTH_ENABLED=true` **and** no
  `Authorization` header present. Invalid tokens always return 401.
- `/api/v1/me` returns the full `AuthContext`.

### Frontend

- `apps/web/utils/supabase/{client,server,middleware}.ts` — SSR-compatible
  Supabase clients (`@supabase/ssr`).
- `apps/web/middleware.ts` — Next.js root middleware for session refresh.
- `apps/web/lib/auth-context.tsx` — `AuthProvider` + `useAuth()` hook.
- `apps/web/lib/supabase.ts` — browser client, supports
  `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` with `ANON_KEY` fallback.
- `/login` page with Supabase email-password sign-in; "not configured"
  banner when env vars absent.
- `AuthGate` component — redirects unauthenticated users to `/login`.
- `Authorization: Bearer <token>` attached to every API call via `createApi()`.

### Security

- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` is the public anon key — safe to
  expose in browser bundles (no tenant data access without RLS-scoped JWT).
- `SUPABASE_SERVICE_ROLE_KEY` exists only in backend env; never in frontend
  files or build output.
- `.env.local` files are gitignored at the repo root.

---

## 13D — Supabase Storage

**Complete.**

### Raw upload flow

1. Frontend calls `POST /api/v1/projects/{id}/uploads/prepare` — backend
   reserves an `upload_id`, generates a signed upload URL via
   `StorageService.generate_upload_url()`.
2. Browser PUTs the file directly to Supabase Storage at
   `organisations/{org_id}/uploads/{project_id}/{upload_id}/{filename}`.
3. Frontend calls `POST /api/v1/projects/{id}/uploads/{upload_id}/ingest` —
   backend downloads from storage via `StorageService.download()`, runs the
   ingestion pipeline, returns `UploadResponse`.
4. Fallback: when storage is not configured, the existing
   `POST /projects/{id}/uploads` (multipart) route still works.

### Export flow

1. `GET /api/v1/projects/{id}/runs/{run_id}/export?fmt=csv|json|md` renders
   bytes in memory via the existing export renderer.
2. When storage is configured, the bytes are uploaded to
   `organisations/{org_id}/exports/{run_id}/{export_id}/{filename}` in the
   `exports` bucket, an `ExportRecord` is persisted (in-memory or DB), and
   the response is a 302 redirect to a signed download URL.
3. When storage is not configured, bytes are returned directly (existing
   dev-mode behaviour).

### Infrastructure

- `altera_api/storage/service.py` — `StorageService` with `generate_upload_url`,
  `download`, `upload_export`, `generate_export_download_url`.
- `altera_api/storage/factory.py` — `get_storage_service()` FastAPI dependency;
  returns `None` when Supabase is not configured.
- `altera_api/api/state.py` — `ExportRecord` dataclass.
- `StoreProtocol` / `InMemoryStore` / `PostgresRepository` all implement
  `add_export_record` / `get_export_record`.
- `supabase/migrations/0016_storage_uploads.sql` — tightens upload bucket
  constraints (file size limit, MIME type allowlist).

### Storage paths

| Object type | Path |
|---|---|
| Raw upload | `organisations/{org_id}/uploads/{project_id}/{upload_id}/{filename}` |
| Export | `organisations/{org_id}/exports/{run_id}/{export_id}/{filename}` |

### RLS

Storage RLS policies in `0011_rls_policies.sql`:
- `uploads` bucket: org members can select/insert on their org prefix;
  `analyst+` required for insert.
- `exports` bucket: org members can select on their org prefix; insert
  is service-role only (backend worker).

---

## Known gaps (deferred to Phase 14+)

- Per-request JWT RLS enforcement in `PostgresRepository` (currently uses
  service-role key — org scoping is enforced in application code).
- Export approval workflow (`report_exports.approval_status`; currently
  all exports are immediately accessible).
- Hard-delete sweep for soft-deleted orgs.
- Background worker for async export generation.
