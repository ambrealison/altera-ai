-- Phase 33I-AI — track whether a nutrition enrichment record came from a
-- deterministic match or an AI-assisted match (or manual entry).
--
-- Background:
--   AI assists *matching* (picking a NEVO/CIQUAL reference for a product
--   whose name the deterministic matcher could not resolve). AI does NOT
--   invent nutrition values — the protein values stored in the record
--   continue to come from the matched reference table row only. This
--   column lets the calculation and the report disclose how many
--   enriched products had their reference picked by AI.
--
-- Existing rows: defaulted to 'deterministic' — they were all created by
-- the Phase 33H exact-name matcher.

alter table public.nutrition_enrichment_records
    add column if not exists match_method text
        not null
        default 'deterministic';

alter table public.nutrition_enrichment_records
    drop constraint if exists nutrition_enrichment_records_match_method_check;

alter table public.nutrition_enrichment_records
    add constraint nutrition_enrichment_records_match_method_check
        check (match_method in ('deterministic', 'ai_assisted', 'manual'));

comment on column public.nutrition_enrichment_records.match_method is
    'How the reference for this record was selected. '
    '"deterministic" = exact/alias/token match on the reference table. '
    '"ai_assisted" = LLM picked the reference from a deterministic '
    'candidate shortlist. AI never supplies nutrition values; protein '
    'values always come from the matched reference row.';
