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

A real OpenAI API key was leaked in commit `27205ca` and has been **revoked** at the OpenAI dashboard. Before pushing this repository to any public remote, the commit must be removed from history:

```bash
# 1. Install git-filter-repo: pip install git-filter-repo
# 2. Create a replacements file:
echo "sk-proj-CQiwScbwV6cWNAcnNpUcYektx4nO...==>REDACTED" > /tmp/secrets.txt
# 3. Rewrite history:
git filter-repo --replace-text /tmp/secrets.txt --force
# 4. Force-push (requires all collaborators to re-clone):
git push --force-with-lease origin main
```

To scan git history locally (before making the repo public):

```bash
gitleaks detect --source . --config .gitleaks.toml
```

## Caches

| Cache | Key |
|-------|-----|
| uv | `uv-${{ runner.os }}-${{ hashFiles('apps/api/uv.lock') }}` |
| pnpm store | managed by `pnpm/action-setup@v4` + `actions/setup-node@v4 cache: pnpm` |

## Adding a new check

1. Add the command to `.github/workflows/ci.yml` under the appropriate job.
2. Add the same command to `scripts/check_all.sh`.
3. Document any new env vars required in `docs/development/deployment.md`.
