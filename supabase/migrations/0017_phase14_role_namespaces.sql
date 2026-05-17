-- 0017_phase14_role_namespaces.sql
--
-- Phase 14: Two-namespace role model + Altera cross-org visibility.
--
-- 1. Extend memberships.role CHECK constraint to accept the new
--    namespaced roles alongside the legacy single-namespace set.
-- 2. Add current_user_is_altera() — true when the JWT belongs to any
--    membership in an altera_internal organisation.
-- 3. Replace current_user_organisations() with a version that returns
--    only the user's own memberships (unchanged behaviour for clients;
--    kept as-is so existing write policies continue to work).
-- 4. Add visible_organisation_ids() — like current_user_organisations()
--    but returns ALL org IDs when the caller is Altera staff. Used in
--    SELECT policies so Altera can read client data.
-- 5. Update SELECT policies on all data tables to use
--    visible_organisation_ids() instead of current_user_organisations().
--    Write policies (INSERT / UPDATE / DELETE) keep current_user_organisations()
--    — Altera staff operate through their own org's projects only.

-- -------------------------------------------------------------------------
-- Extend memberships.role CHECK constraint
-- -------------------------------------------------------------------------
alter table public.memberships
  drop constraint if exists memberships_role_check;

alter table public.memberships
  add constraint memberships_role_check check (role in (
    -- Legacy single-namespace roles (kept for backward compat)
    'owner', 'admin', 'analyst', 'reviewer', 'viewer',
    -- Client namespace (gms_client organisations)
    'client_owner', 'client_admin', 'client_viewer',
    -- Altera namespace (altera_internal organisations)
    'altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead'
  ));

-- -------------------------------------------------------------------------
-- Helper: is the current JWT holder an Altera-internal user?
-- -------------------------------------------------------------------------
create or replace function public.current_user_is_altera()
returns boolean
language sql
stable
security definer
set search_path = public, auth
as $$
  select exists (
    select 1
    from public.memberships m
    join public.organisations o on o.id = m.organisation_id
    where m.user_id = auth.uid()
      and o.organisation_type = 'altera_internal'
  )
$$;

-- -------------------------------------------------------------------------
-- Helper: visible_organisation_ids()
-- Altera staff see ALL organisations; clients see only their memberships.
-- Used in SELECT policies — write policies remain scoped to memberships.
-- -------------------------------------------------------------------------
create or replace function public.visible_organisation_ids()
returns setof uuid
language sql
stable
security definer
set search_path = public, auth
as $$
  select case
    when public.current_user_is_altera() then
      (select id from public.organisations)
    else
      (select organisation_id from public.memberships where user_id = auth.uid())
  end
$$;

-- -------------------------------------------------------------------------
-- Update SELECT policies on data tables to visible_organisation_ids().
-- We drop and recreate each *_select policy; write policies are untouched.
-- -------------------------------------------------------------------------

-- Organisations
drop policy if exists organisations_select on public.organisations;
create policy organisations_select on public.organisations
  for select using (id in (select public.visible_organisation_ids()));

-- Projects
drop policy if exists projects_select on public.projects;
create policy projects_select on public.projects
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Uploads
drop policy if exists uploads_select on public.uploads;
create policy uploads_select on public.uploads
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Products
drop policy if exists products_select on public.products;
create policy products_select on public.products
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Product composite ingredients
drop policy if exists pci_select on public.product_composite_ingredients;
create policy pci_select on public.product_composite_ingredients
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Classifications + events
drop policy if exists classifications_select on public.classifications;
create policy classifications_select on public.classifications
  for select using (organisation_id in (select public.visible_organisation_ids()));

drop policy if exists classification_events_select on public.classification_events;
create policy classification_events_select on public.classification_events
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Manual reviews
drop policy if exists manual_reviews_select on public.manual_reviews;
create policy manual_reviews_select on public.manual_reviews
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Calculation runs + rows
drop policy if exists calculation_runs_select on public.calculation_runs;
create policy calculation_runs_select on public.calculation_runs
  for select using (organisation_id in (select public.visible_organisation_ids()));

drop policy if exists calculation_rows_select on public.calculation_rows;
create policy calculation_rows_select on public.calculation_rows
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Audit events (keeps analyst+ gate from original policy)
drop policy if exists audit_events_select on public.audit_events;
create policy audit_events_select on public.audit_events
  for select using (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_in(organisation_id) in (
      'owner', 'admin', 'analyst',
      'altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead'
    )
  );

-- Report exports (Altera can see all; clients see own org only)
drop policy if exists report_exports_select on public.report_exports;
create policy report_exports_select on public.report_exports
  for select using (organisation_id in (select public.visible_organisation_ids()));

-- Storage SELECT: Altera can read all upload/export objects
drop policy if exists uploads_storage_select on storage.objects;
create policy uploads_storage_select on storage.objects
  for select using (
    bucket_id = 'uploads'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.visible_organisation_ids())
  );

drop policy if exists exports_storage_select on storage.objects;
create policy exports_storage_select on storage.objects
  for select using (
    bucket_id = 'exports'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.visible_organisation_ids())
  );

-- -------------------------------------------------------------------------
-- Export approval: new policy lets altera_methodology_lead approve exports.
-- The approval_status column was added in 0015; the INSERT policy is kept
-- from 0011 but UPDATE is now allowed for methodology leads.
-- -------------------------------------------------------------------------
create policy report_exports_approve on public.report_exports
  for update
  using (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_in(
      (select organisation_id from public.memberships
       where user_id = auth.uid() limit 1)
    ) = 'altera_methodology_lead'
  )
  with check (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_in(
      (select organisation_id from public.memberships
       where user_id = auth.uid() limit 1)
    ) = 'altera_methodology_lead'
  );
