-- 0015_managed_saas_columns.sql
--
-- Adds the managed-SaaS lifecycle columns introduced in Phase 13C's
-- product-direction pivot. All additions are backwards-compatible
-- (NOT NULL with defaults or nullable).

-- -------------------------------------------------------------------------
-- organisations: two-namespace organisation type
-- -------------------------------------------------------------------------
alter table public.organisations
  add column if not exists organisation_type text not null default 'gms_client'
    check (organisation_type in ('gms_client', 'altera_internal'));

-- -------------------------------------------------------------------------
-- projects: internal lifecycle state machine
-- -------------------------------------------------------------------------
alter table public.projects
  add column if not exists project_status text not null default 'created'
    check (project_status in (
      'created', 'waiting_for_client_upload', 'uploaded', 'validation',
      'classification', 'altera_review_required', 'calculation',
      'report_draft', 'report_under_altera_review', 'report_approved',
      'delivered_to_client', 'archived'
    ));

-- -------------------------------------------------------------------------
-- manual_reviews: Altera-ownership marker
-- -------------------------------------------------------------------------
alter table public.manual_reviews
  add column if not exists owner_type text not null default 'altera_internal'
    check (owner_type in ('altera_internal'));

-- -------------------------------------------------------------------------
-- report_exports: mandatory approval gate
-- -------------------------------------------------------------------------
alter table public.report_exports
  add column if not exists approval_status text not null default 'draft'
    check (approval_status in ('draft', 'approved', 'rejected')),
  add column if not exists approved_by uuid references auth.users(id),
  add column if not exists approved_at timestamptz,
  add column if not exists rejected_by uuid references auth.users(id),
  add column if not exists rejected_at timestamptz,
  add column if not exists rejection_reason text,
  add column if not exists release_note text,
  add column if not exists delivered_to_client_at timestamptz;

-- -------------------------------------------------------------------------
-- calculation_runs: summary + rows payload for Phase 13B persistence
--
-- Stores the full serialised PT/WWF summary and per-row results as JSONB
-- so the in-memory RunRecord can be persisted without normalising every
-- calculation column. Future work can lift these into calculation_rows.
-- -------------------------------------------------------------------------
alter table public.calculation_runs
  add column if not exists summary_payload jsonb,
  add column if not exists rows_payload jsonb;

comment on column public.calculation_runs.summary_payload is
  'Serialised PT/WWF calculation summary (model_dump). Stores the full '
  'result object so it can be re-hydrated for export rendering.';
comment on column public.calculation_runs.rows_payload is
  'Serialised list of PT/WWF per-row results (model_dump). Lifted into '
  'calculation_rows in a future migration.';
