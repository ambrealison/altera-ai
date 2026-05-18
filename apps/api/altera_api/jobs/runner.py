"""Worker backend abstraction.

``WorkerBackend`` is the protocol every runner satisfies.
``SyncDevRunner`` is the in-process implementation for dev/test.

To swap to async workers:
  - Celery: push job_id to a Celery task; worker calls execute_job().
  - RQ: push job_id to a Redis queue; worker calls execute_job().
  - Dramatiq: same pattern.

None of those require changes to routes or tasks — only the
``get_worker`` dependency binding changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from altera_api.domain.job import Job

if TYPE_CHECKING:
    from altera_api.persistence.protocol import StoreProtocol
    from altera_api.storage.protocol import StorageProtocol


@runtime_checkable
class WorkerBackend(Protocol):
    """Minimal contract every job runner must satisfy."""

    def dispatch(
        self,
        job: Job,
        store: StoreProtocol,
        storage: StorageProtocol | None = None,
    ) -> Job:
        """Enqueue or execute *job*, returning its final or intermediate state."""
        ...


class SyncDevRunner:
    """Executes jobs synchronously in the calling thread.

    The returned ``Job`` is always in a terminal state (succeeded/failed)
    because execution completes before ``dispatch`` returns. This makes
    HTTP responses self-consistent: the caller sees the outcome immediately.

    In production replace with a broker-backed runner:

        # Example: Celery replacement
        class CeleryRunner:
            def dispatch(self, job, store, storage=None) -> Job:
                execute_job_task.delay(str(job.job_id))  # Celery task
                return job  # status=queued, caller polls GET /jobs/{id}
    """

    def dispatch(
        self,
        job: Job,
        store: StoreProtocol,
        storage: StorageProtocol | None = None,
    ) -> Job:
        from altera_api.jobs.tasks import execute_job

        return execute_job(job, store, storage=storage)
