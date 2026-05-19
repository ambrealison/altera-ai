-- Phase 14B: extend write RLS policies to the namespaced roles.
--
-- Migration 0011 wrote every INSERT/UPDATE/DELETE policy against the
-- legacy single-namespace roles ('owner', 'admin', 'analyst', ...).
-- Migration 0017 then added two new role namespaces (altera_* and
-- client_*) and updated SELECT policies — but write policies were left
-- on the legacy allow-list. Result: a user with role='altera_admin'
-- can read everything via visible_organisation_ids() but cannot
-- INSERT into projects/uploads/etc. PostgREST surfaces this as
-- '42501: new row violates row-level security policy'.
--
-- This migration introduces three role-tier helper functions and
-- rewrites every legacy write policy to use them. Org-scope (must
-- be a member of the target org) is unchanged.

-- ---------------------------------------------------------------------
-- Role-tier helpers
-- ---------------------------------------------------------------------

create or replace function public.user_role_can_admin_org(org uuid)
returns boolean
language sql stable security definer set search_path = public, auth
as $$
  select public.user_role_in(org) in (
    'owner', 'admin',
    'altera_admin',
    'client_owner', 'client_admin'
  )
$$;

create or replace function public.user_role_can_write_org_data(org uuid)
returns boolean
language sql stable security definer set search_path = public, auth
as $$
  select public.user_role_in(org) in (
    'owner', 'admin', 'analyst',
    'altera_admin', 'altera_analyst', 'altera_methodology_lead',
    'client_owner', 'client_admin'
  )
$$;

create or replace function public.user_role_can_review_org_data(org uuid)
returns boolean
language sql stable security definer set search_path = public, auth
as $$
  select public.user_role_in(org) in (
    'owner', 'admin', 'analyst', 'reviewer',
    'altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead',
    'client_owner', 'client_admin'
  )
$$;

-- ---------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------

drop policy if exists projects_insert on public.projects;
create policy projects_insert on public.projects
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists projects_update on public.projects;
create policy projects_update on public.projects
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists projects_delete on public.projects;
create policy projects_delete on public.projects
  for delete using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_admin_org(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Uploads
-- ---------------------------------------------------------------------

drop policy if exists uploads_insert on public.uploads;
create policy uploads_insert on public.uploads
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists uploads_update on public.uploads;
create policy uploads_update on public.uploads
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Products + composite ingredients
-- ---------------------------------------------------------------------

drop policy if exists products_insert on public.products;
create policy products_insert on public.products
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists products_update on public.products;
create policy products_update on public.products
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists pci_insert on public.product_composite_ingredients;
create policy pci_insert on public.product_composite_ingredients
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists pci_update on public.product_composite_ingredients;
create policy pci_update on public.product_composite_ingredients
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Classifications (reviewer-tier — review queue work)
-- ---------------------------------------------------------------------

drop policy if exists classifications_insert on public.classifications;
create policy classifications_insert on public.classifications
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

drop policy if exists classifications_update on public.classifications;
create policy classifications_update on public.classifications
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

drop policy if exists classification_events_insert on public.classification_events;
create policy classification_events_insert on public.classification_events
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Manual reviews
-- ---------------------------------------------------------------------

drop policy if exists manual_reviews_insert on public.manual_reviews;
create policy manual_reviews_insert on public.manual_reviews
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

drop policy if exists manual_reviews_update on public.manual_reviews;
create policy manual_reviews_update on public.manual_reviews
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

drop policy if exists manual_reviews_delete on public.manual_reviews;
create policy manual_reviews_delete on public.manual_reviews
  for delete using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_review_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Calculation runs + rows
-- ---------------------------------------------------------------------

drop policy if exists calculation_runs_insert on public.calculation_runs;
create policy calculation_runs_insert on public.calculation_runs
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists calculation_runs_update on public.calculation_runs;
create policy calculation_runs_update on public.calculation_runs
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

drop policy if exists calculation_rows_insert on public.calculation_rows;
create policy calculation_rows_insert on public.calculation_rows
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_can_write_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Audit events SELECT — restore analyst+ gate with namespaces
-- (audit_events_insert checks org membership only — left alone.)
-- ---------------------------------------------------------------------

drop policy if exists audit_events_select on public.audit_events;
create policy audit_events_select on public.audit_events
  for select using (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_can_write_org_data(organisation_id)
  );

-- ---------------------------------------------------------------------
-- Report exports — any role in the org can request one (viewer too).
-- We rewrite the policy so client_viewer is also allowed.
-- ---------------------------------------------------------------------

drop policy if exists report_exports_insert on public.report_exports;
create policy report_exports_insert on public.report_exports
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in (
      'owner', 'admin', 'analyst', 'viewer', 'reviewer',
      'altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead',
      'client_owner', 'client_admin', 'client_viewer'
    )
  );

-- ---------------------------------------------------------------------
-- Storage objects — uploads bucket INSERT
-- ---------------------------------------------------------------------

drop policy if exists uploads_storage_insert on storage.objects;
create policy uploads_storage_insert on storage.objects
  for insert with check (
    bucket_id = 'uploads'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.current_user_organisations())
    and public.user_role_can_write_org_data((split_part(name, '/', 2))::uuid)
  );
