# ADR 0004 — Semantic versioning for methodology, taxonomy, and rules

- Status: Accepted
- Date: 2026-05-14

## Context

A retailer publishing a number from Altera AI must be able to reproduce
that number months later. The number is the product of three
independently changing things:

- The methodology definition itself.
- The taxonomy used to map retailer categories to methodology buckets.
- The deterministic rules engine.

Without versioning, a future repository state could quietly change any
of these and silently alter past numbers.

## Decision

All three carry an independent semantic version. The version is stored
on every calculation row (via the `runs` row). The version is bumped:

- **Major** for any change that breaks numerical comparability with
  prior results.
- **Minor** for additive change.
- **Patch** for wording or comment changes with no behavioural effect.

Each lives next to its code:

- `apps/api/altera_api/methodologies/protein_tracker/__init__.py:VERSION`
- `apps/api/altera_api/methodologies/wwf/__init__.py:VERSION`
- `apps/api/altera_api/rules/__init__.py:VERSION`
- `packages/taxonomy/version.txt`

Each has its own CHANGELOG.

## Alternatives considered

- **Git SHA per calculation.** Simpler to write, but ties audit history
  to the repository's git history, which is operationally fragile and
  harder for non-engineer auditors to understand.
- **Single combined version.** Hides the source of a numerical change
  and makes minor methodology improvements harder to ship.

## Consequences

Positive:

- A calculation row carries enough information to be reproduced from
  the packaged code at the same versions.
- Auditors can see immediately which subsystem changed between two
  runs of the same project.

Negative:

- Engineers must remember to bump the right version on the right
  change. We mitigate with the reproducibility CI test, which forces
  a version bump when fixture outputs change.
