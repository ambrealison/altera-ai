-- 0003_projects.sql

create table public.projects (
  id                       uuid primary key default gen_random_uuid(),
  organisation_id          uuid not null references public.organisations(id) on delete cascade,
  name                     text not null check (length(name) between 1 and 200),
  methodologies_enabled    text[] not null,
  reporting_period_label   text not null check (length(reporting_period_label) between 1 and 80),
  reporting_period_start   date,
  reporting_period_end     date,
  pinned_pt_version        text,
  pinned_wwf_version       text,
  pinned_taxonomy_version  text,
  pinned_rules_version     text,
  pt_validation_status     text not null default 'none'
                           check (pt_validation_status in ('none', 'draft', 'submitted', 'validated')),
  created_by               uuid references auth.users(id),
  created_at               timestamptz not null default now(),

  -- Methodology array must contain at least one of the two methodologies
  -- and only those values.
  constraint projects_methodologies_valid check (
    array_length(methodologies_enabled, 1) >= 1
    and methodologies_enabled <@ array['protein_tracker', 'wwf']
  ),

  -- Pinning a PT version requires PT to be enabled (mirrored in domain model).
  constraint projects_pt_pin_requires_pt check (
    pinned_pt_version is null or 'protein_tracker' = any (methodologies_enabled)
  ),
  constraint projects_wwf_pin_requires_wwf check (
    pinned_wwf_version is null or 'wwf' = any (methodologies_enabled)
  ),

  -- pt_validation_status only meaningful when PT is enabled.
  constraint projects_pt_validation_requires_pt check (
    pt_validation_status = 'none' or 'protein_tracker' = any (methodologies_enabled)
  ),

  -- Reporting period ordered.
  constraint projects_reporting_period_ordered check (
    reporting_period_start is null
    or reporting_period_end is null
    or reporting_period_end >= reporting_period_start
  )
);

create index projects_org_idx on public.projects (organisation_id);

comment on table public.projects is
  'A unit of work within an organisation. Pins methodologies + reporting period.';
