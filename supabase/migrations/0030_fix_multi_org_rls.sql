-- 0030_fix_multi_org_rls.sql
--
-- Fix: visible_organisation_ids() used a CASE expression with scalar
-- subqueries. In Postgres a CASE branch is evaluated in a scalar context,
-- so each branch must return exactly one row. After Phase 32A/32B,
-- Altera admins can belong to multiple organisations and multiple
-- organisations now exist in staging. The THEN branch:
--
--   (select id from public.organisations)
--
-- returns N rows into a scalar context →
--   "more than one row returned by a subquery used as an expression"
--
-- Fix: rewrite as a set-based query using WHERE + EXISTS. No CASE needed.
--
-- Also fix report_exports_update (from migration 0022) which used:
--   user_role_in((select organisation_id from memberships … limit 1))
-- This picks an arbitrary first membership for multi-org Altera users.
-- Replace with current_user_is_altera(); fine-grained role checks are in
-- FastAPI (not RLS).

-- ---------------------------------------------------------------------------
-- Fix visible_organisation_ids()
-- ---------------------------------------------------------------------------
create or replace function public.visible_organisation_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public, auth
as $$
  select o.id
  from public.organisations o
  where public.current_user_is_altera()
     or exists (
       select 1 from public.memberships m
       where m.user_id = auth.uid()
         and m.organisation_id = o.id
     )
$$;

-- ---------------------------------------------------------------------------
-- Fix report_exports_update policy
-- ---------------------------------------------------------------------------
drop policy if exists report_exports_update on public.report_exports;

create policy report_exports_update on public.report_exports
  for update
  using (
    organisation_id in (select public.visible_organisation_ids())
    and public.current_user_is_altera()
  )
  with check (
    organisation_id in (select public.visible_organisation_ids())
    and public.current_user_is_altera()
  );
