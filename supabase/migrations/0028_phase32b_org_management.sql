-- 0028_phase32b_org_management.sql
--
-- Phase 32B: extend audit_events action CHECK constraint to include
--   organisation.member_removed   (emitted when a membership is deleted)
--
-- The full constraint is rebuilt here so it remains self-documenting.

ALTER TABLE audit_events
    DROP CONSTRAINT IF EXISTS audit_events_action_check;

ALTER TABLE audit_events
    ADD CONSTRAINT audit_events_action_check CHECK (
        action IN (
            -- Organisation lifecycle
            'organisation.created',
            'organisation.member_invited',
            'organisation.role_changed',
            'organisation.member_removed',
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
