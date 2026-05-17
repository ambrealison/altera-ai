-- 0006_classifications.sql
--
-- Current classification per (product, methodology), plus the immutable
-- event log. The current row is overwritten on each new decision; the
-- event log is append-only.

create table public.classifications (
  product_id          uuid not null references public.products(id) on delete cascade,
  methodology         text not null check (methodology in ('protein_tracker', 'wwf')),
  organisation_id     uuid not null references public.organisations(id) on delete cascade,
  category            text not null,
  -- WWF carries structured sub-fields; PT collapses to pt_group only.
  wwf_is_composite    boolean,
  wwf_subgroup        text,
  wwf_composite_step1_bucket text check (
    wwf_composite_step1_bucket is null
    or wwf_composite_step1_bucket in ('meat_based', 'seafood_based', 'vegetarian', 'vegan')
  ),
  source              text not null check (source in ('deterministic', 'ai', 'manual_review')),
  confidence          numeric not null check (confidence >= 0 and confidence <= 1),
  rule_id             text,
  ai_prompt_version   text,
  ai_model            text,
  reviewer_user_id    uuid references auth.users(id),
  review_reason       text,
  updated_at          timestamptz not null default now(),

  primary key (product_id, methodology),

  -- PT categories
  constraint classifications_pt_category_valid check (
    methodology <> 'protein_tracker'
    or category in (
      'plant_based_core', 'plant_based_non_core', 'composite_products',
      'animal_core', 'out_of_scope', 'unknown'
    )
  ),

  -- WWF categories
  constraint classifications_wwf_category_valid check (
    methodology <> 'wwf'
    or category in ('FG1','FG2','FG3','FG4','FG5','FG6','FG7','out_of_scope','unknown')
  ),

  -- Composite Step-1 bucket only meaningful when wwf_is_composite=true.
  constraint classifications_composite_bucket_consistent check (
    (wwf_is_composite is true and wwf_composite_step1_bucket is not null)
    or (wwf_is_composite is not true and wwf_composite_step1_bucket is null)
  ),

  -- source-dependent provenance (mirrors the domain validator).
  constraint classifications_deterministic_has_rule check (
    source <> 'deterministic' or rule_id is not null
  ),
  constraint classifications_deterministic_confidence_one check (
    source <> 'deterministic' or confidence = 1
  ),
  constraint classifications_ai_has_prompt_and_model check (
    source <> 'ai' or (ai_prompt_version is not null and ai_model is not null)
  ),
  constraint classifications_manual_has_reviewer check (
    source <> 'manual_review' or reviewer_user_id is not null
  )
);

create index classifications_methodology_idx on public.classifications (methodology);
create index classifications_org_idx on public.classifications (organisation_id);

comment on table public.classifications is
  'Most recent classification per (product, methodology). Updated by deterministic engine, AI, or reviewer.';

create table public.classification_events (
  id                  uuid primary key default gen_random_uuid(),
  product_id          uuid not null references public.products(id) on delete cascade,
  methodology         text not null check (methodology in ('protein_tracker', 'wwf')),
  organisation_id     uuid not null references public.organisations(id) on delete cascade,
  from_category       text,
  to_category         text,
  source              text not null check (source in ('deterministic', 'ai', 'manual_review')),
  confidence          numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  reviewer_user_id    uuid references auth.users(id),
  reason              text,
  created_at          timestamptz not null default now()
);

create index classification_events_product_idx on public.classification_events (product_id);
create index classification_events_org_idx on public.classification_events (organisation_id);
create index classification_events_created_idx on public.classification_events (created_at desc);

comment on table public.classification_events is
  'Append-only audit trail for every classification decision (deterministic, AI, manual review).';
