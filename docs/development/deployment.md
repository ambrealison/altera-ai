# Deployment

This document describes the deployment targets and process. Details are
intentionally light at MVP and will be expanded as the product matures.

## Targets

| Component | Target                              |
|-----------|-------------------------------------|
| Frontend  | Vercel (or any Next.js-compatible host) |
| Backend   | Fly.io / Render / a single VM running Docker (MVP-tolerant) |
| Database  | Supabase project (managed)           |
| Storage   | Supabase Storage                      |
| Workers   | Same Docker image as backend, run with a worker entrypoint |
| AI        | OpenAI API (or a swap-in provider)    |

The MVP does not require a specific cloud provider. Anywhere that can
run a Python container and a Node host will do.

## Environments

- **local** — Supabase CLI local stack, FastAPI dev server, Next.js dev
  server.
- **preview** — created per pull request. Frontend on a Vercel preview;
  backend on a temporary container. Uses a per-branch Supabase project
  or a schema namespace within a shared preview project.
- **staging** — long-lived. Mirrors production configuration. Uses a
  dedicated Supabase project. Loads only synthetic data.
- **production** — customer-facing. Uses a dedicated Supabase project
  per geographic region as needed.

## Configuration

All configuration is via environment variables. No production secret is
read from a file in the image. Secrets live in the host's secret
manager (Vercel env vars for the frontend, the chosen platform's
equivalent for the backend).

## Migrations

- Schema changes are SQL files in `supabase/migrations/`.
- A migration is applied automatically on backend start in preview
  environments and manually (via `supabase db push`) in staging and
  production.
- Migrations must be idempotent and back-compatible across one release:
  a migration that changes a column type does it as two releases
  (rename or shadow column → backfill → swap), never as one breaking
  release.

## Releases

- Each merged PR to `main` produces a release candidate.
- Release tags follow `vYYYY.MM.DD-NN`.
- The methodology, taxonomy, and rules versions are independent of the
  application release version. A release log notes which methodology
  versions are packaged.

## Observability

Phase 28B introduced a structured observability baseline:

### Structured logging

All log output is JSON to stdout. Each line carries:

| Field | Description |
|---|---|
| `ts` | ISO-8601 timestamp |
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `logger` | Python module name |
| `msg` | Log message |
| `request_id` | UUID assigned per HTTP request (echoed in `X-Request-ID` response header) |
| `org_id` | Organisation UUID (when authenticated) |
| `user_id` | User UUID (when authenticated) |
| `method` / `path` / `status` / `duration_ms` | Request fields on `request.complete` lines |

Log level is controlled by the `LOG_LEVEL` environment variable (default: `INFO`).

Sensitive headers (`Authorization`, `Cookie`) and request bodies are never logged.

### Sentry integration (optional)

Set `SENTRY_DSN` to enable Sentry error tracking. If the variable is absent, Sentry is disabled with no runtime cost. The `sentry-sdk` package is an optional dependency; install it separately: `pip install sentry-sdk`.

| Variable | Default | Description |
|---|---|---|
| `SENTRY_DSN` | _(empty)_ | DSN from the Sentry project settings. Empty = disabled. |
| `SENTRY_ENVIRONMENT` | `production` | Environment tag on events (`staging`, `production`). |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.05` | Fraction of transactions captured for performance tracing. |

The `before_send` hook strips `Authorization` and `Cookie` headers before any event reaches Sentry.

### Runbooks

Operational runbooks for common failure scenarios live in
`docs/development/runbooks/`. Current runbooks:

- [upload-failure.md](runbooks/upload-failure.md)
- [job-stuck.md](runbooks/job-stuck.md)
- [export-download-failure.md](runbooks/export-download-failure.md)
- [rls-permission-denied.md](runbooks/rls-permission-denied.md)
- [ai-classification-failure.md](runbooks/ai-classification-failure.md)
- [report-delivery-issue.md](runbooks/report-delivery-issue.md)

### Health check

A liveness endpoint is available at `GET /health` (returns `{"status": "ok"}`).

### Metrics and tracing

Detailed metrics (Prometheus, Datadog) and distributed tracing are deferred to a post-pilot phase.

## Backups

Supabase managed backups are sufficient for MVP. Restoration is
exercised against staging quarterly. Backups are encrypted at rest by
the platform; Altera AI does not store its own copies.

## Rollback

A bad release is rolled back by redeploying the prior image and, if a
migration introduced a problem, applying its reverse migration. The
rollback drill is documented separately in
`docs/development/runbooks/` (to be added as the product matures).

## Security

### Security headers

Phase 30A added a `SecurityHeadersMiddleware` that stamps the following
headers on every HTTP response:

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=(), payment=()` |
| `Cache-Control` | `no-store` (API paths only) |

