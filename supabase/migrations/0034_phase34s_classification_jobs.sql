-- Phase 34S — async, chunked AI classification jobs (production persistence).
--
-- Phase 34R introduced the ClassificationJob domain entity and routes,
-- but the production PostgresRepository was a NotImplementedError stub.
-- This migration adds the real table so staging/prod can run chunked
-- async classification against the same data plane as everything else.
--
-- Design notes:
-- - Status is stored as a TEXT enum with a CHECK constraint rather than
--   a Postgres ENUM type, so we can add new states without ALTER TYPE.
-- - pending_product_ids / failed_product_ids / sample_errors are JSONB
--   so they can grow with a job's progress without schema changes.
-- - updated_at is the optimistic-concurrency token: every successful
--   advance bumps it, and a concurrent advance that reads the old
--   value loses the race (0 rows affected → 409 conflict to the
--   second caller).

CREATE TABLE classification_jobs (
    id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id          UUID         NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    project_id               UUID         NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    upload_id                UUID         NOT NULL REFERENCES uploads(id)       ON DELETE CASCADE,
    methodology              TEXT         NOT NULL,
    status                   TEXT         NOT NULL DEFAULT 'queued',
    total_products           INTEGER      NOT NULL DEFAULT 0,
    processed_products       INTEGER      NOT NULL DEFAULT 0,
    pending_product_ids      JSONB        NOT NULL DEFAULT '[]',
    failed_product_ids       JSONB        NOT NULL DEFAULT '[]',
    categorized_total        INTEGER      NOT NULL DEFAULT 0,
    accepted_total           INTEGER      NOT NULL DEFAULT 0,
    review_required_total    INTEGER      NOT NULL DEFAULT 0,
    failed_total             INTEGER      NOT NULL DEFAULT 0,
    unknown_total            INTEGER      NOT NULL DEFAULT 0,
    out_of_scope_total       INTEGER      NOT NULL DEFAULT 0,
    retry_batches            INTEGER      NOT NULL DEFAULT 0,
    recovered_rows           INTEGER      NOT NULL DEFAULT 0,
    overwrite                BOOLEAN      NOT NULL DEFAULT FALSE,
    only_missing_or_failed   BOOLEAN      NOT NULL DEFAULT TRUE,
    batch_size               INTEGER      NOT NULL DEFAULT 25,
    cancel_requested         BOOLEAN      NOT NULL DEFAULT FALSE,
    error_code               TEXT,
    error_message            TEXT,
    sample_errors            JSONB        NOT NULL DEFAULT '[]',
    metadata                 JSONB        NOT NULL DEFAULT '{}',
    created_by               UUID,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at               TIMESTAMPTZ,
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,
    cancelled_at             TIMESTAMPTZ,

    CONSTRAINT classification_jobs_status_check CHECK (
        status IN (
            'queued',
            'running',
            'completed',
            'completed_with_errors',
            'failed',
            'cancelled'
        )
    ),
    CONSTRAINT classification_jobs_methodology_check CHECK (
        methodology IN ('protein_tracker', 'wwf')
    ),
    CONSTRAINT classification_jobs_batch_size_check CHECK (
        batch_size BETWEEN 1 AND 100
    )
);

-- Fast lookups for the wizard's poll loop and for the admin
-- "recent jobs for project" view.
CREATE INDEX idx_classification_jobs_project_id
    ON classification_jobs (project_id);

CREATE INDEX idx_classification_jobs_upload_id
    ON classification_jobs (upload_id);

-- Status + created_at lets the admin page sort active jobs first.
CREATE INDEX idx_classification_jobs_status
    ON classification_jobs (status, created_at DESC);

-- --------------------------------------------------------------------
-- RLS — org-scoped, Altera staff see all (matches jobs/projects/etc).
-- --------------------------------------------------------------------

ALTER TABLE classification_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY classification_jobs_org_isolation ON classification_jobs
    USING (organisation_id IN (SELECT public.visible_organisation_ids()));
