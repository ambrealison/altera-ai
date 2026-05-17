"""FastAPI dependency that provides the active WorkerBackend.

Bind a different backend in tests or future production config:

    app.dependency_overrides[get_worker] = lambda: CeleryRunner()
"""
from __future__ import annotations

from altera_api.jobs.runner import SyncDevRunner, WorkerBackend

_dev_runner = SyncDevRunner()


def get_worker() -> WorkerBackend:
    """Return the configured worker backend (default: SyncDevRunner)."""
    return _dev_runner