HSTS (`Strict-Transport-Security`) is intentionally **not** set in the
application — configure it at the reverse proxy or CDN so the
`includeSubDomains` and `preload` directives can be managed correctly.

### CORS

CORS is controlled by the `CORS_ALLOWED_ORIGINS` environment variable
(comma-separated). In production set it to your exact frontend
origin(s). The default (`http://localhost:3000`) is safe only for local
development.

```
# Production example
CORS_ALLOWED_ORIGINS=https://app.altera-ai.com
```

Never set `CORS_ALLOWED_ORIGINS=*` — this is incompatible with
`allow_credentials=True` and will break browser requests.

### Secrets management

| Secret | Where it lives | Notes |
|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Backend env only | Never expose to frontend or commit to source |
| `SUPABASE_JWT_SECRET` | Backend env only | Used to verify Supabase JWTs |
| `OPENAI_API_KEY` | Backend env only | Never expose to frontend |
| `SUPABASE_ANON_KEY` / `PUBLISHABLE_KEY` | Frontend (`NEXT_PUBLIC_*`) | Safe to expose — governed by Supabase RLS |

The `.env.example` files are checked in CI for real-secret patterns. Do
not add real values to example files.

### Signed URLs

- Upload signed URLs expire in **300 seconds** (5 minutes).
- Export download signed URLs expire in **600 seconds** (10 minutes).
  Override by passing `expires_in` to `generate_export_download_url()`.

### Rate limiting

Phase 30B/30C — in-memory sliding-window rate limiter, disabled by default
(`RATE_LIMIT_ENABLED=false`).

| Group    | Default (req/min) | Env var                             | Matched routes                                  |
|----------|-------------------|-------------------------------------|-------------------------------------------------|
| uploads  | 20                | `RATE_LIMIT_UPLOADS_PER_MINUTE`     | POST …/uploads, …/uploads/prepare, …/ingest, …/jobs/validate, …/wwf-ingredients/upload |
| classify | 10                | `RATE_LIMIT_CLASSIFY_PER_MINUTE`    | POST …/classify, …/jobs/classify                |
| exports  | 30                | `RATE_LIMIT_EXPORTS_PER_MINUTE`     | GET …/export, POST …/jobs/export                |
| compute  | 5                 | `RATE_LIMIT_COMPUTE_PER_MINUTE`     | POST …/jobs/calculate, …/scenarios/{id}/run, GET …/comparisons |
| default  | 200               | `RATE_LIMIT_DEFAULT_PER_MINUTE`     | everything else                                 |

**Key selection (Phase 30C):** Requests are keyed by client IP only. Unverified
JWT claims are never used (they are attacker-controlled before signature
verification). `X-Forwarded-For` is only trusted when the direct peer is in
`TRUSTED_PROXIES` (comma-separated CIDR list, empty by default).

```bash
TRUSTED_PROXIES=10.0.0.1,192.168.0.0/16   # example: Fly.io or Cloudflare egress range
RATE_LIMIT_MAX_BUCKETS=100000              # evict oldest beyond this cap
```

Rate-limited responses return `429 Too Many Requests` with a `Retry-After`
header and a structured `error_code: rate_limited` body.

**Production note:** The in-memory limiter is single-process only. For
multi-process or multi-instance deployments (Fly.io, Render, Kubernetes), use a
Redis/Upstash-backed implementation or delegate rate limiting to the edge
(Cloudflare, API gateway).

### CORS fail-closed (Phase 30C)

If `CORS_ALLOWED_ORIGINS` is not set and `ALTERA_DEV_AUTH_ENABLED` is false
(i.e. production mode), the server **refuses to start** with a clear error. This
prevents accidental deployment with the `http://localhost:3000` fallback in
production.

```bash
CORS_ALLOWED_ORIGINS=https://app.altera-ai.com   # required in production
```

### Secret scanning (Phase 30C)

`.gitleaks.toml` in the repo root configures Gitleaks to detect OpenAI API keys,
Supabase service-role keys, and other secrets. Run before every deployment:

```bash
gitleaks detect --source . --config .gitleaks.toml
```

**If a key is found in history:** revoke it in the provider dashboard immediately,
then rewrite history with `git filter-repo --replace-text secrets.txt` before
pushing to any remote.

### Dependency audits

Run before every pilot deployment:

```bash
# Backend Python packages
uv run pip-audit          # or: pip install pip-audit && pip-audit

# Frontend npm packages
cd apps/web && npm audit
```

Enable GitHub Dependabot alerts on the repository to receive automated
dependency vulnerability notifications.

### Pre-pilot security checklist

