# NEVO V2 enrichment persistence — Supabase migration design (Quality-V2-U)

> **Update (Quality-V2-V):** Option 2 is now **scaffolded in code**. Migration
> `0037_quality_v2v_nevo_enrichment_provenance.sql` adds the additive nullable
> `source_version` + `source_metadata` columns; `NutritionEnrichmentRecord` and
> the mapper carry them (omitted from V1 writes). **No apply path writes V2
> rows**, V2 is still not activated, and `match_method` is untouched. See the
> Quality-V2-V section in `v2-quality-roadmap.md`.

**Status: DESIGN ONLY. No migration is applied; no apply path writes the DB.**
This document specifies the minimal, reversible schema + model changes needed
to *later* persist NEVO **V2** enrichment records safely. Nothing here changes
production behaviour: V1 stays the default matcher, embeddings stay off, no
route imports V2, and the apply path remains `blocked_pending_schema_migration`
(see `plan_nevo_v2_apply.py`). No file has been added under
`supabase/migrations/` — the SQL below is a draft to review, not a live
migration.

---

## Part A — Current enrichment schema / model / consumers (as inspected)

**Table** `public.nutrition_enrichment_records` — created in
`supabase/migrations/0025_phase28a_persistence_parity.sql`:

```
id              uuid    pk default gen_random_uuid()
product_id      uuid    not null references products(id) on delete cascade
nutrient        text    not null            -- e.g. "protein_pct"
original_value  numeric                     -- retailer value, immutable
enriched_value  numeric                     -- enrichment result, may be null
unit            text    not null            -- e.g. "g_per_100g"
source          text    not null            -- NutritionEnrichmentSource enum
confidence      numeric check (0..1)
status          text    not null            -- NutritionEnrichmentStatus enum
rationale       text    not null default ''
created_at      timestamptz not null default now()
created_by      uuid
match_method    text    not null default 'deterministic'   -- added 0033
```

**`match_method` CHECK history:**
- `0033_phase33i_ai_match_method.sql` — added the column + CHECK
  `in ('deterministic','ai_assisted','manual')`.
- `0035_phase34t_match_method_none.sql` — extended to add `'none'` (no-match
  audit rows). Current allowed set: **`deterministic | ai_assisted | manual |
  none`**. This is the constraint that blocks V2-tagged writes.

The latest migration on disk is `0036_phase34x_ingestion_jobs.sql`, so the next
number would be **`0037`** when a migration is eventually authored.

**Domain model** `altera_api/domain/enrichment.py`:
- `NutritionEnrichmentRecord` (pydantic). `match_method: str = "deterministic"`
  is a **plain `str`** (not the enum), so Python won't reject an out-of-enum
  value — only the DB CHECK does (this was the Phase 34T 500-error root cause).
- `NutritionMatchMethod(StrEnum)` — `DETERMINISTIC / AI_ASSISTED / MANUAL /
  NONE`. Mirrors the DB CHECK.
- `NutritionEnrichmentSource(StrEnum)` — `retailer_provided, open_food_facts,
  ciqual, oqali, nevo, category_average, manual_altera, unknown`. **`source`
  already distinguishes the reference table** (e.g. `nevo`), separate from
  `match_method` (how the row was picked).

**Mapper** `altera_api/persistence/mappers.py`:
- `enrichment_record_from_row` (line ~1024) — `match_method=row.get("match_method")
  or "deterministic"` (back-compat for pre-0033 rows).
- `enrichment_record_to_row` (line ~1054) — writes `match_method`, `source`,
  etc. **Any new column must be added in both functions.**

**Store** `altera_api/persistence/`:
- `protocol.py` — `add_enrichment_record`, `get_enrichment_records_for_product`,
  `get_enrichment_records_bulk`, `list_enrichment_records_for_project`,
  `project_has_any_enrichment`.
- `postgres.py:1428` — `add_enrichment_record` → `insert(enrichment_record_to_row(record))`.
  Reads select `*`, so a new column flows through automatically on read; the
  **write** path is what a new column touches (via the mapper).

**Consumers of `match_method` / `source` (report/export & API):**
- `calculation/protein_tracker.py:170-175` — counts `match_method == "ai_assisted"`
  into `nevo_ai_assisted_count` / `ciqual_ai_assisted_count` (only when
  `source` is `nevo` / `ciqual`).
- `exports/coverage.py:260-296` — discloses "Of these, N reference(s) were
  selected with AI assistance…" from those counts.
- `api/routes.py` — response models expose `match_method` (lines ~2477, ~2565,
  ~2655); the apply/manual routes WRITE `match_method` =
  `deterministic|ai_assisted|manual|none` (lines ~3238, ~5681, ~5865, ~5895,
  ~6007, ~6136).
- `enrichment/selection.py:54-98` — carries `match_method` on the in-memory
  lookup result.
