# Local setup

This document gets a new contributor from a fresh clone to a running
local stack.

## Prerequisites

- Python 3.12+
- Node.js 20+
- `uv` (Python package and project manager)
- `pnpm` (Node package manager; alternative: npm)
- Docker (used by Supabase CLI for the local stack)
- Supabase CLI

## First-time setup

```bash
git clone <repo-url> altera-ai
cd altera-ai

# Backend
cd apps/api
uv sync
uv run pre-commit install

# Frontend
cd ../web
pnpm install

# Local Supabase
cd ../..
supabase start
supabase db reset       # applies migrations and seeds
```

## Environment variables

Each app reads `.env.local` (gitignored). A template lives at
`.env.example` next to each app. The required variables are:

### `apps/api/.env.local`

```
SUPABASE_URL=http://127.0.0.1:54321
SUPABASE_ANON_KEY=...                 # from `supabase status`
SUPABASE_SERVICE_ROLE_KEY=...         # from `supabase status`
OPENAI_API_KEY=                        # optional; AI classifier tests use a fake by default
ALTERA_AI_PROVIDER=fake                # 'fake' | 'openai'
```

### `apps/web/.env.local`

```
NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
ALTERA_API_BASE=http://127.0.0.1:8000
```

## Running

In three terminals (or via `make dev`):

```bash
# 1. Local Supabase (already running from `supabase start`)
# 2. FastAPI backend
cd apps/api
uv run uvicorn altera_api.main:app --reload --port 8000

# 3. Next.js frontend
cd apps/web
pnpm dev
```

Open <http://localhost:3000>.

## Common commands

```bash
# Run all backend tests
cd apps/api && uv run pytest

# Run only the unit-conversion tests
cd apps/api && uv run pytest tests/validation/test_unit_conversion.py

# Lint and format Python
cd apps/api && uv run ruff check && uv run ruff format

# Lint TypeScript
cd apps/web && pnpm lint

# Apply a new migration
supabase db diff -f <name>     # generate from db diff
supabase db reset              # re-apply locally

# Regenerate TypeScript types from the database
cd apps/web && pnpm db:types
```

## Resetting a stuck local environment

```bash
supabase stop
supabase start
supabase db reset
```

This is the safest reset path; it preserves no local state.

## Troubleshooting

- **`supabase start` fails on port collision.** Stop other Docker
  services on ports 54321–54324.
- **AI classifier tests fail with no key.** Set
  `ALTERA_AI_PROVIDER=fake` (the default in `.env.example`).
- **RLS tests fail.** Reset the database; RLS tests assume the seeded
  fixture users exist.
