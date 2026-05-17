-- 0020_phase17_ai_provider_error.sql
--
-- Add 'ai_provider_error' to the manual_reviews reason CHECK constraint.
-- Previously the constraint only covered the four values present before
-- Phase 17; the AI pipeline can now route products to review when the
-- external AI provider fails (network error, 5xx, rate limit, etc.).

alter table public.manual_reviews
  drop constraint if exists manual_reviews_reason_check;

alter table public.manual_reviews
  add constraint manual_reviews_reason_check
  check (reason in (
    'low_confidence',
    'ai_parse_failed',
    'ai_provider_error',
    'rule_collision',
    'requested'
  ));
