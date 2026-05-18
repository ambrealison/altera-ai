# Runbook: Upload failure

**Severity**: P2 (data loss risk) / P3 (partial failure)
**Oncall trigger**: Client reports upload returning an error, or job status stays `failed`

---

## Symptoms

- `POST /api/v1/projects/{id}/uploads` returns 4xx or 5xx.
- Job status endpoint returns `status: failed`.
- Client sees "Upload failed" in the UI with no row count.

## Triage steps

### 1. Identify the failed job

```sh
# In Supabase or Postgres:
SELECT job_id, status, error_message, created_at, updated_at
FROM jobs
WHERE organisation_id = '<org_id>'
ORDER BY created_at DESC
LIMIT 10;
```

### 2. Check structured logs

Filter by `org_id` and `request_id` (from the `X-Request-ID` response header):

```
level=ERROR org_id=<org_id> request_id=<request_id>
```

Common error patterns:

| Log message | Cause | Fix |
|---|---|---|
| `upload.parse_error` | CSV encoding or delimiter wrong | Ask client to re-export as UTF-8 CSV |
| `upload.row_limit_exceeded` | File has > 50,000 rows (Phase 18 limit) | Ask client to split the file |
| `upload.storage_error` | Supabase Storage unreachable | Check Supabase status; retry |
| `classification.ai_timeout` | OpenAI API timed out | See [ai-classification-failure.md](ai-classification-failure.md) |

### 3. Inspect the upload record

```sql
SELECT id, filename, row_count, valid_row_count, invalid_row_count,
       status, error_summary, created_at
FROM uploads
WHERE organisation_id = '<org_id>'
ORDER BY created_at DESC
LIMIT 5;
```

### 4. Check storage

Verify the file landed in Supabase Storage under `organisations/<org_id>/uploads/`:

```sh
# Via Supabase dashboard Storage tab or:
supabase storage ls organisations/<org_id>/uploads/
```

## Resolution

| Scenario | Action |
|---|---|
| Parse error | Guide client to fix the file; re-upload |
| Row limit | Split file; re-upload each part |
| Storage failure | Wait for Supabase recovery; retry upload |
| AI classification failure | See [ai-classification-failure.md](ai-classification-failure.md) |
| Unknown | Escalate to engineering with `job_id` and `request_id` |

## Prevention

- File-level validation (row limit, encoding) runs synchronously before the job is created, so most bad-file errors surface as 422 before a job is ever written.
- AI batching uses exponential backoff; transient AI failures retry automatically up to 3×.
