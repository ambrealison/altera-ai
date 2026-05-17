# Altera AI — documentation

Altera AI is an open-source client-facing SaaS platform that helps
retailers calculate plant-vs-animal protein ratios using two
methodologies side by side: **Protein Tracker** and the **WWF
Planet-Based Diets Retailer Methodology**. The two methodologies are
kept strictly separate in code and in reporting.

This directory is the project's expert-level documentation. It is part
of the product — an external auditor or stakeholder should be able to
trace any number in a generated report back to the relevant doc page,
and from there to the implementation.

## How to navigate

- New to the project? Start with [project/vision.md](project/vision.md),
  then [project/scope.md](project/scope.md), then
  [project/principles.md](project/principles.md).
- Implementing a methodology change? See
  [methodologies/](methodologies/) and
  [methodologies/versioning.md](methodologies/versioning.md).
- Implementing the data pipeline? See [data/](data/),
  [classification/](classification/), [calculation/](calculation/).
- Working on SaaS plumbing (orgs, auth, RLS, APIs)? See
  [saas/](saas/).
- Producing reports? See [outputs/](outputs/).
- Setting up locally or shipping? See [development/](development/).

## Table of contents

### project/
- [vision.md](project/vision.md) — what Altera AI is and why.
- [scope.md](project/scope.md) — in-scope and out-of-scope for MVP.
- [glossary.md](project/glossary.md) — terminology, alphabetised.
- [roles.md](project/roles.md) — user roles and permissions.
- [principles.md](project/principles.md) — design principles that shape every decision.

### methodologies/
- [overview.md](methodologies/overview.md) — why the two methodologies are separate.
- [protein-tracker.md](methodologies/protein-tracker.md) — full PT spec.
- [wwf.md](methodologies/wwf.md) — full WWF spec.
- [comparison.md](methodologies/comparison.md) — analyst-facing side-by-side reference.
- [versioning.md](methodologies/versioning.md) — methodology version policy.

### data/
- [schema.md](data/schema.md) — canonical database entities.
- [taxonomy.md](data/taxonomy.md) — product taxonomy and versioning.
- [sample-datasets.md](data/sample-datasets.md) — fixtures and mock data.
- [unit-conversion.md](data/unit-conversion.md) — protein unit normalisation.
- [input-formats.md](data/input-formats.md) — accepted upload formats.

### classification/
- [overview.md](classification/overview.md) — deterministic → AI → review pipeline.
- [deterministic-rules.md](classification/deterministic-rules.md) — rules engine.
- [ai-classifier.md](classification/ai-classifier.md) — AI provider abstraction.
- [ai-inputs-policy.md](classification/ai-inputs-policy.md) — what is and is not sent to the AI.
- [review.md](classification/review.md) — manual review workflow.
- [json-validation.md](classification/json-validation.md) — strict JSON output handling.

### calculation/
- [overview.md](calculation/overview.md) — how runs compose.
- [protein-tracker-calculation.md](calculation/protein-tracker-calculation.md) — PT arithmetic.
- [wwf-calculation.md](calculation/wwf-calculation.md) — WWF arithmetic.
- [weighting.md](calculation/weighting.md) — non-commercial weighting bases.
- [versions-and-audit.md](calculation/versions-and-audit.md) — what is stored on each calculation.

### saas/
- [multi-tenancy.md](saas/multi-tenancy.md) — organisation model.
- [auth.md](saas/auth.md) — authentication flow.
- [rls.md](saas/rls.md) — Row-Level Security patterns.
- [audit-logs.md](saas/audit-logs.md) — audit trail.
- [api.md](saas/api.md) — REST API design.
- [workflow.md](saas/workflow.md) — end-to-end user workflow.

### outputs/
- [formats.md](outputs/formats.md) — CSV, JSON, Markdown.
- [report-structure.md](outputs/report-structure.md) — what a report contains.
- [exports.md](outputs/exports.md) — export generation and delivery.

### development/
- [local-setup.md](development/local-setup.md) — local dev environment.
- [testing.md](development/testing.md) — test strategy.
- [coding-standards.md](development/coding-standards.md) — language and module rules.
- [deployment.md](development/deployment.md) — deployment targets and process.
- ADRs:
  - [0001-monorepo.md](development/adr/0001-monorepo.md)
  - [0002-strict-methodology-separation.md](development/adr/0002-strict-methodology-separation.md)
  - [0003-ai-input-restrictions.md](development/adr/0003-ai-input-restrictions.md)
  - [0004-semantic-versioning-for-methodologies.md](development/adr/0004-semantic-versioning-for-methodologies.md)
