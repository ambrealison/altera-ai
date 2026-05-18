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
