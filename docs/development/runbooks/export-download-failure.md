# Runbook: Export / report download failure

**Severity**: P2 (client cannot access approved report)
**Oncall trigger**: Client reports 403 or 404 when downloading a report, or download link is broken

---

## Symptoms

- `GET /api/v1/projects/{id}/runs/{run_id}/report` returns 403 or 404.
- `GET /api/v1/projects/{id}/exports` returns an empty list when the client expects approved exports.
- Signed download URL is expired or returns 403 from Supabase Storage.

## Access-control gate

Per `docs/outputs/report-structure.md`:

| User type | Condition | Result |
|---|---|---|
| Altera internal | Any run state | 200 |
| Client | Export is `approved` or `delivered` | 200 |
| Client | No approved/delivered export | 403 |

The `list_exports` endpoint only surfaces `approved`/`delivered` records to clients. A 403 most often means the export has not been approved yet.

## Triage steps

### 1. Check export status

```sql
SELECT id, run_id, approval_status, approved_by, approved_at,
       delivered_by, delivered_at, created_at
FROM report_exports
WHERE run_id = '<run_id>'
ORDER BY created_at DESC;
```

| `approval_status` | Client symptom | Action |
|---|---|---|
| `draft` | 403 | Export not yet submitted for review — submit it |
| `under_review` | 403 | Awaiting approval — escalate to methodology lead |
| `rejected` | 403 | Methodology lead must fix and re-submit |
| `approved` | Should work | Check storage (step 2) |
| `delivered` | Should work | Check storage (step 2) |

### 2. Check Supabase Storage

Verify the export file exists:

```sh
# Supabase dashboard → Storage → exports bucket
# Path: organisations/<org_id>/exports/<export_id>.json
supabase storage ls organisations/<org_id>/exports/
```

If the file is missing, the export record exists but the file was not written. Regenerate:

```sh
# Altera internal user: POST to regenerate export
POST /api/v1/projects/<project_id>/runs/<run_id>/exports
```

### 3. Check signed URL expiry

Signed URLs expire after 1 hour by default. If the client bookmarked the URL, it will no longer work. The client should click "Download" fresh from the UI (which generates a new signed URL).

### 4. Check audit events

```sql
SELECT event_type, actor_user_id, created_at, metadata
FROM audit_events
WHERE run_id = '<run_id>'
  AND event_type LIKE 'export.%'
ORDER BY created_at;
```

Expected sequence: `export.submitted_for_review` → `export.approved` → optionally `export.delivered` → `export.downloaded`.

## Resolution

| Scenario | Action |
|---|---|
| Status is `draft`/`under_review`/`rejected` | Follow approval workflow; cannot shortcut for client |
| File missing in Storage | Regenerate export via API (Altera internal) |
| Signed URL expired | Client re-downloads from UI |
| RLS permission denied | See [rls-permission-denied.md](rls-permission-denied.md) |
| `client_download_count` incrementing but file not received | Check client network / browser; share direct signed URL |

## Prevention

- The download gate is enforced at the API layer and in Supabase Storage RLS.
- `client_downloaded_at` and `client_download_count` are updated on each download for audit purposes.
