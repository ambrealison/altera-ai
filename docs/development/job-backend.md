# Job backend and SLA

This document covers the current job execution model, the `WorkerBackend` protocol, SLAs for the pilot phase, and the recommended path to a production-grade async queue.

## Current implementation (pilot)

Long-running operations (CSV upload + classify, calculation runs, WWF ingredient uploads) are executed as **jobs** tracked in the `jobs` table. The `WorkerBackend` protocol in `apps/api/altera_api/jobs/runner.py` abstracts the execution mechanism.

The current default is `SyncDevRunner`, which runs jobs **synchronously in the calling HTTP request thread**. This means:

- The HTTP response is not returned until the job finishes.
- No broker or worker process is required to run locally or in staging.
- Memory is bounded by the single uvicorn process.

### Why synchronous for pilot

The pilot load is low (a few hundred products per upload, one or two organisations). Synchronous execution keeps the infrastructure footprint small and eliminates the operational surface of a broker, worker pool, and result backend. The `WorkerBackend` protocol ensures routes never call `SyncDevRunner` directly — swapping backends requires changing only the dependency binding in `apps/api/altera_api/api/dependencies.py`.

## SLAs (pilot phase)

| Operation | P50 target | P95 target | Approach if exceeded |
|---|---|---|---|
| CSV upload + classify (< 500 rows) | < 5 s | < 15 s | Optimise AI batching |
| CSV upload + classify (500 – 5,000 rows) | < 30 s | < 90 s | Move to async queue |
| Calculation run | < 3 s | < 10 s | Profile query plan |
| WWF ingredient upload | < 5 s | < 15 s | Optimise bulk insert |
| Report export (generate PDF) | — | — | Phase 31 (not yet implemented) |

These are initial estimates. Baseline them against real pilot data and revise within the first two sprints.

## Swapping to an async queue

When synchronous execution becomes a bottleneck — either because of latency SLA breaches or because long jobs block uvicorn worker threads — replace `SyncDevRunner` with a broker-backed runner:

### Option A: Celery + Redis (recommended)

Celery has the broadest operational tooling (Flower, beat scheduler, result backends) and is well-understood.

1. Add `celery[redis]` to `pyproject.toml`.
2. Implement `CeleryRunner`:

   ```python
   class CeleryRunner:
       def dispatch(self, job: Job, store, storage=None) -> Job:
           execute_job_task.delay(str(job.job_id))
           return dataclasses.replace(job, status=JobStatus.QUEUED)
   ```

3. Implement `execute_job_task` as a Celery task that loads the job by ID and calls `execute_job(job, store, storage)`.
4. Change the `get_worker` dependency binding to return `CeleryRunner`.
5. Routes and job logic are unchanged.

### Option B: RQ (Redis Queue)

Simpler than Celery; good fit if beat scheduling is not needed.

```python
class RQRunner:
    def __init__(self, queue: rq.Queue) -> None:
        self._q = queue

    def dispatch(self, job: Job, store, storage=None) -> Job:
        self._q.enqueue(execute_job, str(job.job_id))
        return dataclasses.replace(job, status=JobStatus.QUEUED)
```

### Option C: Dramatiq

Strongly-typed messages; better for teams that want type safety in task signatures.

## Polling model

Routes that trigger a job return immediately with `{"job_id": "..."}`. The frontend polls `GET /api/v1/jobs/{job_id}` until `status` transitions to `succeeded` or `failed`. This polling contract is already implemented and works identically across `SyncDevRunner` (where the job is already terminal on first poll) and any async runner.

## Operational considerations

- **Worker health**: Add a `/health` endpoint to the worker that checks broker connectivity.
- **Dead-letter queue**: Configure a DLQ for jobs that exhaust retries; alert on non-empty DLQ.
- **Retry policy**: Idempotent jobs (classify, calculate) can be retried up to 3×. Non-idempotent jobs (upload that mutates stored data) should not retry automatically.
- **Concurrency**: Limit concurrent jobs per organisation to prevent one tenant from starving others (Celery soft rate limits or a semaphore in the task).
- **Observability**: The structured log context (`request_id`, `org_id`, `user_id`) should be propagated into worker log lines by serialising the context into the job payload.
