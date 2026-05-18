# Integration tests

Integration tests in this directory connect to a real Postgres instance with Supabase extensions (RLS, auth helpers). They are excluded from the default `pytest` run to keep the CI feedback loop fast.

## Running locally

### Prerequisites

1. Supabase CLI installed: `brew install supabase/tap/supabase`
2. Docker running
3. `.env` configured (copy from `.env.example` and fill in the Supabase values)

### Start the local Supabase stack

```sh
cd /path/to/altera-ai
supabase start        # starts Postgres + Auth + Storage on localhost
supabase status       # prints SUPABASE_URL, SUPABASE_ANON_KEY, etc.
```

Copy the printed values into your `.env` file.

### Run integration tests

```sh
cd apps/api
uv run pytest tests/integration/ -v
```

To run integration tests alongside the unit suite in one pass:

```sh
uv run pytest -v   # runs everything (slower)
```

### Tearing down

```sh
supabase stop
```

## CI / staging

Integration tests should run in CI on a dedicated staging Supabase project, not the production project. Set the following secrets in your CI environment:

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Staging project URL |
| `SUPABASE_JWT_SECRET` | JWT secret from staging project |
| `SUPABASE_SERVICE_ROLE_KEY` | Service-role key (bypasses RLS for setup) |
| `SUPABASE_ANON_KEY` | Anon/publishable key (used for RLS-scoped queries) |
| `DATABASE_URL` | Direct Postgres connection string |
| `ALTERA_USE_IN_MEMORY_STORE` | `false` |

Run the integration suite in CI after the unit suite:

```yaml
- name: Integration tests
  run: uv run pytest tests/integration/ -v
  env:
    ALTERA_USE_IN_MEMORY_STORE: "false"
    SUPABASE_URL: ${{ secrets.STAGING_SUPABASE_URL }}
    # ... etc.
```

## Test isolation

Each test should create its own fixture data under a test organisation UUID and clean up after itself (either via `teardown` or by wrapping the test in a database transaction that is rolled back). The `test_postgres_repository.py` file shows the existing pattern.

## What to test here vs in unit tests

| Concern | Test here | Test in unit suite |
|---|---|---|
| RLS policies enforce org isolation | Yes | No |
| Postgres-specific query behaviour | Yes | No |
| Migration correctness | Yes (`test_rls_audit.py`) | No |
| Domain logic, calculation correctness | No | Yes |
| Route auth guards | No | Yes (via `TestClient`) |
