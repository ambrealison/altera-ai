-- 0001_extensions_and_helpers.sql
--
-- Extensions and forward-declared helper functions used by RLS
-- policies in later migrations.

create extension if not exists "pgcrypto";        -- gen_random_uuid()
create extension if not exists "citext";          -- case-insensitive text for emails
create extension if not exists "pg_trgm";         -- trigram indexes for product_name search

-- Forward declaration: the body is replaced in 0011_rls_policies.sql once
-- the memberships table exists. Declaring it here lets later migration
-- files reference it without ordering pain.
create or replace function public.current_user_organisations()
returns setof uuid
language sql
stable
security definer
set search_path = public, auth
as $$
  select null::uuid where false
$$;

create or replace function public.user_role_in(org uuid)
returns text
language sql
stable
security definer
set search_path = public, auth
as $$
  select null::text where false
$$;

comment on function public.current_user_organisations() is
  'Returns the set of organisation_ids the authenticated user has membership in. Body filled in by 0011_rls_policies.sql.';
comment on function public.user_role_in(uuid) is
  'Returns the role of the authenticated user in the given organisation, or NULL. Body filled in by 0011_rls_policies.sql.';
