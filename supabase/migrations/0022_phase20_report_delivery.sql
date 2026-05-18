-- Phase 20: report delivery lifecycle
--
-- Adds the six columns required by the Phase 20 submit-for-review /
-- approve / deliver workflow, widens the approval_status CHECK constraint
-- to include the new lifecycle states, and tightens RLS so that:
--   * any Altera-internal user may UPDATE an export (fine-grained role
--     checks remain in FastAPI),
--   * client users only SELECT exports that are approved or delivered.
-- -------------------------------------------------------------------------

-- -------------------------------------------------------------------------
-- 1.  New columns
-- -------------------------------------------------------------------------
alter table public.report_exports
  add column if not exists under_review_by uuid references auth.users(id),
  add column if not exists under_review_at timestamptz,
  add column if not exists delivered_by uuid references auth.users(id),
  add column if not exists delivered_at timestamptz,
  add column if not exists client_downloaded_at timestamptz,
  add column if not exists client_download_count integer not null default 0;

-- -------------------------------------------------------------------------
-- 2.  Widen the approval_status CHECK constraint
--
--     Postgres does not support ALTER CONSTRAINT directly, so we drop the
--     old constraint by name (created implicitly as
--     report_exports_approval_status_check) and add a new one.
-- -------------------------------------------------------------------------
alter table public.report_exports
  drop constraint if exists report_exports_approval_status_check;

alter table public.report_exports
  add constraint report_exports_approval_status_check
    check (approval_status in ('draft', 'under_review', 'approved', 'rejected', 'delivered'));

-- -------------------------------------------------------------------------
-- 3.  RLS: UPDATE policy
--
--     Replace the methodology-lead-only `report_exports_approve` policy
--     with a broader `report_exports_update` that allows any Altera-internal
--     user to write.  Fine-grained lifecycle checks (who may submit /
--     approve / reject / deliver) are enforced in FastAPI.
-- -------------------------------------------------------------------------
drop policy if exists report_exports_approve on public.report_exports;
drop policy if exists report_exports_update on public.report_exports;

create policy report_exports_update on public.report_exports
  for update
  using (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_in(
      (select organisation_id from public.memberships
       where user_id = auth.uid() limit 1)
    ) in ('altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead')
  )
  with check (
    organisation_id in (select public.visible_organisation_ids())
    and public.user_role_in(
      (select organisation_id from public.memberships
       where user_id = auth.uid() limit 1)
    ) in ('altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead')
  );

-- -------------------------------------------------------------------------
-- 4.  RLS: SELECT policy
--
--     Tighten the existing `report_exports_select` policy so that
--     non-Altera (client) users can only see exports that have been
--     approved or delivered.  Altera-internal users see everything in
--     their visible organisations as before.
--
--     We detect Altera users by checking whether their membership role
--     starts with 'altera_'.  The helper user_role_in() returns the
--     caller's role in the supplied organisation; we join against the
--     export's own organisation_id.
-- -------------------------------------------------------------------------
drop policy if exists report_exports_select on public.report_exports;

create policy report_exports_select on public.report_exports
  for select using (
    organisation_id in (select public.visible_organisation_ids())
    and (
      -- Altera-internal users see all statuses
      public.user_role_in(organisation_id) in (
        'altera_admin', 'altera_analyst', 'altera_reviewer', 'altera_methodology_lead'
      )
      -- Clients only see approved / delivered exports
      or approval_status in ('approved', 'delivered')
    )
  );
