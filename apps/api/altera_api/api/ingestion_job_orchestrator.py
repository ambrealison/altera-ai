"""Phase 34X — chunked, resumable CSV ingestion job orchestrator.

The synchronous ``POST /uploads`` flow could not be made reliable for
1050-row CSVs in production: Render's request timeout, Supabase rate
limits, and the cumulative cost of validation + bulk insert + project
refresh all combined to push the user past 30-second deadlines and
trigger a worker restart (which the frontend interpreted as logout).

The new pipeline:

  1. ``POST /uploads``                    — unchanged for small files
                                             (<= 500 rows). For
                                             larger CSVs the wizard
                                             now calls the prepare
                                             + ingestion-job flow.
  2. ``POST /uploads/{uid}/ingestion-jobs`` — parses the CSV up-front
                                             in pure Python (fast,
                                             no DB hits), stores the
                                             parsed product dicts on
                                             the job row, and returns
                                             immediately with the
                                             job id + total_rows.
  3. ``POST /ingestion-jobs/{jid}/advance`` — pops ``chunk_size``
                                             entries from the
                                             pending_payload list,
                                             batch-inserts them via
                                             ``add_products_bulk``,
                                             persists progress.
  4. ``GET /ingestion-jobs/{jid}``         — pure status read.

Invariants:
- One CSV = one upload record = one ingestion job = one product set.
  We never split a user's file into multiple uploads.
- ``pending_payload`` is the source of truth for "what's left".
  Advance is idempotent in the sense that products inserted but not
  yet trimmed from pending_payload would be re-inserted on retry —
  the products table's PK guards against true duplicates via upsert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from altera_api.domain.ingestion_job import (
    IngestionJob,
    IngestionJobStatus,
)
from altera_api.persistence.mappers import product_from_row

if TYPE_CHECKING:
    from altera_api.domain.common import Methodology
    from altera_api.domain.project import Project
    from altera_api.persistence.protocol import StoreProtocol


# Maximum chunk_size we accept. Keeps each advance call well under
# Render's HTTP timeout (each bulk-insert of 1000 rows is ~1s on
# warm Supabase, ~3-4s on cold).
MAX_CHUNK_SIZE = 1000


def create_ingestion_job(
    store: StoreProtocol,
    *,
    organisation_id: UUID,
    project: Project,
    upload_id: UUID,
    parsed_products: list[Any],
    mapping: dict[str, str] | None = None,
    chunk_size: int = 250,
    created_by: UUID | None = None,
    initial_errors_total: int = 0,
    initial_warnings_total: int = 0,
    initial_sample_errors: tuple[str, ...] = (),
) -> IngestionJob:
    """Create a queued ingestion job from a pre-parsed product list.

    The caller is responsible for parsing/validation (which is fast,
    CPU-only). This entry point only persists the resulting job + its
    pending payload. No DB inserts to ``products`` happen here; those
    are deferred to ``advance_ingestion_job``.

    ``parsed_products`` is expected to be a list of
    :class:`NormalizedProduct` instances. We serialise each via the
    existing ``product_to_row`` mapper so advance can deserialise
    cleanly.
    """
    from altera_api.persistence.mappers import product_to_row

    if chunk_size <= 0 or chunk_size > MAX_CHUNK_SIZE:
        chunk_size = min(max(chunk_size, 1), MAX_CHUNK_SIZE)

    pending = tuple(product_to_row(p) for p in parsed_products)
    now = datetime.now(UTC)
    job = IngestionJob(
        id=uuid4(),
        organisation_id=organisation_id,
        project_id=project.id,
        upload_id=upload_id,
        status=IngestionJobStatus.QUEUED,
        total_rows=len(pending),
        processed_rows=0,
        inserted_products=0,
        errors_total=initial_errors_total,
        warnings_total=initial_warnings_total,
        sample_errors=initial_sample_errors[:20],
        pending_payload=pending,
        mapping=dict(mapping or {}),
        chunk_size=chunk_size,
        next_row_offset=0,
        error_code=None,
        error_message=None,
        created_by=created_by,
        created_at=now,
        started_at=None,
        updated_at=now,
        completed_at=None,
    )
    store.add_ingestion_job(job)
    return job


def advance_ingestion_job(
    store: StoreProtocol,
    job_id: UUID,
    *,
    project: Project,
    methodologies_enabled: frozenset[Methodology] | None = None,
) -> IngestionJob:
    """Process the next chunk of pending products.

    Returns the updated job. If the job is already terminal or has no
    pending payload, returns it unchanged.

    The advance call:
    1. Slices ``chunk_size`` rows from the head of pending_payload.
    2. Deserialises them via ``product_from_row`` (respecting the
       project's enabled methodologies so PT/WWF fields land in the
       right buckets).
    3. Batch-inserts via ``store.add_products_bulk``.
    4. Persists the trimmed pending_payload + bumped counters.

    The function NEVER raises an unhandled exception out to the route
    layer — any error is captured into the job's ``error_code`` /
    ``error_message`` / ``sample_errors`` fields and the job is moved
    to a terminal status if the failure is unrecoverable. (A single
    chunk failing is recoverable; the caller can retry the advance.)
    """
    job = store.get_ingestion_job(job_id)
    if job is None:
        raise LookupError(f"ingestion job {job_id} not found")
    if job.is_terminal:
        return job
    if not job.pending_payload:
        # Nothing to do — finalise.
        coverage_status = (
            IngestionJobStatus.COMPLETED_WITH_ERRORS
            if job.errors_total > 0
            else IngestionJobStatus.COMPLETED
        )
        finished = job.with_progress(
            status=coverage_status, completed_at=datetime.now(UTC)
        )
        store.update_ingestion_job(finished)
        return finished

    now = datetime.now(UTC)
    take = max(1, min(job.chunk_size, MAX_CHUNK_SIZE))
    chunk_rows = list(job.pending_payload[:take])
    remaining = tuple(job.pending_payload[take:])

    # Mark running on the very first advance.
    if job.status is IngestionJobStatus.QUEUED:
        store.update_ingestion_job(
            job.with_progress(
                status=IngestionJobStatus.RUNNING,
                started_at=job.started_at or now,
            )
        )

    try:
        methodologies = (
            methodologies_enabled
            if methodologies_enabled is not None
            else project.methodologies_enabled
        )
        products = [
            product_from_row(row, methodologies_enabled=methodologies)
            for row in chunk_rows
        ]
        store.add_products_bulk(products)
        inserted = len(products)
    except Exception as exc:  # noqa: BLE001
        # Hotfix-Upload — one chunk failed. Previously the orchestrator
        # set sample_errors + bumped errors_total but did NOT trim
        # ``pending_payload``. On each retry the same failing chunk was
        # processed again, so a 100-row file with a missing required
        # field surfaced as "0 produits insérés / 7800 erreur(s)" (78
        # advance retries × 100 rows). We now move past the failed
        # chunk so a single permanent error counts each row once and
        # the job reaches a terminal status promptly.
        sample = (*job.sample_errors, f"chunk_error: {type(exc).__name__}: {exc}")[
            -20:
        ]
        # If the very first advance fails — i.e. nothing has been
        # inserted yet AND the failure happened on the head of the
        # pending payload — surface it as a terminal FAILED job with
        # a clear error_code. This stops the wizard from polling
        # forever on a structurally broken upload (e.g. required
        # field unmapped).
        is_first_chunk_total_failure = (
            job.processed_rows == 0
            and job.inserted_products == 0
            and not remaining
        )
        new_status = (
            IngestionJobStatus.FAILED
            if is_first_chunk_total_failure
            else IngestionJobStatus.RUNNING
        )
        updated = job.with_progress(
            status=new_status,
            pending_payload=remaining,  # trim, don't retry same chunk
            processed_rows=job.processed_rows + len(chunk_rows),
            next_row_offset=job.next_row_offset + len(chunk_rows),
            sample_errors=sample,
            errors_total=job.errors_total + len(chunk_rows),
            error_code=(
                "chunk_validation_failed"
                if new_status is IngestionJobStatus.FAILED
                else job.error_code
            ),
            error_message=(
                f"{type(exc).__name__}: {exc}"[:240]
                if new_status is IngestionJobStatus.FAILED
                else job.error_message
            ),
            completed_at=(
                now if new_status is IngestionJobStatus.FAILED else None
            ),
        )
        store.update_ingestion_job(updated)
        return updated

    # Trim pending, bump counters, decide terminal.
    is_done = not remaining
    new_status = (
        (
            IngestionJobStatus.COMPLETED_WITH_ERRORS
            if job.errors_total > 0
            else IngestionJobStatus.COMPLETED
        )
        if is_done
        else IngestionJobStatus.RUNNING
    )
    updated = job.with_progress(
        status=new_status,
        pending_payload=remaining,
        processed_rows=job.processed_rows + len(chunk_rows),
        inserted_products=job.inserted_products + inserted,
        next_row_offset=job.next_row_offset + len(chunk_rows),
        started_at=job.started_at or now,
        completed_at=now if is_done else None,
    )
    store.update_ingestion_job(updated)
    return updated
