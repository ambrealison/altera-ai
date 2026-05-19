-- Phase 16: background job tracking table
--
-- Jobs represent units of pipeline work (validate, ingest, classify,
-- calculate, export). The SyncDevRunner executes jobs synchronously in
-- the same process; future workers (Celery, RQ) will poll this table.

-- --------------------------------------------------------------------
-- Enums
-- --------------------------------------------------------------------

CREATE TYPE job_type AS ENUM (
    'validate_upload',
    'ingest_upload',
    'classify_upload',
    'run_calculation',
    'generate_export',
    'generate_report'
);

CREATE TYPE job_status AS ENUM (
    'queued',
    'running',
    'succeeded',
    'failed',
    'cancelled',
    'retrying'
);

-- --------------------------------------------------------------------
-- Table
-- --------------------------------------------------------------------

CREATE TABLE jobs (
    job_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id UUID        NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    project_id      UUID        NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    upload_id       UUID        REFERENCES uploads(id)                ON DELETE SET NULL,
    run_id          UUID        REFERENCES calculation_runs(id)       ON DELETE SET NULL,
    job_type        job_type    NOT NULL,
    status          job_status  NOT NULL DEFAULT 'queued',
    progress_pct    SMALLINT    CHECK (progress_pct BETWEEN 0 AND 100),
    created_by      UUID        NOT NULL REFERENCES auth.users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    failed_at       TIMESTAMPTZ,
    error_message   TEXT,
    retry_count     SMALLINT    NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    payload         JSONB       NOT NULL DEFAULT '{}'
);

-- Prevent duplicate active jobs with the same idempotency key.
CREATE UNIQUE INDEX idx_jobs_idempotency_active
    ON jobs (idempotency_key)
    WHERE idempotency_key IS NOT NULL
      AND status IN ('queued', 'running');

-- Fast lookup by project (used by list_jobs_for_project).
CREATE INDEX idx_jobs_project_id ON jobs (project_id);

-- Fast lookup by upload (used by upload status polling).
CREATE INDEX idx_jobs_upload_id ON jobs (upload_id) WHERE upload_id IS NOT NULL;

-- Fast lookup by run (used by export status polling).
CREATE INDEX idx_jobs_run_id ON jobs (run_id) WHERE run_id IS NOT NULL;

-- --------------------------------------------------------------------
-- Audit-log event types (extend existing CHECK constraint)
-- --------------------------------------------------------------------

-- Add new job lifecycle actions to the audit_events action column.
-- The existing constraint uses a CHECK; we drop and recreate it.
ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS audit_events_action_check;

ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_action_check CHECK (
        action IN (
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
            'commercial_data_block',
            -- Phase 16 job lifecycle
            'job.created',
            'job.started',
            'job.succeeded',
            'job.failed',
            'job.retrying',
            'job.cancelled'
        )
    );

-- --------------------------------------------------------------------
-- RLS — jobs are org-scoped; Altera staff see all
-- --------------------------------------------------------------------

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY jobs_org_isolation ON jobs
    USING (organisation_id IN (SELECT public.visible_organisation_ids()));
