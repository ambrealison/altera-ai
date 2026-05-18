# altera-api

FastAPI backend for Altera AI.

## Status

Phase 17 complete (13A–17). Persistence is configurable:

- **In-memory** (default, `ALTERA_USE_IN_MEMORY_STORE=true`) — for dev and tests.
- **Postgres** (`ALTERA_USE_IN_MEMORY_STORE=false`) — Supabase-backed via service-role key.

Supabase Storage is used for raw uploads and generated exports when
`SUPABASE_SERVICE_ROLE_KEY` is set (13D). Falls back to in-memory bytes otherwise.

What each phase added:

- **13C**: `altera_api/auth/` — `verify_supabase_jwt`, `authed_user` FastAPI
  dependency, `AuthContext`, `/api/v1/me`, cross-tenant 404, dev fallback.
- **13B**: `altera_api/persistence/` — `StoreProtocol`, `MemoryRepository`,
  `PostgresRepository`, `get_repository()` factory.
- **13D**: `altera_api/storage/` — `StorageService` (signed-URL uploads, export
  upload/download), `prepare` + `ingest` endpoints, export route persists to
  the `exports` bucket and redirects to a signed URL.
- **14**: Role namespace split — `ClientRole`/`AlteraRole`, `organisation_type`,
  export approval workflow, Altera cross-org visibility.
- **15**: Production upload pipeline — 11-value `UploadStatus` enum, pre-flight
  validation, SHA-256 duplicate detection, validation report persistence,
  lifecycle timestamps, `GET /uploads/{id}` detail endpoint.
- **16**: Background job system — `Job` domain model, `SyncDevRunner` (in-process),
  `WorkerBackend` protocol for future Celery/RQ replacement. Job endpoints:
  validate/ingest/classify per upload, calculate per project, export per run.
  Job audit trail. Migration `0019_phase16_jobs.sql`.
- **16B**: Storage-first job resolution — job handlers fetch uploaded files from
  Supabase Storage when `upload.storage_path` is real; `file_bytes_b64` becomes
  an optional dev/test fallback only. `generate_export` persists exports to the
  `exports` bucket and creates an `ExportRecord` in the store.
- **17**: AI classifier integration — `OpenAIProvider` + `ClassifierProvider` ABC;
  `get_ai_provider()` config factory (`ALTERA_AI_CLASSIFIER_ENABLED`,
  `ALTERA_AI_PROVIDER`, `OPENAI_API_KEY`, `ALTERA_OPENAI_MODEL`); `classify_upload`
  pipeline calls AI for pass-through products after deterministic rules;
  `ClassifySummary` extended with `ai_attempted/accepted/review/failed`;
  `ManualReviewQueueReason.AI_PROVIDER_ERROR`; privacy guard (`assert_payload_allowed`)
  enforced before every outbound call. Migration `0020_phase17_ai_provider_error.sql`.

## Auth setup

### Local development with Supabase

```bash
# Start the local Supabase stack (Postgres + Auth + Storage).
supabase start

# Grab values from `supabase status` and put them in apps/api/.env.local.
cp .env.example .env.local
# Then fill in SUPABASE_URL, SUPABASE_JWT_SECRET, SUPABASE_SERVICE_ROLE_KEY.
```

