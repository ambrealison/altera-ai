-- Phase 34T — allow ``match_method='none'`` on no-match enrichment records.
--
-- Phase 33I introduced the ``nutrition_enrichment_records.match_method``
-- column with a CHECK constraint that only allowed three values:
--     ('deterministic', 'ai_assisted', 'manual')
--
-- But the apply_references route already writes ``match_method='none'``
-- when the deterministic + fuzzy + AI candidate-shortlist passes all
-- return no match (apps/api/altera_api/api/routes.py line ~4733). The
-- record is created so the wizard can show "Sans correspondance: N" in
-- the NEVO step's diagnostic panel — without the record the wizard
-- would have no idea why nothing was enriched.
--
-- In staging/prod this produced:
--     500 new row for relation "nutrition_enrichment_records" violates
--     check constraint "nutrition_enrichment_records_match_method_check"
--
-- Fix: extend the constraint to accept ``'none'`` for no-match rows.
-- We intentionally keep the enum tight (only the four values the code
-- actually emits) — additional values like 'proxy' or 'suggested' can
-- be added in a future migration when there's a real use site.

alter table public.nutrition_enrichment_records
    drop constraint if exists nutrition_enrichment_records_match_method_check;

alter table public.nutrition_enrichment_records
    add constraint nutrition_enrichment_records_match_method_check
        check (
            match_method in (
                'deterministic',
                'ai_assisted',
                'manual',
                'none'
            )
        );

comment on column public.nutrition_enrichment_records.match_method is
    'How the reference for this record was selected. '
    '"deterministic" = exact/alias/token match on the reference table. '
    '"ai_assisted" = LLM picked the reference from a deterministic '
    'candidate shortlist. '
    '"manual" = analyst entered a value through the validation table. '
    '"none" = no match was found; the record is a "Sans correspondance" '
    'audit trail. AI never supplies nutrition values; protein values '
    'always come from the matched reference row.';
