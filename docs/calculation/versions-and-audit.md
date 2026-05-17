# Versions and audit on calculations

Every calculation row is reproducible from the inputs and the recorded
versions. This document specifies what is stored, where, and why.

## Stored on every `calculation_rows` row

Through `runs`, every row inherits:

- `methodology` — `protein_tracker` or `wwf`.
- `methodology_version` — semver of the methodology module.
- `methodology_source_edition` — e.g. `'GPA & ProVeg Foodservice 2024-08'`
  or `'WWF Food Practice 2024'`.
- `taxonomy_version` — semver of the taxonomy at run time.
- `rules_version` — semver of the deterministic rules at run time.
- `run_id`, `triggered_by`, `started_at`, `finished_at`.

Through the linked `classifications` row, each calculation row
inherits:

- `source` — `deterministic`, `ai`, or `manual_review`.
- `rule_id` (if deterministic), or
- `ai_prompt_version` and `ai_model` (if AI), or
- `reviewer_user_id` (if manual review).

These fields are what a downstream consumer (auditor, GPA / ProVeg
validator, NGO reviewer) needs to validate any number in the report.

## Stored on the run, not the row

To avoid duplication, the following live on the `runs` row only:

- The pinned versions for the run.
- The set of upload ids covered.
- The reporting period (e.g. FY 2024).
- The aggregate figures (if pre-computed; default is to compute at
  read-time from `calculation_rows`).

## Audit log entries written per run

| Event                              | Trigger                                  |
|------------------------------------|------------------------------------------|
| `run.created`                       | A run is queued.                          |
| `run.started`                       | The worker picks up the run.              |
| `run.failed`                        | The run errors out.                       |
| `run.succeeded`                     | The run completes successfully.           |
| `run.exported`                      | An export is generated for the run.       |
| `run.purged`                        | The run is deleted by the organisation.   |
| `pt.submitted_for_validation`       | A PT run is sent for GPA & ProVeg review. |
| `pt.validated`                       | A PT run is validated by GPA & ProVeg.    |

The `audit_logs.metadata` JSONB field carries run-specific details:
the methodology, the source edition, the versions, and counts
(in-scope, out-of-scope, unknown).

## Reproducibility test

A continuous-integration test runs the canonical fixture files
against the current methodology, taxonomy, and rules versions, and
asserts byte-for-byte identical output against `*.expected.json`
fixtures. A drift in this test indicates either an intended
methodology change (which must come with a version bump and an
updated fixture) or a regression.

## Reproducibility for older versions

The packaged code for an older major methodology version is retained
while any stored calculation references it. A re-run of an older run
re-loads that version and produces the same number; if the older
version's code has been purged, the re-run is refused with a clear
error pointing the user at the export of the historical figures.
