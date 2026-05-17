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

MVP observability is intentionally minimal:

- Application logs go to the host's log stream (Fly, Render, or
  CloudWatch as appropriate).
- A health endpoint at `/healthz` reports DB connectivity and
  configuration sanity.
- A high-severity audit event (`commercial_data_block`) triggers an
  alert through whatever channel the deployment chooses (PagerDuty,
  Slack webhook, email).

Detailed metrics and tracing are deferred past MVP.

## Backups

Supabase managed backups are sufficient for MVP. Restoration is
exercised against staging quarterly. Backups are encrypted at rest by
the platform; Altera AI does not store its own copies.

## Rollback

A bad release is rolled back by redeploying the prior image and, if a
migration introduced a problem, applying its reverse migration. The
rollback drill is documented separately in
`docs/development/runbooks/` (to be added as the product matures).
