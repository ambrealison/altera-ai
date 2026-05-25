"""Phase 34R — Classification job state.

A ``ClassificationJob`` tracks an asynchronous, chunked AI
classification run over an upload's products. The browser starts a
job, then polls/advances it; each advance call processes one batch
(default ~25 products, ~5–10s wall time on a real OpenAI call) and
persists progress to the store. The job survives across requests so
a slow OpenAI call or a flaky network never lands the user in
"Failed to fetch" hell again.

Design notes:

- Separate from the Phase 16 ``Job`` table on purpose. Phase 16's
  Job models pipeline-style audits (validate / ingest / calculate /
  export); ClassificationJob models the resumable state of a long-
  running AI orchestration. Stuffing batch-index / processed-count
  / sample-errors into ``Job.payload`` would make Job's schema
  ambiguous between two very different lifecycles.
- A ``ClassificationJob`` does NOT itself store classifications —
  those are written directly to the PT/WWF classification tables
  as each batch completes. The job record is *metadata* about
  progress. If the API restarts mid-job the persisted
  classifications are intact; the user just calls advance again to
  resume.
- The status state machine is intentionally minimal:
    queued → running → (completed | completed_with_errors | failed | cancelled)
  No retrying status — that's encoded by the caller starting a new
  advance call with retry_failed=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from altera_api.domain.common import Methodology


class ClassificationJobStatus(StrEnum):
    """Job lifecycle.

    Transitions:
        QUEUED → RUNNING after the first advance call processes a batch.
        RUNNING → COMPLETED when all eligible products are classified.
        RUNNING → COMPLETED_WITH_ERRORS when finished but some rows
                  remained as parse_failed / unknown.
        RUNNING → FAILED when an unrecoverable error occurred (no
                  products were processable, provider permanently down).
        Anywhere → CANCELLED when the caller explicitly cancels.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATUSES: frozenset[ClassificationJobStatus] = frozenset(
    {
        ClassificationJobStatus.COMPLETED,
        ClassificationJobStatus.COMPLETED_WITH_ERRORS,
        ClassificationJobStatus.FAILED,
        ClassificationJobStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class ClassificationJob:
    """Persistent record of an AI classification run.

    The dataclass is frozen — every state update creates a new
    instance via :py:meth:`with_progress`. The store keeps the latest
    instance under the job id.
    """

    id: UUID
    organisation_id: UUID
    project_id: UUID
    upload_id: UUID
    methodology: Methodology
    status: ClassificationJobStatus
    total_products: int
    processed_products: int = 0
    # The pending_product_ids list is the source of truth for "what's
    # left to do". Each advance call slices the head of this list,
    # classifies those products, and persists a new instance with the
    # head removed. When pending is empty the job is finished.
    pending_product_ids: tuple[UUID, ...] = ()
    # Phase 34Q coverage counters — refreshed each advance call by
    # walking the classification store for products in this upload.
    categorized_total: int = 0
    accepted_total: int = 0
    review_required_total: int = 0
    failed_total: int = 0
    unknown_total: int = 0
    out_of_scope_total: int = 0
    # Retry diagnostics aggregated across all advance calls so far.
    retry_batches: int = 0
    recovered_rows: int = 0
    # Configuration locked at job creation. ``overwrite`` rewrites
    # already-classified rows; ``only_missing_or_failed`` (default
    # True) skips Accepted classifications from previous runs so a
    # second advance call doesn't re-spend OpenAI quota.
    overwrite: bool = False
    only_missing_or_failed: bool = True
    batch_size: int = 25
    created_by: UUID | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    sample_errors: tuple[str, ...] = ()
    cancel_requested: bool = False
    # Free-form id list of products that failed in any batch — used
    # for the "retry failed" endpoint. Reset when overwrite=True or
    # when a fresh job is created.
    failed_product_ids: tuple[UUID, ...] = field(default_factory=tuple)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    @property
    def progress_pct(self) -> float:
        if self.total_products <= 0:
            return 100.0 if self.is_terminal else 0.0
        return round(
            100.0 * self.processed_products / self.total_products, 1
        )

    def with_progress(self, **changes: object) -> ClassificationJob:
        """Return a new instance with the given fields replaced.

        ``dataclasses.replace`` would also work but this wrapper makes
        the call sites read clearly and centralises the immutability
        contract in the domain layer.
        """
        from dataclasses import replace

        return replace(self, **changes)  # type: ignore[arg-type]
