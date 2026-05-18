# Runbook: Job stuck in `running` or `queued`

**Severity**: P2
**Oncall trigger**: Job has been in `running` or `queued` for > 5 minutes

---

## Symptoms

- `GET /api/v1/jobs/{job_id}` returns `status: running` or `status: queued` for an unexpectedly long time.
- Client sees a spinner that never resolves.
- No corresponding `request.complete` log line for the job's HTTP request.

## Triage steps

### 1. Confirm the job is stuck

```sql
SELECT job_id, job_type, status, created_at, updated_at,
       EXTRACT(EPOCH FROM (now() - updated_at)) AS seconds_since_update
FROM jobs
WHERE status IN ('running', 'queued')
ORDER BY created_at;
```

A job stuck for > 300 seconds without a log entry is likely orphaned.

### 2. Check API process health

```sh
# If using systemd:
systemctl status altera-api

# If using Docker:
docker ps | grep altera-api
docker logs altera-api --tail 100
```

Look for OOM kills, unexpected restarts, or uncaught exceptions.

### 3. Check structured logs for the job

```
level=ERROR job_id=<job_id>
level=INFO  job_id=<job_id> msg=job.started
```

If `job.started` is present but `job.completed` / `job.failed` is absent, the worker crashed mid-execution.

### 4. Check the current backend

The pilot uses `SyncDevRunner` (synchronous in the HTTP request thread). A stuck job means the uvicorn worker thread is blocked — typically on:

- An unresponsive external API call (OpenAI, Supabase Storage).
- A database query without a timeout.
- An infinite loop or deadlock.

Check uvicorn thread count / active connections:

```sh
# If uvicorn exposes metrics, check active workers.
# Otherwise look at process state:
ps aux | grep uvicorn
```

### 5. If using an async queue (post-pilot)

Check broker connectivity and worker health. Celery:

```sh
celery -A altera_api.tasks inspect active
celery -A altera_api.tasks inspect reserved
```

## Resolution

| Scenario | Action |
|---|---|
| Worker thread blocked on external call | Restart the API process; add timeout to the external call |
| Orphaned job (process restarted) | Mark job `failed` manually (see below); client should re-trigger |
| Database deadlock | Check `pg_stat_activity`; terminate blocking query if safe |
| OOM kill | Scale up memory or reduce batch size |

### Manually marking an orphaned job as failed

```sql
UPDATE jobs
SET status = 'failed',
    error_message = 'Job orphaned — API process restarted during execution.',
    updated_at = now()
WHERE job_id = '<job_id>'
  AND status IN ('running', 'queued');
```

After this update the client can re-trigger the operation.

## Prevention

- Add per-request timeouts to OpenAI calls (already present in Phase 20 AI module).
- Add `statement_timeout` to Postgres connections.
- A future `CronCreate`-based watchdog can auto-fail jobs stuck > N minutes.
