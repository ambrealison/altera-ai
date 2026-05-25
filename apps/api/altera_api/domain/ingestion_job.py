"""Phase 34X — Chunked, resumable CSV ingestion job.

The synchronous ``POST /uploads`` path was still failing on 1050-row
CSVs in production even after Phase 34W's bulk-insert optimisation:
variable network latency + Supabase rate limits + Render's HTTP
timeout combined to make any single-request ingestion fragile at
scale.

The new flow mirrors Phase 34R's classification jobs:

1. ``POST /uploads/{id}/ingestion-jobs`` parses the CSV up-front in
   pure Python (no DB hits) and stores the parsed
   :class:`NormalizedProduct` list inline on the job record. The
   parsing is fast (~100ms for 15K rows); only the DB inserts are
   slow at scale.
2. ``POST /ingestion-jobs/{jid}/advance`` pops the next chunk
   (default 500 rows) and batch-inserts them via
   ``add_products_bulk``. Each advance call runs in well under
   Render's timeout.
3. The frontend polls advance every ~1.5s until status is terminal.

Invariants:
- The pre-parsed list is the source of truth for "what's left".
- The products table is the source of truth for "what's been
  ingested". If the API restarts mid-advance, the next call picks
  up from the persisted ``pending_payload`` — at worst one chunk
  of work is repeated, and product upsert keys de-duplicate.
- One CSV upload = one ingestion job = one user-visible upload =
  one product set. We do NOT split a single CSV into multiple
  uploads on the user's side.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class IngestionJobStatus(StrEnum):
    """Job lifecycle. Identical state machine to ClassificationJob
    so the wizard's progress UI is symmetrical."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL: frozenset[IngestionJobStatus] = frozenset(
    {
        IngestionJobStatus.COMPLETED,
        IngestionJobStatus.COMPLETED_WITH_ERRORS,
        IngestionJobStatus.FAILED,
        IngestionJobStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class IngestionJob:
    """Persistent record of a chunked CSV ingestion.

    The dataclass is frozen — every state update creates a new
    instance via :py:meth:`with_progress`. ``pending_payload`` is a
    list of pre-parsed ``NormalizedProduct``-dict-shaped entries; the
    advance call pops the head and batch-inserts them.

    The whole entity is intentionally serialisable via the
    Phase 34T classification_job_to_row pattern (UUIDs → str,
    datetimes → ISO, JSONB columns as lists).
    """

    id: UUID
    organisation_id: UUID
    project_id: UUID
    upload_id: UUID
    status: IngestionJobStatus
    total_rows: int
    processed_rows: int = 0
    inserted_products: int = 0
    errors_total: int = 0
    warnings_total: int = 0
    sample_errors: tuple[str, ...] = ()
    # Each entry is the dict-encoded form of a parsed NormalizedProduct
    # (mappers.product_to_row output). Stored inline so advance can
    # be stateless w.r.t. external storage.
    pending_payload: tuple[dict[str, Any], ...] = ()
    mapping: dict[str, str] = field(default_factory=dict)
    # Phase 35-OOM — default dropped 500 → 250 to halve per-advance
    # peak memory. Each advance materialises chunk_size product dicts
    # in the orchestrator AND keeps them alive while the JSONB
    # ``pending_payload`` write goes back to Postgres. On Render's
    # 512 MB instance with two concurrent jobs the previous default
    # tipped the worker into OOM. Callers can still pass an explicit
    # value via the ``chunk_size`` form field.
    chunk_size: int = 250
    next_row_offset: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_by: UUID | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def progress_pct(self) -> float:
        if self.total_rows <= 0:
            return 100.0 if self.is_terminal else 0.0
        return round(100.0 * self.processed_rows / self.total_rows, 1)

    def with_progress(self, **changes: object) -> IngestionJob:
        return replace(self, **changes)  # type: ignore[arg-type]
