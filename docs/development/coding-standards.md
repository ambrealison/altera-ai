# Coding standards

The standards in this document are non-negotiable for code in this
repository. They exist to keep the methodology implementation auditable.

## Languages

- **Python.** 3.12+, type-annotated end-to-end. mypy in `strict` mode
  for `apps/api/altera_api/methodologies/`, `rules/`, `validation/`,
  `ai/`, and `calculation/`; `non-strict` is acceptable for HTTP route
  bodies.
- **TypeScript.** 5.x+, `strict: true` in `tsconfig.json`. No `any`
  in `src/lib/` or anywhere that touches API contracts; opt-out only
  with a justifying comment.
- **SQL.** PostgreSQL 15+. Idempotent migrations.

## Tooling

- **Python.** `uv` for env + deps, `ruff` for lint + format, `mypy`
  for types, `pytest` for tests.
- **TypeScript.** `pnpm` for deps, `eslint` + `prettier` for lint +
  format, `tsc --noEmit` for types, `vitest` for tests.

## Module structure (Python)

- Each methodology is a package under `methodologies/`. It exports a
  `VERSION` constant, a `Category` enum, a `classify(...)` pure
  function (for the deterministic-derived per-row classification when
  driven by category), and a `calculate(...)` pure function returning
  a `Result` dataclass.
- The two methodology packages do **not** import from each other.
- Cross-cutting code (validation, AI, review) does not import from a
  methodology package; it dispatches by string keys.

## Module structure (TypeScript)

- `src/app/` — Next.js App Router routes.
- `src/components/` — presentational components. No fetching.
- `src/lib/api/` — generated and hand-written API client.
- `src/lib/auth/` — Supabase client and helpers.

## Naming

- Database tables and columns: `snake_case`, plural for tables.
- Python: `snake_case` for functions, `PascalCase` for classes.
- TypeScript: `camelCase` for variables, `PascalCase` for components
  and types.

## Numerical handling

- **Python:** `Decimal` for all protein arithmetic. `float` is
  forbidden in `methodologies/`, `calculation/`, and
  `validation/unit_conversion.py`. A lint check enforces this.
- **PostgreSQL:** `numeric` for protein values; never `real` or
  `double precision`.
- **TypeScript:** numbers in the report UI are formatted from strings
  emitted by the API; the frontend does not re-aggregate.

## Comments

Default to writing no comments. Add one only when the **why** is not
obvious from the code: a methodology nuance, a hidden constraint, a
known surprising behaviour. Do not write what the code does.

Docstrings on methodology functions are an exception: those functions
implement an external standard and benefit from a short summary
linking to the doc page.

## Imports

- No wildcard imports.
- Methodology packages import only from their own package, the
  shared schemas, and stdlib.
- The AI module imports only from its own package, the schemas, and
  stdlib + a single HTTP client.

## Errors

- Domain errors are typed exceptions, not strings.
- HTTP handlers translate domain exceptions to RFC 7807 problem
  documents. No raw stack traces in responses.

## Logging

- Use the project's structured logger. Each log entry carries
  `organisation_id`, `project_id`, and `request_id` when available.
- Never log the contents of a product (`product_name`,
  `ingredients_text`) at INFO level; use DEBUG for that.
- Never log any prompt body.

## Commits and PRs

- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`,
  `refactor:`.
- A PR that changes methodology, taxonomy, or rules behaviour
  includes a version bump and an updated CHANGELOG entry in the same
  PR.
- Reviews focus on methodology fidelity first, code style second.
