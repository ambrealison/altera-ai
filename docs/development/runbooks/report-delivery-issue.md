# Runbook: Report delivery issue

**Severity**: P1 (client cannot see approved report) / P2 (delivery transition failed)
**Oncall trigger**: Methodology lead reports delivery button failed, or client cannot see a `delivered` report they were told to expect

---

## Background

The report lifecycle is: `draft → under_review → approved → delivered`. Only `altera_methodology_lead` or `altera_admin` can transition to `approved` or `delivered`. Clients see `approved` and `delivered` reports identically (both are downloadable); `delivered` additionally sets `delivered_at` on the export and emits an `export.delivered` audit event.

See `docs/outputs/report-structure.md` for the full lifecycle diagram.

## Symptoms

- `POST /api/v1/exports/{export_id}/deliver` returns 403 or 422.
- Export status shows `approved` in the DB but client still sees "being prepared".
- `delivered_at` is null on an export that should be delivered.
- Client reports downloading stale data (old export, not the latest run).

## Triage steps

### 1. Check the export record

```sql
SELECT id, run_id, approval_status, approved_by, approved_at,
       delivered_by, delivered_at, client_download_count, client_downloaded_at
FROM report_exports
WHERE run_id = '<run_id>'
ORDER BY created_at DESC;
```

Expected for a delivered export: `approval_status = delivered`, `delivered_at` not null.

### 2. Verify the actor's role

The deliver endpoint requires `can_deliver_report` which is `altera_methodology_lead` or `altera_admin`:

```sql
SELECT role FROM memberships
WHERE user_id = '<actor_user_id>'
  AND organisation_id = '<altera_org_id>';
```

If the actor is `altera_analyst` or a client role, the 403 is correct behaviour.

### 3. Check the audit trail

```sql
SELECT event_type, actor_user_id, created_at, metadata
FROM audit_events
WHERE run_id = '<run_id>'
  AND event_type LIKE 'export.%'
ORDER BY created_at;
```

The complete sequence for a delivered report:
1. `export.submitted_for_review`
2. `export.approved`
3. `export.delivered`
4. `export.downloaded` (one per client download)

A gap in this sequence identifies where the process broke.

### 4. Check current export status vs. expected

If `approval_status = approved` but client cannot access:

```sql
-- Does the client's auth context match the org?
SELECT organisation_id FROM report_exports WHERE id = '<export_id>';
-- vs the client's org from memberships
SELECT organisation_id FROM memberships WHERE user_id = '<client_user_id>';
```

### 5. Check if a newer export superseded this one

```sql
SELECT id, approval_status, created_at
FROM report_exports
WHERE run_id = '<run_id>'
ORDER BY created_at DESC;
```

If multiple exports exist for the same run, the API returns the most recent `approved`/`delivered` one.

## Resolution

| Scenario | Action |
|---|---|
| 403 on deliver | Check actor's role; escalate to `altera_admin` if needed |
| 422 on deliver | Export is not in `approved` state; approve first |
| Client sees `403` on download | Export is `under_review` or `rejected`; follow approval flow |
| Client downloads stale export | Check for multiple exports; delivered is most recent |
| `delivered_at` null after delivery attempt | Check audit events for `export.delivered`; retry delivery |

### Manually marking as delivered (emergency only)

Only as a last resort when the API endpoint is unavailable:

```sql
UPDATE report_exports
SET approval_status = 'delivered',
    delivered_by = '<actor_user_id>',
    delivered_at = now(),
    updated_at = now()
WHERE id = '<export_id>'
  AND approval_status = 'approved';

-- Also emit the audit event:
INSERT INTO audit_events (event_type, actor_user_id, organisation_id, run_id, metadata, created_at)
VALUES ('export.delivered', '<actor_user_id>', '<org_id>', '<run_id>',
        '{"manual": true, "reason": "API unavailable"}'::jsonb, now());
```

Document the manual action and file an incident report.

## Prevention

- The approval workflow is gated by role (`can_approve_report`, `can_deliver_report`) — not bypassable through the API.
- Audit events are append-only (enforced by migration `0012_audit_immutability.sql`).
- The report UI shows approval status prominently so clients are not surprised by `under_review` states.
