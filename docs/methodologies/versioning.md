# Methodology versioning

Methodology versions are part of the audit contract: any number Altera
AI produces must be reproducible from the same inputs and the same
versions. This document describes how versions are assigned, where they
live, and how they appear in stored data.

## What carries a version

Three independently versioned things participate in every calculation:

1. **Methodology version** — one per methodology module. Identifies the
   rules-as-written for Protein Tracker or WWF, and pins the source
   edition (e.g. PT Foodservice Aug 2024, WWF 2024 retailer
   methodology).
2. **Taxonomy version** — the version of the product category taxonomy
   used to map retailer/foodservice categories to methodology
   groups/food groups.
3. **Rules version** — the version of the deterministic rules engine
   (keyword lists, brand allowlists, regex patterns).

The AI classifier is not versioned through this mechanism; it carries
the prompt template version and the model identifier in the per-row
classification record (see
[../classification/ai-classifier.md](../classification/ai-classifier.md)).

## Source-edition tracking

Each methodology module records the **source edition** it implements,
because both methodologies are external and may evolve independently of
Altera AI:

| Module             | Source                                                                 | Edition pinned at v1.0.0 |
|--------------------|------------------------------------------------------------------------|--------------------------|
| `protein_tracker`  | *The Protein Tracker — Foodservice*, Green Protein Alliance & ProVeg   | August 2024              |
| `wwf`              | *Achieving a Planet-Based Diet*, WWF Food Practice (Meyer et al.)      | 2024                     |

A new edition of either source PDF triggers a **major** version bump
when the edition's rules change comparability with the prior edition,
or a **minor** bump when the change is additive (e.g. WWF adds a whole-
food-basket variant that does not change the default ratios).

If a retail edition of Protein Tracker diverges from the foodservice
edition in numerical effect, it is implemented as a separate version
line (e.g. `protein_tracker_retail`) rather than a parameter of the
foodservice module, so both can coexist for any organisation that
needs them.

## Format

Semantic versioning: `MAJOR.MINOR.PATCH`.

- `MAJOR` — change that breaks numerical comparability with prior
  results.
- `MINOR` — additive change that does not break prior comparability.
- `PATCH` — bug fix, documentation, or wording change with no
  numerical effect.

## Where versions live

- Each methodology package declares its version in
  `apps/api/altera_api/methodologies/<name>/__init__.py` as a top-level
  constant `VERSION`, alongside a `SOURCE_EDITION` string.
- The rules engine declares its version in
  `apps/api/altera_api/rules/__init__.py`.
- The taxonomy declares its version in
  `packages/taxonomy/version.txt` (a one-line file).

CHANGELOG files live alongside each module:

```
apps/api/altera_api/methodologies/protein_tracker/CHANGELOG.md
apps/api/altera_api/methodologies/wwf/CHANGELOG.md
apps/api/altera_api/rules/CHANGELOG.md
packages/taxonomy/CHANGELOG.md
```

## How versions appear on stored data

Every calculation row written to the database includes:

```
methodology               TEXT    -- 'protein_tracker' | 'wwf'
methodology_version       TEXT    -- e.g. '1.0.0'
methodology_source_edition TEXT   -- e.g. 'GPA & ProVeg Foodservice 2024-08'
taxonomy_version          TEXT    -- e.g. '1.0.0'
rules_version             TEXT    -- e.g. '1.0.0'
ai_prompt_version         TEXT    -- nullable; only set if AI was used
ai_model                  TEXT    -- nullable; only set if AI was used
```

These columns are immutable for the lifetime of the row.

## Re-running an older calculation

A user may "pin" a project to a specific methodology version when
creating a project. By default, a project floats on the current major
version. To reproduce a prior result, the user creates a new run on the
same upload with the desired pinned versions; the engine refuses to run
under a methodology version it does not still have packaged.

## Deprecation policy

A major version may be deprecated but is never deleted while there
exists any stored calculation that references it. The packaged code
for an older major version is retained until those calculations are
explicitly purged by the owning organisation.