- [ ] `ALTERA_DEV_AUTH_ENABLED=false` in production
- [ ] `CORS_ALLOWED_ORIGINS` set to production frontend URL only
- [ ] `SUPABASE_SERVICE_ROLE_KEY` stored in secret manager (not .env file)
- [ ] Supabase RLS enabled on all tenant tables (verified by `tests/observability/test_rls_audit.py`)
- [ ] `SENTRY_DSN` set and events are flowing
- [ ] Export download URL expiry ≤ 600 s (verified by security tests)
- [ ] `npm audit` shows no critical/high vulnerabilities
- [ ] `pip-audit` shows no critical/high vulnerabilities
- [ ] Reviewed Supabase Storage bucket policies (no public buckets for `uploads`/`exports`)
- [ ] HSTS configured at reverse proxy with `includeSubDomains`
- [ ] Security headers verified via browser DevTools or `curl -I`

---

## Staging deployment

### Required backend env vars

All of the following must be set in the backend secret manager before starting the container.

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL (`https://<ref>.supabase.co`) |
| `SUPABASE_JWT_SECRET` | Used to verify Supabase JWTs (from project settings) |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend-only service role key — never expose to frontend |
| `SUPABASE_ANON_KEY` | Anon/publishable key for per-request RLS clients |
| `DATABASE_URL` | Direct Postgres URL (used by some Supabase clients; may be empty if using REST only) |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origin(s), e.g. `https://staging.altera-ai.com` |
| `ALTERA_USE_IN_MEMORY_STORE` | `false` for Postgres persistence |
| `ALTERA_DEV_AUTH_ENABLED` | Must be `false` in staging/production |
| `OPENAI_API_KEY` | Required when `ALTERA_AI_PROVIDER=openai` |
| `LOG_LEVEL` | `INFO` (default) — use `DEBUG` temporarily to diagnose issues |
| `SENTRY_DSN` | Sentry ingest URL; leave empty to disable |
| `SENTRY_ENVIRONMENT` | `staging` or `production` |
| `RATE_LIMIT_ENABLED` | `true` for staging/production single-process deployments |
| `TRUSTED_PROXIES` | CIDRs of reverse proxy egress ranges (e.g. Fly.io or Cloudflare) |

Optional rate-limit overrides (defaults shown):

```
RATE_LIMIT_UPLOADS_PER_MINUTE=20
RATE_LIMIT_CLASSIFY_PER_MINUTE=10
RATE_LIMIT_EXPORTS_PER_MINUTE=30
RATE_LIMIT_COMPUTE_PER_MINUTE=5
RATE_LIMIT_DEFAULT_PER_MINUTE=200
RATE_LIMIT_MAX_BUCKETS=100000
```

### Required frontend env vars

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Backend URL, e.g. `https://api.staging.altera-ai.com` |
| `NEXT_PUBLIC_SUPABASE_URL` | Same Supabase project URL as backend |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Anon/publishable key (safe to expose — governed by RLS) |

### Supabase migration deployment

```bash
# Apply all pending migrations to the target project.
supabase db push --project-ref <PROJECT_REF>

# Alternatively, for manual application:
psql "$DATABASE_URL" -f supabase/migrations/<file>.sql
```

Migrations are idempotent and back-compatible across one release. Never apply a migration that drops or renames a column without a two-release shadow-column strategy.

### Supabase Storage bucket checklist

- [ ] `uploads` bucket is **private** (not public)
- [ ] `exports` bucket is **private** (not public)
- [ ] Signed URL expiry: uploads ≤ 300 s, exports ≤ 600 s (configured in `StorageService`)
- [ ] Bucket-level RLS policies match the application RLS policies

### Secret rotation

To rotate a secret:

1. Generate the new value in the provider dashboard (Supabase, OpenAI, Sentry).
2. Add the new value to the secret manager **without removing the old one**.
3. Deploy the new backend version that reads the new env var.
4. Verify the deployment is healthy (`GET /health`, check Sentry for errors).
5. Remove the old secret from the secret manager.

For `SUPABASE_JWT_SECRET` rotation: Supabase rotates the JWT secret automatically when you roll the JWT secret in the dashboard. All existing sessions are invalidated; users must re-login.

If a secret is found committed to git history: revoke it immediately in the provider dashboard, then rewrite history (see `docs/development/ci.md` — Git history note).

### Staging deployment checklist

- [ ] CI passes on the branch being deployed
- [ ] All required backend env vars set in the staging secret manager
- [ ] All required frontend env vars set in the Vercel project settings (or equivalent)
- [ ] `supabase db push` applied to the staging Supabase project
- [ ] `GET /health` returns `{"status": "ok"}` after deploy
- [ ] `/version` returns the expected build version
- [ ] Login flow works end-to-end (Supabase Auth → JWT → API `/me`)
- [ ] At least one test upload processed successfully
- [ ] Sentry events visible in the staging environment
- [ ] `ALTERA_DEV_AUTH_ENABLED=false` confirmed in staging logs