- Frontend `apps/web/lib/api.ts:492` — TS union
  `"deterministic" | "ai_assisted" | "manual" | "none" | null`. **This is the
  only frontend assumption**; it is display/typing only (no logic branches on
  it elsewhere in `apps/web`).

> Note: `exports/report.py:121` `deterministic=counts.get("deterministic")`
> counts the **PT/WWF classification source**, NOT enrichment `match_method` —
> unrelated to this migration.

---

## Part B — Migration approach comparison

### Option 1 — add `'v2_embeddings'` to the `match_method` CHECK

```sql
check (match_method in ('deterministic','ai_assisted','manual','none','v2_embeddings'))
```

### Option 2 — keep `match_method` as-is; add `source_version` + `source_metadata`

Leave the CHECK at `deterministic|ai_assisted|manual|none`. Add two additive,
nullable columns: `source_version text` (`'v1'`, `'v2_embeddings'`, future
`'v3_*'`) and `source_metadata jsonb` (model, provider, top_k,
review_package_id, matcher_confidence, nutrition_safety_action, …).

| Criterion | Option 1 (enum value) | Option 2 (version + metadata) |
|---|---|---|
| **Backward compatibility** | OK — existing rows keep their value; default unchanged. | OK — additive nullable columns; existing rows get `NULL` (read as "v1/legacy"). |
| **Rollback** | Drop+recreate CHECK without `v2_embeddings` — **only safe if zero v2 rows exist** (else those rows violate the new CHECK). | `alter table … drop column` — always safe (additive, nullable); no row rewrites. |
| **Report / export impact** | `ai_assisted` disclosure logic must learn a 5th value; `protein_tracker` counters and `coverage.py` text need a new branch; `api.ts` union grows. | Existing readers untouched (match_method enum unchanged). New disclosure is **opt-in**: read `source_version` to add a "V2 (voyage-4-lite)" note. |
| **Distinguish V1 vs V2** | Yes, but at the cost of conflating *engine* with *pick-method*. | Yes — cleanly, via `source_version`, orthogonal to `match_method`. |
| **Store model / provider / top_k / review id** | **No** — a single enum value can't carry provenance. | **Yes** — `source_metadata` JSONB holds the full audit trail. |
| **Manual-override safety** | Unaffected (`manual` still its own value). | Unaffected (`manual` still its own value; `source_version` is just a tag). |
| **Future V3 compatibility** | Another enum value + another migration each time. | New `source_version` value (or just metadata) — **no enum churn**. |
| **Semantic cleanliness** | Conflates "how picked" (deterministic/ai) with "which engine" (v1/v2). A V2 row can no longer also say it was ai-assisted vs rule-gated. | `match_method` keeps meaning "how picked"; `source_version` means "which engine". Orthogonal and honest. |

### Recommendation — **Option 2**

Adopt **`source_version` + `source_metadata` JSONB**. It is the more robust,
audit-friendly, and reversible choice for a precision-first program:

1. **Cleanest rollback** — dropping two nullable columns can never invalidate an
   existing row, unlike Option 1's CHECK tightening.
2. **Full provenance** — V2 quality depends on knowing the embedding provider,
   model, `top_k`, the originating review-package / apply-plan id, the
   `matcher_confidence`, and the `nutrition_safety_action`. Only `source_metadata`
   can carry these.
3. **No enum churn for V3** and zero forced changes to existing readers; the
   only new reader work is an *additive, optional* report disclosure.
4. **Honest semantics** — a V2 row records `match_method='ai_assisted'` (a model
   participated in selection) **and** `source_version='v2_embeddings'`, so the
   existing "AI assisted matching" disclosure already covers it while the new
   tag distinguishes it from the V1 LLM-shortlist path.

**Mapping to the V2-T apply plan.** The planner emits
`proposed_source_tag = "nevo_v2_embeddings"` and a placeholder
`proposed_match_method = "v2_embeddings"`. Under Option 2 these map as:
`proposed_source_tag → source_version`; the persisted **`match_method` stays
within the existing enum** (`'ai_assisted'` for embeddings-retrieved rows). The
`proposed_match_method` field is therefore superseded by this design and should
be read as the *source_version*, not the DB `match_method` column. (No planner
code change is made in this phase.)

---

## Part C — Migration spec (draft, NOT applied)

### Proposed SQL — `0037_phaseXX_nevo_v2_source_version.sql` (Option 2)