The frontend's `apps/web/.env.local` should carry the matching
`NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
(or the legacy `NEXT_PUBLIC_SUPABASE_ANON_KEY`).

### Local development without Supabase (dev fallback)

```bash
cp .env.example .env.local
# Edit:
#   ALTERA_DEV_AUTH_ENABLED=true
# Leave SUPABASE_* blank.
```

Start the API and the frontend; the frontend's `/login` page renders
the "Supabase not configured" banner and every API call uses the
synthetic dev user.

### Production

`ALTERA_DEV_AUTH_ENABLED` must be `false` (or unset) in production.
All three Supabase env vars (`SUPABASE_URL`, `SUPABASE_JWT_SECRET`,
`SUPABASE_SERVICE_ROLE_KEY`) must be present. Set
`ALTERA_USE_IN_MEMORY_STORE=false` to enable Postgres persistence.

Set `CORS_ALLOWED_ORIGINS` to the exact frontend URL(s). Never use `*`.

```bash
CORS_ALLOWED_ORIGINS=https://app.altera-ai.com
```

`SUPABASE_SERVICE_ROLE_KEY` is a backend-only secret. It must **never**
appear in frontend environment variables or be committed to source
control. Frontend Supabase access uses the publishable/anon key only.

## Observability configuration (Phase 28B)

### Structured logging

Logs are emitted as JSON to stdout. Set `LOG_LEVEL` to control verbosity:

```bash
LOG_LEVEL=INFO   # DEBUG | INFO | WARNING | ERROR (default: INFO)
```

Every request emits a `request.complete` log line with `method`, `path`, `status`, `duration_ms`, and `request_id`. Sensitive headers (`Authorization`, `Cookie`) are never logged.

### Sentry (optional)

Install `sentry-sdk` separately, then set:

```bash
pip install sentry-sdk
SENTRY_DSN=https://key@o0.ingest.sentry.io/123
SENTRY_ENVIRONMENT=staging       # staging | production
SENTRY_TRACES_SAMPLE_RATE=0.05  # 0.0–1.0, default 0.05
```

Leave `SENTRY_DSN` empty (or unset) to disable Sentry entirely — no `sentry-sdk` installation required.

## Rate limiting configuration (Phase 30B/30C)

Disabled by default. Enable for single-process deployments:

```bash
RATE_LIMIT_ENABLED=true
RATE_LIMIT_UPLOADS_PER_MINUTE=20    # POST .../uploads, .../uploads/prepare, .../ingest, .../jobs/validate
RATE_LIMIT_CLASSIFY_PER_MINUTE=10   # POST .../classify, .../jobs/classify
RATE_LIMIT_EXPORTS_PER_MINUTE=30    # GET .../export, POST .../jobs/export
RATE_LIMIT_COMPUTE_PER_MINUTE=5     # POST .../jobs/calculate, .../scenarios/{id}/run, GET .../comparisons
RATE_LIMIT_DEFAULT_PER_MINUTE=200   # all other routes
RATE_LIMIT_MAX_BUCKETS=100000       # evict oldest beyond this cap

