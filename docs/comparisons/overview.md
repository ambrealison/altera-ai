# Run comparison (Phase 27A)

Run comparisons let an analyst (or client, with approved exports) compare two
calculation runs from the same project side-by-side — typically year-over-year
or period-to-period.

## Guarantees

- **Run data is never mutated.** Each comparison is computed on demand from the
  stored, immutable `summary_payload` of the two selected runs.
- **Deterministic.** Given the same two runs, the response is always identical.
- **No forecasting.** Only measured periods are compared; no extrapolation.
- **PT and WWF are never merged.** Each methodology produces its own comparison
  result independently.
- **Version mismatches produce warnings, not errors.** If methodology, taxonomy,
  or rules versions differ between the two runs, a human-readable warning is
  included in `warnings[]` so the analyst knows the delta may not be
  apples-to-apples.

## Access control

| User type               | Access                                                    |
|-------------------------|-----------------------------------------------------------|
| Altera internal         | Unrestricted, any two runs from the same project          |
| Client user             | Both runs must have an `approved` or `delivered` export   |
| Cross-org client        | Blocked (404) via the `get_project` dependency            |

## API

```
GET /api/v1/projects/{project_id}/comparisons
    ?baseline_run_id=<uuid>
    &comparison_run_id=<uuid>
```

Returns `RunComparisonResponse`. Both run IDs must belong to the same project
and share the same methodology. Passing the same ID twice returns HTTP 422.

### Error codes

| Code | Meaning                                                  |
|------|----------------------------------------------------------|
| 422  | Same run ID used for both baseline and comparison        |
| 422  | Runs have different methodologies                        |
| 404  | Either run not found in this project                     |
| 403  | Client user without an approved/delivered export for one or both runs |

## Direction logic

### Protein Tracker

Direction is based on the change in plant-share percentage points:

- `delta_plant_share_pct > 0.1 pp` → **improving**
- `delta_plant_share_pct < -0.1 pp` → **declining**
- otherwise → **stable**

If neither run has a plant share (both totals are zero), direction is `stable`.

### WWF

Direction is based on whether the plant weight *fraction* of total weight
increased:

- `(comp_plant / comp_total) − (base_plant / base_total) > 0.001` → **improving**
- same expression `< −0.001` → **declining**
- otherwise → **stable**

Fraction-based logic avoids a "false improving" signal when total volume grows
even though the diet composition is unchanged.

## Response shape

```json
{
  "baseline_run_id": "...",
  "comparison_run_id": "...",
  "project_id": "...",
  "methodology": "protein_tracker",
  "pt_comparison": {
    "baseline_reporting_period": "2023",
    "comparison_reporting_period": "2024",
    "baseline_methodology_version": "1.0",
    "comparison_methodology_version": "1.0",
    "baseline_plant_protein_kg": "50.00",
    "comparison_plant_protein_kg": "60.00",
    "delta_plant_protein_kg": "10.00",
    "baseline_plant_share_pct": "50.00",
    "comparison_plant_share_pct": "55.00",
    "delta_plant_share_pct": "5.00",
    "direction": "improving",
    "per_group": [
      {
        "pt_group": "plant_based_core",
        "baseline_protein_kg": "20.00",
        "comparison_protein_kg": "25.00",
        "delta_protein_kg": "5.00"
      }
    ]
  },
  "wwf_comparison": null,
  "warnings": [],
  "created_at": "2026-05-18T10:00:00Z"
}
```

All `Decimal` fields are serialized as strings. `delta_*` fields are signed
(positive = comparison > baseline).

## No persistence

Comparisons are computed on demand — there is no comparisons table or
migration. The `created_at` timestamp in the response reflects when the
endpoint was called, not a stored record.

## Frontend

The **Runs** page (`/projects/[id]/runs`) shows a **Compare runs** card when
two or more runs exist. The analyst selects a baseline (earlier) and a
comparison (later) run from dropdowns, then clicks **Compare**.

Results are rendered inline:

- A direction badge (green = improving, red = declining, grey = stable).
- A methodology version mismatch pill if applicable.
- A headline table: plant kg, animal kg, plant share % — with baseline,
  comparison, and signed delta columns.
- A collapsible per-group breakdown (PT only).
- Warnings in an amber notice box.
