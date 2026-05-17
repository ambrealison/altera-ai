# Altera AI — Protein Ratio Software (Open Source)

Altera AI is an open-source client-facing SaaS platform that helps
retailers calculate the ratio of plant-vs-animal protein across their
assortment using two methodologies, side by side:

1. **Protein Tracker** — granular plant/animal categories with Level A
   (known split) and Level B (total protein only) calculations.
2. **WWF Planet-Based Diets Retailer Methodology** — plant/animal/hybrid
   classification with a protein-source ratio.

The two methodologies are kept strictly separate. Altera AI does not
produce a blended methodology.

## Status

Early stage. The repository currently contains expert-level
documentation under [`docs/`](docs/). Application code is built in the
order defined in the project brief: documentation, then sample
datasets, then database schema, then backend models, then validation,
rules engine, AI classifier, manual review, methodology calculations,
exports, the Next.js frontend, Supabase integration, tests, and
finally deployment documentation.

## What is here

```
altera-ai/
  docs/                  # canonical documentation tree
  apps/                  # (to be created) FastAPI backend + Next.js frontend
  packages/              # (to be created) shared contracts + taxonomy
  supabase/              # (to be created) migrations, RLS, seeds
```

## Read the docs

Start with the documentation index: [`docs/README.md`](docs/README.md).

Key entry points:

- [Vision](docs/project/vision.md) and
  [scope](docs/project/scope.md) of the MVP.
- [Design principles](docs/project/principles.md).
- The two methodology specs:
  [Protein Tracker](docs/methodologies/protein-tracker.md) and
  [WWF](docs/methodologies/wwf.md).
- The [AI inputs policy](docs/classification/ai-inputs-policy.md) —
  the single most important rule in the system.

## Stack

- Frontend: Next.js, React, TypeScript, Tailwind CSS.
- Backend: Python, FastAPI, Pydantic, Pandas.
- Database / Auth: Supabase (PostgreSQL, Supabase Auth, Row-Level
  Security).
- Storage: Supabase Storage.
- AI: OpenAI by default, behind a provider abstraction with strict
  JSON validation.
- Reports: CSV, JSON, Markdown at MVP; Excel and PDF later.

## Contributing

The project follows the documentation-first build order described in
the brief. Before implementing any feature, locate the relevant doc
page and confirm the spec, or propose an edit to the doc page in the
same PR.

See [`docs/development/local-setup.md`](docs/development/local-setup.md),
[`docs/development/coding-standards.md`](docs/development/coding-standards.md),
and [`docs/development/testing.md`](docs/development/testing.md) once
the application code is in place.

## Licence

To be added (intended open source; licence file pending).
