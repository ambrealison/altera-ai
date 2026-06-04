# NEVO V2 first-apply runbook (Quality-V2-X)

The exact, ordered procedure for the **first real V2 enrichment apply** after
migration `0037`. It is deliberately conservative: every step is read-only or
reversible until the very end, and even the first write is limited to a tiny
sample via `--limit-apply`.

**Invariants that MUST hold throughout:** V1 stays the default matcher,
embeddings stay off by default, no app route imports V2/apply modules, and only
the explicit `apply_nevo_v2_plan` CLI may write — never an app route.

Artifacts live under `/tmp/altera-quality` (or your `--output-dir`). Substitute
`<project>` / `<uuid>` throughout.

---

## 0. Pre-flight

- [ ] You have the four pipeline artifacts for the project:
      `nevo_v2_enrich_review_package_<project>.csv` (filled by a reviewer),
      `nevo_v2_review_validation_summary_<project>.json`,
      `nevo_v2_review_approved_candidates_<project>.csv`,
      `nevo_v2_apply_plan_<project>.json`.
- [ ] The validation summary recommendation is `ready_for_apply_planning`
      (or `review_incomplete` if you intend `--allow-incomplete-apply`).

## 1. Confirm the app is still on V1

```bash
python - <<'PY'
import os
for k in ("ALTERA_NEVO_MATCHER_VERSION","ALTERA_ENABLE_EMBEDDINGS"):
    os.environ.pop(k, None)
from altera_api.classification_v2.nevo_matcher import resolve_nevo_matcher_version
from altera_api.quality_config import embeddings_enabled
print("matcher default =", resolve_nevo_matcher_version())   # must be v1
print("embeddings      =", embeddings_enabled())             # must be False
PY
```
- [ ] `matcher default = v1`, `embeddings = False`.

## 2. Apply Supabase migration 0037

Additive only (`source_version text`, `source_metadata jsonb`); does **not**
touch the `match_method` CHECK; no backfill.

```bash
# via your normal Supabase migration flow, e.g.:
supabase db push          # or apply 0037_quality_v2v_nevo_enrichment_provenance.sql
```
- [ ] Migration `0037` reports applied with no errors.

## 3. Run the migration-readiness checker (read-only)

```bash
python -m altera_api.classification_v2.check_nevo_v2_apply_readiness \
  --project-id <uuid> \
  --plan-json /tmp/altera-quality/nevo_v2_apply_plan_<project>.json \
  --approved-candidates /tmp/altera-quality/nevo_v2_review_approved_candidates_<project>.csv \
  --output-dir /tmp/altera-quality
```
- [ ] Exit code `0` and `ready: true` in
      `nevo_v2_apply_readiness_<project>.json`.
- [ ] `provenance_columns_present`, `plan_project_matches`,
      `approved_count_matches_plan`, `db_apply_status_expected`,
      `no_overwrite_flags`, `v1_default_unchanged`, `routes_clean` all `pass`.
- [ ] Review the `conflicts` block (`existing_manual` / `existing_v1` /
      `existing_v2`) — those rows will be **skipped**, never overwritten.

If `ready: false`, stop and fix the failing check before continuing.

## 4. (Optional) Regenerate the pipeline artifacts

Only if the catalog or review decisions changed since they were produced:
re-run the dry-run enrich → review package → validator → plan generator, then
return to step 3.

## 5. Run the apply CLI in DRY-RUN (writes nothing)

```bash
python -m altera_api.classification_v2.apply_nevo_v2_plan \
  --plan-json /tmp/altera-quality/nevo_v2_apply_plan_<project>.json \
  --approved-candidates /tmp/altera-quality/nevo_v2_review_approved_candidates_<project>.csv \
  --project-id <uuid> \
  --output-dir /tmp/altera-quality
```
- [ ] `dry_run: true`, `written_count: 0`,
      `provenance_columns_present: true` (now that 0037 is applied).

## 6. Inspect the dry-run result

Open `nevo_v2_apply_result_<project>.json` / `.csv`.
- [ ] `would_write_count` is what you expect.
- [ ] `skipped_v1_count` / `skipped_manual_count` / `skipped_existing_count`
      (v2) match the readiness `conflicts`.
- [ ] `error_count == 0`. Investigate any `error` rows before proceeding.

## 7. Confirmed apply on a TINY sample (`--limit-apply`)

Start with one or a few rows:

```bash
python -m altera_api.classification_v2.apply_nevo_v2_plan \
  --plan-json /tmp/altera-quality/nevo_v2_apply_plan_<project>.json \
  --approved-candidates /tmp/altera-quality/nevo_v2_review_approved_candidates_<project>.csv \
  --project-id <uuid> --output-dir /tmp/altera-quality \
  --limit-apply 1 \
  --embedding-provider voyage --embedding-model voyage-4-lite --top-k 20 \
  --confirm-apply-v2
```
- [ ] `dry_run: false`, `limit_apply: 1`, `written_count: 1`, `error_count: 0`.
- [ ] Only `--confirm-apply-v2` plus the live columns enabled the write.

## 8. Re-read the DB rows and verify provenance

```bash
python - <<'PY'
from uuid import UUID
from altera_api.api.store_factory import get_store
store = get_store()
for r in store.get_enrichment_records_for_product(UUID("<written-product-uuid>")):
    if r.source_version == "v2_embeddings":
        print(r.source, r.match_method, r.source_version, r.enriched_value)
        print(r.source_metadata)
PY
```
- [ ] `source='nevo'`, `match_method='ai_assisted'`,
      `source_version='v2_embeddings'`, sensible `enriched_value`, and
      `source_metadata` carrying provider/model/top_k/paths/manual_decision/
      `applied_by_cli=true`.

## 9. Verify app / API / export behaviour is unchanged

- [ ] Open the project in the app — calculations/reports render normally.
- [ ] The V2 row is counted under the existing "AI-assisted" enrichment
      disclosure (it is `match_method='ai_assisted'`); no UI/API errors.
- [ ] No regression in `/projects`, the nutrition table, or the report export.

Only after a clean tiny sample should you consider a larger
`--limit-apply N` (or no limit) run, repeating steps 5–9.

## 10. Rollback

In increasing severity:

1. **Stop applying.** Do not run further confirmed applies. The app is
   unaffected (V1 default; V2 rows are additive and read like any
   AI-assisted enrichment).
2. **Delete the V2 rows** written so far:
   ```sql
   delete from public.nutrition_enrichment_records
   where source_version = 'v2_embeddings';
   ```
3. **Reverse the migration** (only if you want the columns gone — safe, additive
   nullable columns, no row rewrites):
   ```sql
   alter table public.nutrition_enrichment_records drop column if exists source_metadata;
   alter table public.nutrition_enrichment_records drop column if exists source_version;
   ```
   After dropping the columns, `apply_nevo_v2_plan --confirm-apply-v2` refuses
   again (columns missing → writes nothing), restoring the pre-0037 posture.

Operational note: keep `ALTERA_NEVO_MATCHER_VERSION` unset/`v1` at all times.
The apply CLI never depends on the app's matcher version — it reads the
already-approved candidates — so the production workflow stays on V1 regardless.
