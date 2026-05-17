-- 0008_calculation_runs.sql

create table public.calculation_runs (
  id                          uuid primary key default gen_random_uuid(),
  project_id                  uuid not null references public.projects(id) on delete cascade,
  organisation_id             uuid not null references public.organisations(id) on delete cascade,
  methodology                 text not null check (methodology in ('protein_tracker', 'wwf')),
  methodology_version         text not null,
  methodology_source_edition  text not null,
  taxonomy_version            text not null,
  rules_version               text not null,
  reporting_period_label      text not null,
  status                      text not null
                              check (status in ('pending', 'running', 'success', 'failed')),
  started_at                  timestamptz,
  finished_at                 timestamptz,
  triggered_by                uuid references auth.users(id),
  created_at                  timestamptz not null default now()
);

create index calculation_runs_project_idx on public.calculation_runs (project_id);
create index calculation_runs_org_idx on public.calculation_runs (organisation_id);
create index calculation_runs_methodology_idx on public.calculation_runs (methodology);

comment on table public.calculation_runs is
  'One calculation run on a project for one methodology. Version-stamped for reproducibility.';

create table public.calculation_rows (
  run_id              uuid not null references public.calculation_runs(id) on delete cascade,
  product_id          uuid not null references public.products(id) on delete cascade,
  organisation_id     uuid not null references public.organisations(id) on delete cascade,
  in_scope            boolean not null,

  -- PT-specific (NULL on WWF runs)
  pt_group            text check (
    pt_group is null
    or pt_group in (
      'plant_based_core','plant_based_non_core','composite_products',
      'animal_core','out_of_scope','unknown'
    )
  ),
  volume_kg                  numeric,
  protein_pct                numeric,
  protein_kg                 numeric,
  used_per_product_split     boolean,
  plant_protein_kg           numeric,
  animal_protein_kg          numeric,

  -- WWF-specific (NULL on PT runs)
  wwf_food_group             text check (
    wwf_food_group is null
    or wwf_food_group in ('FG1','FG2','FG3','FG4','FG5','FG6','FG7','out_of_scope','unknown')
  ),
  wwf_subgroup               text,
  weight_kg                  numeric,
  weight_kg_dairy_equiv      numeric,
  wwf_is_composite           boolean,
  wwf_composite_step1_bucket text check (
    wwf_composite_step1_bucket is null
    or wwf_composite_step1_bucket in ('meat_based','seafood_based','vegetarian','vegan')
  ),
  wwf_step2_ingredient_weights jsonb,
  retail_channel             text check (
    retail_channel is null or retail_channel in ('fresh','grocery_ambient','frozen')
  ),

  primary key (run_id, product_id)
);

create index calculation_rows_org_idx on public.calculation_rows (organisation_id);

comment on table public.calculation_rows is
  'Per-product calculation outputs for a run. PT-specific and WWF-specific columns are populated per the run methodology.';