```sql
-- DRAFT — do not apply until the V2 apply phase is approved.
alter table public.nutrition_enrichment_records
    add column if not exists source_version text;          -- 'v1' | 'v2_embeddings' | future
alter table public.nutrition_enrichment_records
    add column if not exists source_metadata jsonb;        -- provider/model/top_k/review id/...

comment on column public.nutrition_enrichment_records.source_version is
    'Matching engine that produced this record. NULL / "v1" = legacy '
    'deterministic+AI-shortlist pipeline; "v2_embeddings" = NEVO V2 embeddings '
    'retrieval + concept-gate. match_method still records HOW the reference was '
    'picked (deterministic/ai_assisted/manual/none); this records WHICH engine.';
comment on column public.nutrition_enrichment_records.source_metadata is
    'Provenance for non-v1 rows: {provider, model, top_k, review_package_id, '
    'apply_plan_id, matcher_confidence, nutrition_safety_action}. Audit only; '
    'protein values always come from the matched reference row.';

-- Optional, deferrable: an index only if reports filter by engine at scale.
-- create index if not exists nutrition_enrichment_records_source_version_idx
--     on public.nutrition_enrichment_records(source_version);
```

`match_method` CHECK is **unchanged**. No data rewrite. (Alternative Option 1
SQL, for the record, is the single `add constraint … check (… 'v2_embeddings')`
shown in Part B.)

### Affected Python models
- `domain/enrichment.py` — add `source_version: str | None = None` and
  `source_metadata: dict | None = None` to `NutritionEnrichmentRecord`
  (nullable, defaulting `None` → 100% back-compatible). Optionally add a
  `SourceVersion` StrEnum (`V1`, `V2_EMBEDDINGS`). Keep `match_method` a `str`.
- A small constants module (e.g. `classification_v2/apply_constants.py`) could
  hold `V2_SOURCE_VERSION = "v2_embeddings"` shared by the planner and the
  future apply path — **not added in this phase.**

### Affected store / mapper methods
- `persistence/mappers.py` — `enrichment_record_to_row` writes
  `source_version` + `source_metadata`; `enrichment_record_from_row` reads them
  with `None` defaults (pre-migration rows have no such keys).
- `persistence/postgres.py:add_enrichment_record` — unchanged (it already
  inserts the whole mapped row); reads use `select *` so the columns surface
  automatically.
- `persistence/protocol.py` — signatures unchanged.

### Report / export (additive, optional)
- `protein_tracker.py` / `exports/coverage.py` — may add a "N product(s)
  enriched via NEVO V2 (provider/model)" disclosure by reading `source_version`.
  Until then, V2 rows (written with `match_method='ai_assisted'`) are correctly
  covered by the existing AI-assisted disclosure. No change is *required*.
- `apps/web/lib/api.ts` — add `source_version?: "v1" | "v2_embeddings" | null`
  to the type (purely additive; no existing branch depends on it).

### Tests needed (when the migration + apply land — NOT now)
- mapper round-trips `source_version` / `source_metadata` (incl. `None`).
- pre-migration row (no keys) → model defaults to `None` (back-compat).
- a V2 apply writes `match_method='ai_assisted'`, `source_version='v2_embeddings'`,
  and populated `source_metadata`.
- **never overwrite `manual`**: apply skips (refuses) a product that already has
  a `match_method='manual'` record.
- **never overwrite V1 unless explicit**: apply skips an existing V1 record
  unless an explicit `--overwrite-v1` flag is set (default false; the V2-T plan
  already records `overwrite_existing_v1=false`).
- report disclosure counts V2 rows correctly once added.
- migration-applied check: `v2_embeddings` source_version inserts succeed; (for
  Option 1's alternative) the CHECK accepts the new value.

### Rollback SQL (Option 2 — always safe)
```sql
alter table public.nutrition_enrichment_records drop column if exists source_metadata;
alter table public.nutrition_enrichment_records drop column if exists source_version;
```
Operational rollback (no SQL): set `ALTERA_NEVO_MATCHER_VERSION=v1` (or unset)
and do not run apply. If V2 rows were ever written, delete them with
`delete from public.nutrition_enrichment_records where source_version = 'v2_embeddings';`
(Option 1 rollback would additionally require removing those rows *before*
re-tightening the CHECK.)

### Data backfill policy
- **No backfill.** Existing rows keep `source_version = NULL`, interpreted as
  `v1`/legacy. The columns are additive and nullable; historical records are
  never rewritten or re-tagged.

### Hard constraints carried into the apply phase
- **Never overwrite `manual`** records.
- **Never overwrite V1** records unless an explicit opt-in flag is passed
  (default off).
- **V1 remains the default matcher**; V2 apply runs only behind the existing
  `ALTERA_NEVO_MATCHER_VERSION=v2-embeddings` + `ALTERA_ENABLE_EMBEDDINGS=true`
  gates, admin/internal only.
- Apply stays blocked until this migration is actually applied — the planner's
  `db_apply_status = blocked_pending_schema_migration` flips only then.

---

## Part D — Status of this phase

Documentation only. No Python/SQL/TS code changed, so existing tests stay green,
V1 stays default, embeddings stay off, no route imports V2, and **no apply path
writes the DB**. The recommendation (Option 2) and the draft SQL above are the
deliverable; the actual migration file and code changes are deferred to a
future, explicitly-approved apply phase.
