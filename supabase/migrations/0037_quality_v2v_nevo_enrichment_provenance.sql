-- Phase Quality-V2-V — NEVO V2 enrichment provenance columns (additive only).
--
-- Background:
--   The offline V2 pipeline (dry-run enrich -> review package -> validator ->
--   apply plan) is ready, but a DB apply was blocked because
--   nutrition_enrichment_records.match_method only allows
--   ('deterministic','ai_assisted','manual','none'). Per the migration design
--   (docs/quality/v2-nevo-enrichment-persistence-migration.md, Option 2) we do
--   NOT touch match_method. Instead we add two additive, nullable provenance
--   columns so a FUTURE (still-gated) apply can persist V2 rows as:
--       source         = 'nevo'
--       match_method   = 'ai_assisted'        -- unchanged enum; a model helped pick
--       source_version = 'v2_embeddings'      -- WHICH engine produced the row
--       source_metadata = {provider, model, top_k, matcher_confidence,
--                          nutrition_safety_action, review_package_id,
--                          apply_plan_id, ...}
--
-- This migration ONLY adds columns. It does not activate V2, does not write any
-- rows, does not backfill, and does not modify the match_method CHECK. Existing
-- rows keep source_version = NULL (interpreted as v1 / legacy).
--
-- Rollback (always safe — additive nullable columns, no row rewrites):
--   alter table public.nutrition_enrichment_records drop column if exists source_metadata;
--   alter table public.nutrition_enrichment_records drop column if exists source_version;
-- Operational rollback: keep ALTERA_NEVO_MATCHER_VERSION=v1 and do not run apply.
-- If V2 rows were ever written, delete them with:
--   delete from public.nutrition_enrichment_records where source_version = 'v2_embeddings';

alter table public.nutrition_enrichment_records
    add column if not exists source_version text;

alter table public.nutrition_enrichment_records
    add column if not exists source_metadata jsonb;

comment on column public.nutrition_enrichment_records.source_version is
    'Matching ENGINE that produced this record. NULL / "v1" = legacy '
    'deterministic + AI-shortlist pipeline; "v2_embeddings" = NEVO V2 embeddings '
    'retrieval + concept-gate. Orthogonal to match_method (which records HOW the '
    'reference was picked: deterministic/ai_assisted/manual/none). Left as open '
    'text (no CHECK) so a future v3_* engine needs no enum migration.';

comment on column public.nutrition_enrichment_records.source_metadata is
    'Provenance for non-v1 rows (JSONB): provider, model, top_k, '
    'matcher_confidence, nutrition_safety_action, review_package_id, '
    'apply_plan_id, etc. Audit only — protein values always come from the '
    'matched reference row, never from this metadata.';
