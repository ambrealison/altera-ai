-- 0014_version_registry.sql
--
-- Reference registries for methodology, taxonomy, and rules-engine
-- versions. These are global (not tenant-scoped); every authenticated
-- user can read the registry so the project-creation UI can populate
-- the "pin a version" dropdowns. Inserts are service-role only —
-- versions ship with the application, not with tenant data.

create table public.methodology_versions (
  id              uuid primary key default gen_random_uuid(),
  methodology     text not null check (methodology in ('protein_tracker', 'wwf')),
  version         text not null,                        -- semver "1.0.0"
  source_edition  text not null,                        -- e.g. "GPA & ProVeg Foodservice 2024-08"
  source_citation text not null,                        -- human-readable citation
  year            integer not null check (year between 2000 and 2100),
  deprecated_at   timestamptz,
  created_at      timestamptz not null default now(),
  unique (methodology, version)
);

create index methodology_versions_active_idx
  on public.methodology_versions (methodology)
  where deprecated_at is null;

comment on table public.methodology_versions is
  'Registry of methodology versions Altera AI has shipped. NOT tenant-scoped — versions ship with the app.';

create table public.taxonomy_versions (
  id            uuid primary key default gen_random_uuid(),
  version       text not null unique,                   -- semver
  deprecated_at timestamptz,
  created_at    timestamptz not null default now()
);

create index taxonomy_versions_active_idx
  on public.taxonomy_versions (created_at desc)
  where deprecated_at is null;

comment on table public.taxonomy_versions is
  'Registry of canonical-taxonomy versions. NOT tenant-scoped.';

create table public.rules_versions (
  id            uuid primary key default gen_random_uuid(),
  version       text not null unique,                   -- semver
  deprecated_at timestamptz,
  created_at    timestamptz not null default now()
);

create index rules_versions_active_idx
  on public.rules_versions (created_at desc)
  where deprecated_at is null;

comment on table public.rules_versions is
  'Registry of deterministic-rules-engine versions. NOT tenant-scoped.';

-- RLS: every authenticated user can read the registries, but only the
-- service role can insert/update/delete (no INSERT/UPDATE/DELETE policy
-- → RLS blocks all writes from authenticated roles).
alter table public.methodology_versions enable row level security;
alter table public.taxonomy_versions    enable row level security;
alter table public.rules_versions       enable row level security;

create policy methodology_versions_select on public.methodology_versions
  for select using (auth.uid() is not null);

create policy taxonomy_versions_select on public.taxonomy_versions
  for select using (auth.uid() is not null);

create policy rules_versions_select on public.rules_versions
  for select using (auth.uid() is not null);