# Only set if a known reverse proxy sits in front (Fly.io, Cloudflare, etc.)
TRUSTED_PROXIES=                    # comma-separated CIDRs; empty = never trust X-Forwarded-For
```

Requests are keyed by **client IP only**. Unverified JWT claims are never used
(they are attacker-controlled). `X-Forwarded-For` is only trusted when the
direct peer is in `TRUSTED_PROXIES`. 429 responses include `Retry-After` and
a structured `error_code: rate_limited` body.

The in-memory limiter is single-process only. For multi-process production
deployments, use a Redis/Upstash-backed implementation or API gateway.

## CORS configuration (Phase 30C)

`CORS_ALLOWED_ORIGINS` **must** be set in production. The server refuses to
start if it is unset and `ALTERA_DEV_AUTH_ENABLED` is not `true`.

```bash
CORS_ALLOWED_ORIGINS=https://app.altera-ai.com
```

## Secret scanning

`.gitleaks.toml` at the repo root configures Gitleaks detection. Run before
every deployment: `gitleaks detect --source . --config .gitleaks.toml`. If a
secret is found in history, revoke it immediately and rewrite history with
`git filter-repo` before pushing to any remote.

## AI classifier configuration

The classifier is disabled by default. To enable it:

```bash
ALTERA_AI_CLASSIFIER_ENABLED=true
ALTERA_AI_PROVIDER=openai        # "openai" | "mock" | "disabled"
OPENAI_API_KEY=sk-...
ALTERA_OPENAI_MODEL=gpt-4o-mini  # optional, default gpt-4o-mini
```

- `ALTERA_AI_PROVIDER=mock` — returns deterministic fake responses; for
  development without spending tokens.
- `ALTERA_AI_PROVIDER=disabled` (or `ALTERA_AI_CLASSIFIER_ENABLED=false`) —
  pass-through products go straight to manual review as before.

## Layout

```
altera_api/
├── main.py                   # FastAPI app, /health and /version
├── version.py                # app version + current build phase
├── domain/                   # Phase 4 — strict Pydantic domain models
│   ├── audit.py              # AuditEvent, AuditEventType
│   ├── common.py             # Methodology, Role, base config, brand types
│   ├── organisation.py       # Organisation, UserProfile
│   ├── product.py            # RawProduct, NormalizedProduct, PT/WWF blocks
│   ├── project.py            # Project, PTValidationStatus
│   ├── protein_tracker.py    # PT enums + classification + calc models
│   ├── review.py             # manual review queue + decision
│   ├── report_exports.py     # ReportExport, ReportApprovalStatus, ReviewOwnerType
│   ├── upload.py             # Upload, UploadStatus
│   ├── validation.py         # ValidationError/Warning/Report
│   ├── versioning.py         # Semver + methodology / taxonomy / rules versions
│   └── wwf.py                # WWF enums + classification + calc models
├── ingestion/                # Phase 5 — CSV → NormalizedProduct[]
├── rules/                    # Phase 6 — deterministic classifier
├── ai/                       # Phase 7 + 17 — LLM classifier
│   ├── provider.py           # ClassifierProvider ABC, ProviderResponse, ProviderError
│   ├── openai_provider.py    # OpenAIProvider (lazy openai import)
│   ├── config.py             # get_ai_provider() factory + AISettings
│   ├── classifier.py         # classify_pt(), classify_wwf(), DEFAULT_CONFIDENCE_THRESHOLD
│   ├── prompt_builder.py     # build_classifier_prompt()
│   ├── prompt_input.py       # ClassifierPromptInput (allow-list dataclass)
│   ├── policy.py             # assert_payload_allowed(), ALLOWED_PROMPT_FIELDS
│   └── fakes.py              # StaticFakeProvider, RaisingFakeProvider, FailingFakeProvider
├── review/                   # Phase 8 — manual-review workflow
├── calculation/              # Phase 9 + 10 — methodology calculators
├── exports/                  # Phase 11 — CSV / JSON / Markdown renderers
├── jobs/                     # Phase 16 — background job runner
│   ├── runner.py             # WorkerBackend protocol + SyncDevRunner
│   ├── tasks.py              # execute_job() + per-type handlers
│   └── dependencies.py       # get_worker() FastAPI dependency
├── api/                      # Phase 12 + 13 — HTTP API layer
│   ├── state.py              # RunRecord, UploadRecord, ExportRecord, Job; InMemoryStore
│   ├── store_factory.py      # get_store() → get_repository()
│   ├── orchestrator.py       # ingest → classify → review → calc → export
│   ├── dependencies.py       # FastAPI DI: get_project, current_user_id
│   └── routes.py             # /api/v1/* all endpoints
├── auth/                     # Phase 13C — Supabase JWT verification + dev fallback
│   ├── config.py             # AuthSettings (pydantic_settings)
│   ├── errors.py             # AuthError + subclasses
│   ├── models.py             # AuthContext, AuthProvider, role helpers
│   ├── verifier.py           # verify_supabase_jwt (PyJWT, HS256)
│   └── dependency.py         # authed_user FastAPI dep + auto-provisioning
├── persistence/              # Phase 13B — store abstraction
│   ├── protocol.py           # StoreProtocol (typing.Protocol)
│   ├── memory.py             # MemoryRepository = InMemoryStore alias
│   ├── postgres.py           # PostgresRepository (supabase-py v2)
│   ├── mappers.py            # row ↔ domain conversions
│   └── factory.py            # get_repository() — reads ALTERA_USE_IN_MEMORY_STORE
├── storage/                  # Phase 13D + 16B — Supabase Storage
│   ├── service.py            # StorageService: signed upload URL, upload_export, signed download
│   ├── protocol.py           # StorageProtocol (duck-typed, for job handlers)
│   ├── fake.py               # FakeStorageService (in-memory test double)
│   └── factory.py            # get_storage_service() — None when Supabase not configured
└── observability/            # Phase 28B — structured logging + Sentry
    ├── logging.py            # _JsonFormatter, _ContextFilter, configure_logging(), get_logger()
    ├── middleware.py         # RequestLoggingMiddleware (request_id, duration_ms, path)
    └── sentry.py             # init_sentry() — optional sentry-sdk integration
```

## Setup

```bash
uv sync --extra dev
```

## Run dev server

```bash
uv run uvicorn altera_api.main:app --reload --port 8000
```

Then:

- http://localhost:8000/health
- http://localhost:8000/version
- http://localhost:8000/docs (OpenAPI)

## Test

```bash
uv run pytest                          # all unit tests
uv run pytest tests/domain             # domain-model suite
uv run pytest tests/ingestion          # ingestion suite
uv run pytest tests/rules              # rules-engine suite
uv run pytest tests/ai                 # AI classifier suite
uv run pytest tests/review             # manual-review workflow suite
uv run pytest tests/calculation        # calculation suite
uv run pytest tests/exports            # export renderers
uv run pytest tests/api                # HTTP integration tests
uv run pytest tests/supabase           # SQL migration shape + RLS-policy lint
uv run pytest tests/auth               # JWT verification + dev fallback + cross-tenant
uv run pytest tests/integration -m integration  # Postgres integration (needs SUPABASE_URL)
```

## Lint and typecheck

```bash
uv run ruff check .
uv run ruff check --fix .       # auto-fix import order, etc.
uv run mypy
```
