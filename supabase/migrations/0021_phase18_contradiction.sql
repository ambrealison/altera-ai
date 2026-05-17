-- 0021_phase18_contradiction.sql
--
-- Add 'contradiction_detected' to the manual_reviews reason CHECK constraint.
-- Phase 18 introduces deterministic contradiction detection in the rules engine
-- (e.g. vegan label + animal ingredient, vegetarian label + meat ingredient,
-- plant-based claim + whey protein, pet-food / non-food signals).
-- Products flagged as contradictions bypass the AI classifier and route
-- directly to Altera manual review with this reason.

alter table public.manual_reviews
  drop constraint if exists manual_reviews_reason_check;

alter table public.manual_reviews
  add constraint manual_reviews_reason_check
  check (reason in (
    'low_confidence',
    'ai_parse_failed',
    'ai_provider_error',
    'rule_collision',
    'contradiction_detected',
    'requested'
  ));
