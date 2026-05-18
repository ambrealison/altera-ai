-- Phase 28A-3: extend audit_events action CHECK constraint
--
-- Adds missing action values from Phases 20–27 and new 28A-3 values:
--   scenario.run, comparison.requested, enrichment.applied
--
-- The constraint was last updated in 0019 (Phase 16 jobs). All values
-- added since then are included here so the constraint matches the
-- AuditEventType enum in domain/audit.py.

ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS audit_events_action_check;

ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_action_check CHECK (
        action IN (
            -- Organisation lifecycle
            'organisation.created',
            'organisation.member_invited',
            'organisation.role_changed',
            -- Project lifecycle
            'project.created',
            -- Upload lifecycle
            'upload.created',
            'upload.dropped_columns',
            -- Classification batch lifecycle
            'classification.batch_started',
            'classification.batch_finished',
            -- Run lifecycle
            'run.created',
            'run.succeeded',
            'run.failed',
            -- Export lifecycle (Phase 20)
            'export.generated',
            'export.submitted_for_review',
            'export.approved',
            'export.rejected',
            'export.delivered',
            'export.downloaded',
            -- Auth
            'auth.signed_in',
            -- PT validation lifecycle
            'pt_validation.submitted',
            'pt_validation.validated',
            -- Hardened-policy guards
            'commercial_data_block',
            -- Job lifecycle (Phase 16)
            'job.created',
            'job.started',
            'job.succeeded',
            'job.failed',
            'job.retrying',
            'job.cancelled',
            -- Review decisions (Phase 19C)
            'review.decision_made',
            'review.bulk_action',
            -- Recommendation lifecycle (Phase 25B)
            'recommendation.generated',
            'recommendation.proposed',
            'recommendation.accepted',
            'recommendation.dismissed',
            'recommendation.archived',
            -- Scenario lifecycle (Phase 26A)
            'scenario.run',
            -- Comparison lifecycle (Phase 27A)
            'comparison.requested',
            -- Nutrition enrichment lifecycle (Phase 23A)
            'enrichment.applied'
        )
    );
