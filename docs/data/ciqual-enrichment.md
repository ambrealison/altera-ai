# CIQUAL nutrition enrichment

## What is CIQUAL?

CIQUAL is the French national food composition database maintained by ANSES
(Agence nationale de sécurité sanitaire de l'alimentation). It covers ~3,500
food items with macro- and micro-nutrient values.

**Attribution (required in all outputs):**
> Anses. 2025. Ciqual French food composition table. https://ciqual.anses.fr/

## Why Altera uses it

Retailer upload files often omit `protein_pct`. Without a protein value the
product is excluded from the Protein Tracker calculation. CIQUAL provides a
reference fallback — an analytical category average rather than SKU-level label
data — so fewer products need manual review.

CIQUAL values are **never applied silently**. They are stored as enrichment
records and only used in a calculation when `use_enriched_nutrition=true` is
set by an Altera internal user.

## Enrichment priority order

| Priority | Source | Notes |
|----------|--------|-------|
| 1 (highest) | `retailer_provided` | Label data from the retailer's upload file. Never overwritten. |
| 2 | `manual_altera` | Value entered by an Altera methodology reviewer. |
| 3 | `ciqual` | Reference table lookup (this document). |
| 4 | `category_average` | Static YAML table by PT methodology group. |

## Matching logic

`CiqualProvider` uses an in-memory index loaded at application startup.
Two match strategies are tried in order:

1. **Exact name match** (`food_name_en`, case-insensitive) — confidence 0.80
2. **Food-group average** — mean `protein_g_per_100g` across all non-`is_below_detection`
   entries in the group — confidence 0.55

A product with `is_below_detection=True` is excluded from group averages.
If neither strategy produces a match, a `FAILED` enrichment record is stored
and the product continues to be flagged for manual review.

## How to run the importer

The importer reads the CIQUAL 2025 Excel file and upserts rows into the
`ciqual_reference` table. It is idempotent — re-running it on the same version
is safe.

```bash
# From apps/api/
uv run python scripts/import_ciqual.py \
    --path /path/to/Table\ Ciqual\ 2025_ENG_2025_11_03.xlsx \
    [--dry-run]   # Print rows without writing to DB
```

Environment variables required (same as the API server):

```
DATABASE_URL=postgresql://...
```

### What the importer does

1. Opens the `"food composition"` sheet via `openpyxl`.
2. Reads the header row to detect the protein column (expected at index 14:
   `Protein (g 100g)`).
3. For each data row, normalises the protein value:
   - Comma-decimal `"4,41"` → `Decimal("4.41")`
   - Missing `"-"` or empty → `None`; `is_below_detection=False`
   - Below-detection `"< 0,2"` → `None`; `is_below_detection=True`
4. Skips rows where `alim_code` (column index 6) is `None`.
5. Upserts on `(source_version, source_food_code)` in batches of 500.

### Do NOT commit the source file

The CIQUAL Excel file must not be committed to the repository. It is listed
in `.gitignore`:

```
Table Ciqual *.xlsx
Table_Ciqual_*.xlsx
```

Store it in a shared drive or team password manager and distribute out-of-band.

## Disclosure in reports

When CIQUAL values are used in a calculation, the coverage section of the
report includes a disclosure caveat:

> "X product(s) have protein_pct enriched from the ANSES CIQUAL 2025
> reference table (Anses. 2025. Ciqual French food composition table).
> These are reference averages, not SKU-level label data."

This disclosure is emitted by `_enrichment_caveats()` in
`apps/api/altera_api/exports/coverage.py`.

## Test fixture

A minimal CSV fixture lives at
`apps/api/tests/fixtures/ciqual_sample.csv` for unit tests. It contains
6 food items covering:

- Comma-decimal protein values
- Below-detection entries
- Multiple food groups (used to verify group-average exclusions)

The fixture is **not** loaded into the production database — it exists solely
for the test suite's openpyxl monkeypatch stubs.
