"""Background job system.

``SyncDevRunner`` — executes jobs synchronously in the calling thread.
Drop-in replacement path: swap for ``CeleryRunner``, ``RQRunner``, or
``DramatiqRunner`` without touching routes or tasks.
"""
from altera_api.jobs.runner import SyncDevRunner, WorkerBackend

__all__ = ["SyncDevRunner", "WorkerBackend"]
