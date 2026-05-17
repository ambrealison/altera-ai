-- 0011_rls_policies.sql
--
-- Replace the forward-declared helper bodies with real ones, then
-- enable RLS on every multi-tenant table and add policies that scope
-- reads/writes to the authenticated user's organisations.
--
-- See docs/saas/rls.md for the policy patterns this file follows and
-- supabase/README.md for the table-by-table summary.

-- ---------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------
create or replace function public.current_user_organisations()
returns setof uuid
language sql
stable
security definer
set search_path = public, auth
as $$
  select organisation_id
  from public.memberships
  where user_id = auth.uid()
$$;

create or replace function public.user_role_in(org uuid)
returns text
language sql
stable
security definer
set search_path = public, auth
as $$
  select role
  from public.memberships
  where user_id = auth.uid() and organisation_id = org
  limit 1
$$;

-- ---------------------------------------------------------------------
-- Enable RLS
-- ---------------------------------------------------------------------
alter table public.organisations               enable row level security;
alter table public.memberships                 enable row level security;
alter table public.projects                    enable row level security;
alter table public.uploads                     enable row level security;
alter table public.products                    enable row level security;
alter table public.product_composite_ingredients enable row level security;
alter table public.classifications             enable row level security;
alter table public.classification_events       enable row level security;
alter table public.manual_reviews              enable row level security;
alter table public.calculation_runs            enable row level security;
alter table public.calculation_rows            enable row level security;
alter table public.audit_events                enable row level security;
alter table public.report_exports              enable row level security;

-- ---------------------------------------------------------------------
-- Organisations: a user sees only their organisations.
-- Creating an organisation goes through a SECURITY DEFINER function
-- (later); direct INSERT is service-role only.
-- ---------------------------------------------------------------------
create policy organisations_select on public.organisations
  for select using (id in (select public.current_user_organisations()));

create policy organisations_update on public.organisations
  for update
  using (
    id in (select public.current_user_organisations())
    and public.user_role_in(id) in ('owner', 'admin')
  )
  with check (
    id in (select public.current_user_organisations())
    and public.user_role_in(id) in ('owner', 'admin')
  );

-- ---------------------------------------------------------------------
-- Memberships
-- ---------------------------------------------------------------------
create policy memberships_select on public.memberships
  for select using (
    user_id = auth.uid()
    or organisation_id in (select public.current_user_organisations())
  );

create policy memberships_insert on public.memberships
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin')
  );

create policy memberships_update on public.memberships
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin')
  );

create policy memberships_delete on public.memberships
  for delete using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin')
  );

-- ---------------------------------------------------------------------
-- Projects
-- ---------------------------------------------------------------------
create policy projects_select on public.projects
  for select using (organisation_id in (select public.current_user_organisations()));

create policy projects_insert on public.projects
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy projects_update on public.projects
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy projects_delete on public.projects
  for delete using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin')
  );

-- ---------------------------------------------------------------------
-- Uploads
-- ---------------------------------------------------------------------
create policy uploads_select on public.uploads
  for select using (organisation_id in (select public.current_user_organisations()));

create policy uploads_insert on public.uploads
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy uploads_update on public.uploads
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

-- ---------------------------------------------------------------------
-- Products + Step-2 ingredients
-- ---------------------------------------------------------------------
create policy products_select on public.products
  for select using (organisation_id in (select public.current_user_organisations()));

create policy products_insert on public.products
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy products_update on public.products
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy pci_select on public.product_composite_ingredients
  for select using (organisation_id in (select public.current_user_organisations()));

create policy pci_insert on public.product_composite_ingredients
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy pci_update on public.product_composite_ingredients
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

-- ---------------------------------------------------------------------
-- Classifications
-- ---------------------------------------------------------------------
create policy classifications_select on public.classifications
  for select using (organisation_id in (select public.current_user_organisations()));

create policy classifications_insert on public.classifications
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

create policy classifications_update on public.classifications
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

-- classification_events is append-only — see audit immutability migration.
create policy classification_events_select on public.classification_events
  for select using (organisation_id in (select public.current_user_organisations()));

create policy classification_events_insert on public.classification_events
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

-- ---------------------------------------------------------------------
-- Manual reviews
-- ---------------------------------------------------------------------
create policy manual_reviews_select on public.manual_reviews
  for select using (organisation_id in (select public.current_user_organisations()));

create policy manual_reviews_insert on public.manual_reviews
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

create policy manual_reviews_update on public.manual_reviews
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

create policy manual_reviews_delete on public.manual_reviews
  for delete using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'reviewer')
  );

-- ---------------------------------------------------------------------
-- Calculation runs + rows
-- ---------------------------------------------------------------------
create policy calculation_runs_select on public.calculation_runs
  for select using (organisation_id in (select public.current_user_organisations()));

create policy calculation_runs_insert on public.calculation_runs
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy calculation_runs_update on public.calculation_runs
  for update
  using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  )
  with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy calculation_rows_select on public.calculation_rows
  for select using (organisation_id in (select public.current_user_organisations()));

create policy calculation_rows_insert on public.calculation_rows
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

-- ---------------------------------------------------------------------
-- Audit events + report exports
-- ---------------------------------------------------------------------
-- audit_events read is gated to analyst+ (viewers/reviewers don't see
-- the operational trail).
create policy audit_events_select on public.audit_events
  for select using (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst')
  );

create policy audit_events_insert on public.audit_events
  for insert with check (
    organisation_id in (select public.current_user_organisations())
  );

-- report_exports: anyone in the org can list their org's exports;
-- any role can request one (viewer can re-export an existing run).
create policy report_exports_select on public.report_exports
  for select using (organisation_id in (select public.current_user_organisations()));

create policy report_exports_insert on public.report_exports
  for insert with check (
    organisation_id in (select public.current_user_organisations())
    and public.user_role_in(organisation_id) in ('owner', 'admin', 'analyst', 'viewer')
  );

-- ---------------------------------------------------------------------
-- Storage policies: bucket access scoped by path prefix.
-- Path: organisations/<org_id>/(uploads|exports)/<rest>
-- ---------------------------------------------------------------------
create policy uploads_storage_select on storage.objects
  for select using (
    bucket_id = 'uploads'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.current_user_organisations())
  );

create policy uploads_storage_insert on storage.objects
  for insert with check (
    bucket_id = 'uploads'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.current_user_organisations())
    and public.user_role_in((split_part(name, '/', 2))::uuid)
        in ('owner', 'admin', 'analyst')
  );

create policy exports_storage_select on storage.objects
  for select using (
    bucket_id = 'exports'
    and split_part(name, '/', 1) = 'organisations'
    and (split_part(name, '/', 2))::uuid in (select public.current_user_organisations())
  );
