# ADR 0001 — Use a single monorepo for backend, frontend, and docs

- Status: Accepted
- Date: 2026-05-14

## Context

Altera AI consists of a Python/FastAPI backend, a Next.js/TypeScript
frontend, shared schema/contracts (Pydantic ↔ TypeScript), versioned
methodology and taxonomy data, and a documentation tree that is part
of the product (auditors and stakeholders read it).

We considered:

1. A monorepo containing all of the above.
2. Separate repositories for backend, frontend, and shared contracts.

## Decision

Use a single monorepo. Layout:

```
altera-ai/
  apps/api/                 # FastAPI
  apps/web/                 # Next.js
  packages/contracts/       # JSON schemas + generated types
  packages/taxonomy/        # versioned taxonomy data
  supabase/                 # migrations, RLS, seeds
  docs/                     # docs tree
```

## Consequences

Positive:

- The shared contracts (Pydantic models on the backend, generated TS
  types on the frontend) live in one place and stay in sync via a
  single PR.
- A methodology, taxonomy, or rules change can touch the implementation,
  the docs, and the fixtures atomically.
- CI sees the full picture and can block merges where one side drifts.

Negative:

- Backend and frontend deploys must still be independent in practice;
  the monorepo does not couple them at release time.
- The repository grows large; new contributors face a steeper initial
  read.

We accept these costs in exchange for the auditability and atomicity
gains.
