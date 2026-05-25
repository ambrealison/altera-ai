-- Phase 34X — chunked, resumable CSV ingestion jobs for 10K-15K rows.
--
-- The synchronous ``POST /uploads`` path was still failing in
-- production on 1050-row CSVs even after Phase 34W's bulk-insert
-- optimisations — variable network latency, Supabase rate limits,
-- and Render's request timeout combined to make any single-request
-- ingestion fragile at scale.
--
-- This table mirrors the Phase 34S ``classification_jobs`` design
-- (status enum, processed/total counters, chunked-pending list as
-- JSONB) but for ingestion. The wizard's upload step now:
--
--   1. POST /uploads/{id}/ingestion-jobs  — parses CSV up-front,
--                                            persists the parsed
--                                            product list as JSONB.
--   2. POST /ingestion-jobs/{jid}/advance — pops next 500 products,
--                                            batch-inserts into the
--                                            products table.
--   3. Polls until status is terminal.

CREATE TABLE ingestion_jobs (
    id                       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    organisation_id          UUID         NOT NULL REFERENCES organisations(id) ON DELETE CASCADE,
    project_id               UUID         NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    upload_id                UUID         NOT NULL REFERENCES uploads(id)       ON DELETE CASCADE,
    status                   TEXT         NOT NULL DEFAULT 'queued',
    total_rows               INTEGER      NOT NULL DEFAULT 0,
    processed_rows           INTEGER      NOT NULL DEFAULT 0,
    inserted_products        INTEGER      NOT NULL DEFAULT 0,
    errors_total             INTEGER      NOT NULL DEFAULT 0,
    warnings_total           INTEGER      NOT NULL DEFAULT 0,
    sample_errors            JSONB        NOT NULL DEFAULT '[]',
    -- pending_payload holds the up-front-parsed NormalizedProduct
    -- list (serialised) waiting to be inserted. Each advance call
    -- pops chunk_size entries from the head, batch-inserts them, and
    -- persists the trimmed payload. Inline JSONB is fine for 15K
    -- rows (~3-5 MB).
    pending_payload          JSONB        NOT NULL DEFAULT '[]',
    mapping                  JSONB        NOT NULL DEFAULT '{}',
    chunk_size               INTEGER      NOT NULL DEFAULT 500,
    next_row_offset          INTEGER      NOT NULL DEFAULT 0,
    error_code               TEXT,
    error_message            TEXT,
    created_by               UUID,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at               TIMESTAMPTZ,
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,

    CONSTRAINT ingestion_jobs_status_check CHECK (
        status IN (
            'queued',
            'running',
            'completed',
            'completed_with_errors',
            'failed',
            'cancelled'
        )
    ),
    CONSTRAINT ingestion_jobs_chunk_size_check CHECK (
        chunk_size BETWEEN 1 AND 2000
    )
);

CREATE INDEX idx_ingestion_jobs_project_id
    ON ingestion_jobs (project_id);

CREATE INDEX idx_ingestion_jobs_upload_id
    ON ingestion_jobs (upload_id);

CREATE INDEX idx_ingestion_jobs_status
    ON ingestion_jobs (status, created_at DESC);

ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY;

CREATE POLICY ingestion_jobs_org_isolation ON ingestion_jobs
    USING (organisation_id IN (SELECT public.visible_organisation_ids()));
