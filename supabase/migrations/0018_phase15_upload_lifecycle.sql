-- Phase 15: production upload pipeline
-- Adds full lifecycle statuses, file metadata, duplicate tracking,
-- and a JSONB validation-report column to the uploads table.

-- ── 1. Expand the status CHECK constraint ─────────────────────────────────
ALTER TABLE public.uploads
  DROP CONSTRAINT IF EXISTS uploads_status_check;

ALTER TABLE public.uploads
  ADD CONSTRAINT uploads_status_check CHECK (status IN (
    -- Phase 15 lifecycle
    'created',
    'upload_url_created',
    'uploaded_to_storage',
    'validation_pending',
    'validation_running',
    'validation_failed',
    'validation_completed',
    'ingestion_running',
    'ingestion_failed',
    'ingestion_completed',
    'ready_for_classification',
    -- Legacy values (kept for backward compatibility)
    'pending',
    'valid',
    'invalid'
  ));

-- ── 2. File metadata columns ───────────────────────────────────────────────
ALTER TABLE public.uploads
  ADD COLUMN IF NOT EXISTS content_type          TEXT,
  ADD COLUMN IF NOT EXISTS file_size_bytes       BIGINT     CHECK (file_size_bytes >= 0),
  ADD COLUMN IF NOT EXISTS checksum_sha256       TEXT       CHECK (length(checksum_sha256) = 64),
  ADD COLUMN IF NOT EXISTS duplicate_of_upload_id UUID      REFERENCES public.uploads (id),
  ADD COLUMN IF NOT EXISTS validation_started_at   TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS validation_completed_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ingestion_started_at    TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS ingestion_completed_at  TIMESTAMPTZ,
  -- JSONB snapshot of the ValidationReport (errors + warnings + totals)
  ADD COLUMN IF NOT EXISTS validation_report     JSONB;

-- ── 3. Duplicate-detection index ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_uploads_project_checksum
  ON public.uploads (project_id, checksum_sha256)
  WHERE checksum_sha256 IS NOT NULL;

-- ── 4. Update the row-count constraint to cover new terminal statuses ──────
ALTER TABLE public.uploads
  DROP CONSTRAINT IF EXISTS uploads_row_count_required_when_resolved;

ALTER TABLE public.uploads
  ADD CONSTRAINT uploads_row_count_required_when_resolved CHECK (
    status NOT IN (
      'validation_completed', 'ingestion_completed',
      'ready_for_classification', 'valid', 'invalid'
    )
    OR row_count IS NOT NULL
  );

COMMENT ON COLUMN public.uploads.checksum_sha256       IS 'Hex SHA-256 of the raw uploaded file; used for duplicate detection.';
COMMENT ON COLUMN public.uploads.duplicate_of_upload_id IS 'Set when this upload has the same checksum as an earlier upload in the same project.';
COMMENT ON COLUMN public.uploads.validation_report      IS 'Snapshot of the ValidationReport (total_rows, errors, warnings) from the ingestion pipeline.';
