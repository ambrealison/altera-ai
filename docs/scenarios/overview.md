# Scenario modelling overview

Scenarios are deterministic, read-only projections against a base
calculation run. They let Altera methodology leads ask "what would the
protein share look like if the retailer made this change?" — without
touching the underlying measurement or re-running the full pipeline.

Scenarios are **not forecasts** and do not modify any historical data.
Every projection is recomputable on demand from the same base run and
the same ordered set of operations.

## Phase 26A scope

Phase 26A implements the scenario foundation for **Protein Tracker only**.
WWF scenario modelling is deferred to a future phase.

## Core concepts

### Scenario

A named container linking a `base_run_id` to an ordered list of
operations. Status lifecycle:

```
draft → active
```

A scenario is created in `draft`. It transitions to `active`
automatically the first time it is successfully run
(`POST /scenarios/{id}/run`). Archived scenarios (`archived`) are
read-only.

### Operations

Each operation is a discrete transformation step applied to a copy of
the base run summary. Operations are applied in ascending `order` and
do not mutate the base data. Each operation carries:

| Field | Description |
|-------|-------------|
| `operation_type` | One of the four supported types (see below) |
| `parameters` | Operation-specific numeric parameters |
| `rationale` | Free-text explanation of the business reason |
| `order` | 0-based integer; operations execute lowest-first |

#### Supported operation types (Phase 26A)

| `operation_type` | What it does | Key parameters |
|------------------|--------------|----------------|
| `shift_protein_between_groups` | Moves a fraction of protein from one PT group to another | `from_group`, `to_group`, `fraction` (0–1) |
| `increase_plant_core_protein` | Scales up the `plant_based_core` protein total | `factor` (>0) |
| `reduce_animal_core_protein` | Scales down the `animal_core` protein total | `factor` (0–1) |
| `improve_composite_split` | Overrides the plant fraction assumed for composite products | `plant_fraction` (0–1) |

`increase_plant_core_protein` and `reduce_animal_core_protein` accept
`factor > 1` and `factor < 1` respectively. Using them the other way
round is not an error but produces unexpected results (the names are
directional hints, not constraints).

### Projection engine

`project_pt_scenario()` in `scenarios/pt_projection.py` is a **pure
function** with no state, no side effects, and no LLM calls. Given a
`ProteinTrackerCalculationSummary` and a list of operations it returns
a `ScenarioResult` containing a `PTProjectedSummary` that mirrors the
structure of the base summary.

Key invariants:

- Protein totals that would go negative after an operation are clamped
  to zero and a warning is recorded in `result.warnings`.
- `composite_plant_fraction` starts at 0.50 (the methodology default);
  `improve_composite_split` replaces it for subsequent operations in the
  same projection.
- Group shares are recomputed from the projected totals at the end, not
  accumulated across operations.
- The base run is **never mutated**; projection works on a deep copy of
  the per-group protein totals.

### Result

`scenario_results` stores the latest projection output keyed by
`scenario_id`. Re-running a scenario upserts the result. The result
payload is the serialised `ScenarioResult` Pydantic model.

## API endpoints (Phase 26A)

All scenario endpoints are under `/api/v1/projects/{project_id}/scenarios`
unless noted.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/` | Altera only | Create a scenario (returns `draft` scenario) |
| `GET` | `/` | Altera only | List all scenarios for a project |
| `POST` | `/{id}/operations` | Altera only | Add an operation to a scenario |
| `POST` | `/{id}/run` | Altera only | Execute the projection; upserts result; auto-promotes to `active` |
| `GET` | `/{id}/result` | Altera + clients (active only) | Fetch the latest projection result |

Clients can see the `result` endpoint only for scenarios with
`status = 'active'`. Creating, listing, or running scenarios is
restricted to Altera internal users.

Attempting to run a scenario against a non-PT run returns
`HTTP 422 Unprocessable Content` with a clear message:
`"WWF scenario modelling is not yet implemented"`.

## Access control

| Action | Minimum role |
|--------|--------------|
| Create / list scenarios | `altera_analyst` (any Altera internal) |
| Add operations | `altera_analyst` |
| Run projection | `altera_analyst` |
| View result | `altera_analyst` or `client_viewer` (active only) |

RLS mirrors these rules in Supabase:

- `altera_full_access_scenarios` — Altera internal users full CRUD.
- `clients_see_active_scenarios` — clients select where `status = 'active'`
  and `organisation_id` matches their org.

## Database tables (migration `0024_phase26a_scenarios.sql`)

| Table | Purpose |
|-------|---------|
| `scenarios` | Header + metadata (name, description, status, methodology, base_run_id) |
| `scenario_operations` | Ordered operation steps per scenario |
| `scenario_results` | Latest projection output, keyed by `scenario_id` |

## Frontend (Phase 26A)

A `ScenariosPlaceholderCard` is shown on the report page for Altera
users when the run methodology is `protein_tracker`. The card renders a
blue info box with the API endpoint instructions for creating and running
scenarios. A full interactive scenario-builder UI is deferred to a later
phase.

## What scenarios are NOT

- Scenarios are not saved what-if drafts stored per client.
- Scenarios are not recommendations; they do not imply the retailer
  should make any change. They are analytical tools for methodology leads.
- Scenarios do not feed back into the approved report or the delivered
  download. They are always labelled as projections.
- No commercial (product-level) data is included in any scenario result.
  Results contain only aggregate protein-group totals and shares.
