-- 0004_uploads.sql

create table public.uploads (
  id                 uuid primary key default gen_random_uuid(),
  organisation_id    uuid not null references public.organisations(id) on delete cascade,
  project_id         uuid not null references public.projects(id) on delete cascade,
  storage_path       text not null,
  original_filename  text not null check (length(original_filename) between 1 and 400),
  status             text not null
                     check (status in ('pending', 'validating', 'valid', 'invalid')),
  row_count          integer check (row_count is null or row_count >= 0),
  dropped_columns    text[] not null default '{}',
  uploaded_by        uuid references auth.users(id),
  created_at         timestamptz not null default now(),

  -- row_count must be set once the upload has been validated either way.
  constraint uploads_row_count_required_when_resolved check (
    status not in ('valid', 'invalid') or row_count is not null
  )
);

create index uploads_project_idx on public.uploads (project_id);
create index uploads_org_idx on public.uploads (organisation_id);

comment on table public.uploads is
  'One row per CSV submitted by a user. Storage path points into Supabase Storage.';
