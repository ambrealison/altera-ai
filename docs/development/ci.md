# CI / CD

Phase 31A added a GitHub Actions CI pipeline at `.github/workflows/ci.yml`.

## Jobs

| Job | What it runs | When it blocks merge |
|-----|--------------|----------------------|
| `backend` | `pytest --ignore=tests/integration` + `ruff check` | Always |
| `frontend` | `tsc --noEmit` + `next lint` + `next build` | Always |
| `security` | `gitleaks detect --no-git` + secret-safety pytest suite | Always |

All three jobs run in parallel on every push to `main`/`staging` and on every pull request. Concurrent runs for the same branch are cancelled automatically.

## Local quality check

Run all CI checks locally before pushing:

```bash
./scripts/check_all.sh
```

Or run individual checks:

```bash
# Backend
cd apps/api
uv run pytest --ignore=tests/integration -q
uv run ruff check .

# Frontend
pnpm typecheck:web
pnpm lint:web
pnpm build:web
```

## Integration tests

Integration tests live in `apps/api/tests/integration/` and require a live Supabase stack. They are skipped by default in CI via `--ignore=tests/integration`.

To run them locally or on a staging runner, set the following env vars, then run:

```bash
export SUPABASE_URL=https://<project>.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=<service-role-key>
export SUPABASE_ANON_KEY=<anon-key>
export SUPABASE_JWT_SECRET=<jwt-secret>
export ALTERA_USE_IN_MEMORY_STORE=false

cd apps/api
uv run pytest tests/integration -m integration -v
```

To enable integration tests on a GitHub Actions runner, add these as [repository secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions) and add a separate `integration` job with `if: secrets.SUPABASE_URL != ''`.

## Migration / RLS audit

The RLS audit tests (`tests/observability/test_rls_audit.py`) and migration shape tests (`tests/supabase/test_migrations.py`) run against SQL source only — no live database required. They are included in the default `pytest` run and will catch:

- A new table added to migrations without RLS enabled
- A new table without at least one `CREATE POLICY` statement
- Missing migration files or malformed filenames

## Secret scanning

The `security` job installs `gitleaks` and runs:

```
gitleaks detect --source . --config .gitleaks.toml --no-git
```

`--no-git` scans file content in the working tree. It does **not** scan git history.

### Git history note

A real OpenAI API key was leaked in commit `27205ca` and was **revoked** at the OpenAI dashboard. The key no longer works. **History has been rewritten** (Phase 31E, 2026-05-19) using `git filter-repo --replace-text` — the key no longer appears in any commit. All commit hashes changed; the pre-rewrite state is archived at `/tmp/altera_backup_pre_cleanup.bundle`.

See the full step-by-step runbook:
[`docs/development/runbooks/git-history-secret-cleanup.md`](runbooks/git-history-secret-cleanup.md)

Quick working-tree check (does not scan history):

```bash
./scripts/verify_no_tracked_secrets.sh
```

Full history scan (requires gitleaks installed):

```bash
gitleaks detect --source . --config .gitleaks.toml
```

## Staging smoke test workflow

`.github/workflows/staging-smoke.yml` is a manual `workflow_dispatch`
workflow. Trigger it from GitHub Actions UI after deploying to staging:

1. Go to **Actions → Staging smoke test → Run workflow**.
2. Enter the backend URL and optionally the frontend URL.
3. The workflow runs `scripts/staging_smoke.sh` which checks `/health`,
   `/version`, and `/api/v1/me` (expects 401).

No deployment secrets are required — the smoke test only hits public
endpoints.

Run the same check locally:

```bash
API_BASE_URL=https://api.staging.altera-ai.com \
WEB_BASE_URL=https://staging.altera-ai.com \
./scripts/staging_smoke.sh
```

## Caches

| Cache | Key |
|-------|-----|
| uv | `uv-${{ runner.os }}-${{ hashFiles('apps/api/uv.lock') }}` |
| pnpm store | managed by `pnpm/action-setup@v4` + `actions/setup-node@v4 cache: pnpm` (Node 22) |

## Remote

The canonical private remote is `https://github.com/ambrealison/altera-ai` (PRIVATE).
Set up 2026-05-19 (Phase 31F). To clone:

```bash
git clone https://github.com/ambrealison/altera-ai.git
```

## Node version requirement

The frontend job runs on **Node 22** (bumped from 20 in Phase 31F). `pnpm@11.1.2`
requires Node ≥ 22.13 and will crash on Node 20 with `ERR_UNKNOWN_BUILTIN_MODULE:
node:sqlite`. If updating pnpm, check the minimum Node version before bumping.

## Adding a new check

1. Add the command to `.github/workflows/ci.yml` under the appropriate job.
2. Add the same command to `scripts/check_all.sh`.
3. Document any new env vars required in `docs/development/deployment.md`.
