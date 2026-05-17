-- 0002_tenants.sql
--
-- Organisations and memberships. Organisation is the root of multi-
-- tenant access control; every other multi-tenant table carries
-- organisation_id and is RLS-scoped by membership.

create table public.organisations (
  id              uuid primary key default gen_random_uuid(),
  name            text not null check (length(name) between 1 and 200),
  slug            text not null unique
                  check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and length(slug) between 1 and 80),
  suspended_at    timestamptz,
  soft_deleted_at timestamptz,
  created_at      timestamptz not null default now()
);

comment on table public.organisations is
  'The top-level tenant. Every multi-tenant entity belongs to exactly one organisation.';

create table public.memberships (
  user_id         uuid not null references auth.users(id) on delete cascade,
  organisation_id uuid not null references public.organisations(id) on delete cascade,
  role            text not null check (role in ('owner', 'admin', 'analyst', 'reviewer', 'viewer')),
  created_at      timestamptz not null default now(),
  primary key (user_id, organisation_id)
);

create index memberships_org_idx on public.memberships (organisation_id);

comment on table public.memberships is
  'Joins auth.users to organisations with a per-org role.';

-- Reserved slugs (cannot be used as org URL slugs).
create table public.reserved_slugs (
  slug text primary key
);
insert into public.reserved_slugs (slug) values
  ('admin'), ('api'), ('app'), ('auth'), ('billing'), ('console'),
  ('dashboard'), ('docs'), ('login'), ('logout'), ('public'), ('settings'),
  ('signup'), ('signin'), ('signout'), ('static'), ('support'), ('system');

alter table public.organisations
  add constraint organisations_slug_not_reserved
  check (slug not in (select slug from public.reserved_slugs))
  not valid;  -- check trigger added below; the NOT VALID skips existing rows
-- A SQL check can't reference another table, so we additionally guard via
-- a trigger. The constraint above stays for documentation; the trigger
-- below enforces it.

create or replace function public.guard_organisation_slug()
returns trigger
language plpgsql
as $$
begin
  if exists (select 1 from public.reserved_slugs where slug = new.slug) then
    raise exception 'slug % is reserved', new.slug using errcode = '23514';
  end if;
  return new;
end
$$;

create trigger trg_guard_org_slug
before insert or update of slug on public.organisations
for each row execute function public.guard_organisation_slug();
