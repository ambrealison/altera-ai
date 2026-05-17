-- 0009_audit_events.sql

create table public.audit_events (
  id                uuid primary key default gen_random_uuid(),
  organisation_id   uuid not null references public.organisations(id) on delete cascade,
  actor_user_id     uuid references auth.users(id),
  action            text not null,
  target_table      text,
  target_id         uuid,
  metadata          jsonb not null default '{}',
  created_at        timestamptz not null default now(),

  -- The canonical action vocabulary mirrors docs/saas/audit-logs.md.
  constraint audit_events_action_known check (
    action in (
      'organisation.created',
      'organisation.member_invited',
      'organisation.role_changed',
      'project.created',
      'upload.created',
      'upload.dropped_columns',
      'classification.batch_started',
      'classification.batch_finished',
      'run.created',
      'run.succeeded',
      'run.failed',
      'export.generated',
      'auth.signed_in',
      'pt_validation.submitted',
      'pt_validation.validated',
      'commercial_data_block'
    )
  )
);

create index audit_events_org_idx on public.audit_events (organisation_id, created_at desc);
create index audit_events_action_idx on public.audit_events (action);

comment on table public.audit_events is
  'General audit trail. Append-only enforced by the trigger in 0012_audit_immutability.sql.';

-- report_exports — generated export artefacts (CSV/JSON/Markdown, …).
create table public.report_exports (
  id                uuid primary key default gen_random_uuid(),
  run_id            uuid not null references public.calculation_runs(id) on delete cascade,
  organisation_id   uuid not null references public.organisations(id) on delete cascade,
  format            text not null check (format in ('csv', 'json', 'md', 'xlsx', 'pdf')),
  status            text not null
                    check (status in ('pending', 'success', 'failed')),
  storage_path      text,
  size_bytes        bigint check (size_bytes is null or size_bytes >= 0),
  filename          text,
  error_code        text,
  requested_by      uuid references auth.users(id),
  created_at        timestamptz not null default now(),
  finished_at       timestamptz
);

create index report_exports_run_idx on public.report_exports (run_id);
create index report_exports_org_idx on public.report_exports (organisation_id);

comment on table public.report_exports is
  'One row per generated export artefact. Files live in the `exports` storage bucket under the org-scoped path prefix.';
