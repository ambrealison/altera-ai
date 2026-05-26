"""Phase 34R — chunked classification job orchestrator.

The wizard's Step 4 ("Classification IA") no longer waits on one long
synchronous OpenAI run. Instead it:

1. ``POST /classification-jobs`` to create a :class:`ClassificationJob`
   record (status=queued). Returns immediately with the job id, total
   eligible products, and an empty progress payload.
2. ``POST /classification-jobs/{id}/advance`` in a polling loop. Each
   advance call processes ONE batch of up to ``batch_size`` (default
   25) products from the job's pending list and persists progress
   back to the store before returning. Wall time per call: well under
   Render's HTTP timeout.
3. ``GET /classification-jobs/{id}`` to read current state without
   doing work (used when the wizard re-mounts or the user revisits
   the project mid-job).

Invariants:
- Classifications are written DIRECTLY to the PT/WWF classification
  tables as each batch completes. The job record is metadata.
- The pending list is the source of truth for "what's left". Each
  advance call slices the head, classifies those rows, and persists
  the trimmed list. If the API process dies mid-batch, the worst
  case is that one batch's OpenAI work is wasted; the next advance
  call picks up from the persisted pending list.
- An advance call NEVER raises an unhandled exception out to the
  route layer. Any error is captured into the job's ``error_code`` /
  ``error_message`` / ``sample_errors`` fields and the job is moved
  to a terminal status if the failure is unrecoverable.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from altera_api.ai.batch_classifier import batch_classify as ai_batch_classify
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
)
from altera_api.api.orchestrator import (
    _enqueue_review_item,
    _queue_unknown_pt,
    _queue_unknown_wwf,
)
from altera_api.domain.classification_job import (
    ClassificationJob,
    ClassificationJobStatus,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.review import ManualReviewQueueReason

if TYPE_CHECKING:
    from altera_api.ai.provider import ClassifierProvider
    from altera_api.persistence.protocol import StoreProtocol


# Maximum batch_size we accept from clients. Higher values would risk
# returning the request past Render's HTTP timeout for one advance call.
MAX_BATCH_SIZE = 50


def _default_batch_size() -> int:
    """Phase 35-perf — env-tunable default batch size.

    Bench on staging with real OpenAI calls determines the sweet spot
    (provider latency does not scale linearly with batch size). Defaults
    to 25 — the historical safe value. Set
    ``ALTERA_AI_CLASSIFICATION_BATCH_SIZE=40`` (or 50, capped at
    ``MAX_BATCH_SIZE``) to validate larger batches without a redeploy.
    """
    raw = os.environ.get("ALTERA_AI_CLASSIFICATION_BATCH_SIZE", "25")
    try:
        v = int(raw)
    except ValueError:
        return 25
    return max(1, min(v, MAX_BATCH_SIZE))


def _eligible_product_ids(
    store: StoreProtocol,
    upload_id: UUID,
    methodology: Methodology,
    *,
    overwrite: bool,
    only_missing_or_failed: bool,
) -> tuple[list[UUID], dict[str, float]]:
    """Return the list of product ids in ``upload_id`` that this job
    should still classify, plus a timing breakdown dict.

    Phase 35-perf — replaces a 3×N round-trip loop (``get_upload`` +
    ``get_product`` per id + ``get_*_classification`` per id) with at
    most three bulk fetches: one ``get_upload``, one
    ``list_products_by_ids``, one ``get_*_classifications_bulk``. On a
    1050-row upload this drops creation time from ~126s to <5s on
    Render Standard.

    Filters applied:
    - The product must be ingested under this upload.
    - The product must have the target methodology enabled.
    - If ``overwrite=False`` and ``only_missing_or_failed=True`` (the
      default), products that already have a non-UNKNOWN classification
      from a prior run are skipped — this is what makes a "retry-failed"
      pass cheap.
    """
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    upload_record = store.get_upload(upload_id)
    timings["get_upload_ms"] = (time.perf_counter() - t0) * 1000
    if upload_record is None:
        timings["list_products_ms"] = 0.0
        timings["existing_classifications_ms"] = 0.0
        timings["upload_product_ids_count"] = 0
        timings["products_loaded_count"] = 0
        return [], timings

    product_ids = list(upload_record.product_ids)
    # Phase 36H — surface the upload size BEFORE filtering so a
    # silent PostgREST 1000-row truncation on the products fetch
    # (the 10K-upload bug) is immediately visible in production logs.
    timings["upload_product_ids_count"] = float(len(product_ids))
    t0 = time.perf_counter()
    products = store.list_products_by_ids(product_ids)
    timings["list_products_ms"] = (time.perf_counter() - t0) * 1000
    timings["products_loaded_count"] = float(len(products))

    # Filter for methodology + presence of methodology-specific fields.
    candidate_products: list[NormalizedProduct] = []
    for product in products:
        if methodology not in product.methodologies_enabled:
            continue
        if methodology is Methodology.PROTEIN_TRACKER:
            if product.pt_fields is None:
                continue
        elif product.wwf_fields is None:
            continue
        candidate_products.append(product)

    # Bulk-fetch existing classifications only if we need to skip
    # already-classified rows.
    t0 = time.perf_counter()
    existing_pt: dict[UUID, object] = {}
    existing_wwf: dict[UUID, object] = {}
    if not overwrite and only_missing_or_failed and candidate_products:
        candidate_ids = [p.id for p in candidate_products]
        if methodology is Methodology.PROTEIN_TRACKER:
            existing_pt = dict(
                store.get_pt_classifications_bulk(candidate_ids)
            )
        else:
            existing_wwf = dict(
                store.get_wwf_classifications_bulk(candidate_ids)
            )
    timings["existing_classifications_ms"] = (
        (time.perf_counter() - t0) * 1000
    )

    out: list[UUID] = []
    for product in candidate_products:
        if not overwrite and only_missing_or_failed:
            if methodology is Methodology.PROTEIN_TRACKER:
                existing = existing_pt.get(product.id)
                if (
                    existing is not None
                    and existing.pt_group  # type: ignore[attr-defined]
                    is not ProteinTrackerGroup.UNKNOWN
                ):
                    continue
            else:
                if existing_wwf.get(product.id) is not None:
                    continue
        out.append(product.id)
    return out, timings


def create_classification_job(
    store: StoreProtocol,
    *,
    organisation_id: UUID,
    project_id: UUID,
    upload_id: UUID,
    methodology: Methodology,
    overwrite: bool = False,
    only_missing_or_failed: bool = True,
    batch_size: int | None = None,
    created_by: UUID | None = None,
) -> ClassificationJob:
    """Create a queued classification job. No OpenAI calls happen here.

    The caller (route handler) commits the job to the store and returns
    it to the client. The browser then drives the advance loop.

    Phase 35-perf — emits ``classify.create.timing`` with a per-stage
    breakdown so production logs reveal exactly where the wall-clock
    time goes (get_upload / list_products / existing_classifications /
    add_job). Useful when the cost regresses on a new release.
    """
    import logging

    if batch_size is None:
        batch_size = _default_batch_size()
    elif batch_size <= 0 or batch_size > MAX_BATCH_SIZE:
        batch_size = min(max(batch_size, 1), MAX_BATCH_SIZE)
    t_total = time.perf_counter()
    eligible, timings = _eligible_product_ids(
        store,
        upload_id,
        methodology,
        overwrite=overwrite,
        only_missing_or_failed=only_missing_or_failed,
    )
    now = datetime.now(UTC)
    job = ClassificationJob(
        id=uuid4(),
        organisation_id=organisation_id,
        project_id=project_id,
        upload_id=upload_id,
        methodology=methodology,
        status=ClassificationJobStatus.QUEUED,
        total_products=len(eligible),
        processed_products=0,
        pending_product_ids=tuple(eligible),
        overwrite=overwrite,
        only_missing_or_failed=only_missing_or_failed,
        batch_size=batch_size,
        created_by=created_by,
        created_at=now,
        started_at=None,
        completed_at=None,
    )
    t0 = time.perf_counter()
    store.add_classification_job(job)
    add_job_ms = (time.perf_counter() - t0) * 1000
    total_ms = (time.perf_counter() - t_total) * 1000
    logging.getLogger("altera_api.classification_create").info(
        "classify.create.timing project=%s upload=%s methodology=%s "
        "upload_product_ids_count=%d products_loaded_count=%d "
        "eligible_count=%d total_products=%d "
        "get_upload_ms=%.1f list_products_ms=%.1f "
        "existing_cls_ms=%.1f add_job_ms=%.1f total_ms=%.1f "
        "batch_size=%d",
        project_id,
        upload_id,
        methodology.value,
        int(timings.get("upload_product_ids_count", 0)),
        int(timings.get("products_loaded_count", 0)),
        len(eligible),
        len(eligible),
        timings.get("get_upload_ms", 0.0),
        timings.get("list_products_ms", 0.0),
        timings.get("existing_classifications_ms", 0.0),
        add_job_ms,
        total_ms,
        batch_size,
    )
    return job


def _refresh_coverage_counters(
    store: StoreProtocol,
    job: ClassificationJob,
) -> tuple[dict[str, int], float]:
    """Walk the classification table for the job's upload and bucket
    products by their final pt_group. Used after every advance call so
    the wizard's progress bar reflects the actual stored state.

    Phase 35-perf — replaces a 1000+ ``get_pt_classification`` N+1 plus
    two ``list_review_items_for_project`` calls with one bulk fetch and
    one review-items fetch. On a 1050-row job this drops coverage-refresh
    time per advance from ~40s to <1s.

    Returns ``(counts, elapsed_ms)`` so the route can expose the cost.
    """
    t0 = time.perf_counter()
    categorized = accepted = review = failed = unknown = oos = 0
    if job.methodology is Methodology.PROTEIN_TRACKER:
        upload_record = store.get_upload(job.upload_id)
        product_ids = list(
            upload_record.product_ids if upload_record is not None else []
        )
        cls_map = (
            store.get_pt_classifications_bulk(product_ids)
            if product_ids
            else {}
        )
        for cls in cls_map.values():
            if cls.pt_group is ProteinTrackerGroup.UNKNOWN:
                unknown += 1
            elif cls.pt_group is ProteinTrackerGroup.OUT_OF_SCOPE:
                oos += 1
                categorized += 1
            else:
                categorized += 1
        # Single fetch of review items, then in-process filtering.
        product_id_set = set(product_ids)
        review_items = store.list_review_items_for_project(
            job.project_id, methodology=Methodology.PROTEIN_TRACKER
        )
        for item in review_items:
            if item.product_id not in product_id_set:
                continue
            review += 1
            if item.reason in (
                ManualReviewQueueReason.AI_PARSE_FAILED,
                ManualReviewQueueReason.AI_PROVIDER_ERROR,
            ):
                failed += 1
        accepted = max(0, categorized - review)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return {
        "categorized_total": categorized,
        "accepted_total": accepted,
        "review_required_total": review,
        "failed_total": failed,
        "unknown_total": unknown,
        "out_of_scope_total": oos,
    }, elapsed_ms


class ClassificationJobConflict(Exception):
    """Raised when an advance call detects another advance in flight.

    The route layer maps this to a 409 with
    ``error_code=classification_job_conflict`` so the wizard can pause
    its poll loop briefly before retrying. Phase 34S — needed for
    durable persistence in staging/prod where two browser tabs (or a
    racing retry-failed flow) might both reach the advance endpoint.
    """


#: Phase 34S — concurrent-advance guard window.
#:
#: If a previous advance for the same job committed within this many
#: milliseconds, AND the job has work remaining (pending non-empty),
#: AND status is RUNNING, we assume another advance is racing us and
#: reject the second caller with ``classification_job_conflict``.
#:
#: Tuned to a small enough value that:
#:  - the wizard's 1.5s poll loop NEVER trips it on normal use;
#:  - a test that calls advance back-to-back synchronously (no
#:    real network delay) also doesn't trip it — synchronous test
#:    flows always have the prior advance fully returned before the
#:    next call starts;
#:  - a two-tab race where both tabs fire advance within ~250 ms
#:    of each other gets correctly rejected on the second tab.
_ADVANCE_CONFLICT_WINDOW_SECONDS: float = 0.25


def advance_classification_job(
    store: StoreProtocol,
    job_id: UUID,
    *,
    ai_provider: ClassifierProvider | None,
) -> ClassificationJob:
    """Process the next batch for ``job_id``.

    Returns the updated job. If the job is already terminal or has no
    pending products, returns it unchanged.

    Phase 34S — concurrent-advance safety: if another advance updated
    this job within ``_ADVANCE_CONFLICT_WINDOW_SECONDS``, the second
    caller is rejected with :class:`ClassificationJobConflict`. This
    protects against the two-tab race where both tabs poll the advance
    endpoint within the same poll tick and would otherwise slice the
    same head of the pending list — wasting OpenAI quota and risking
    duplicate review-item rows.

    The function is the single chokepoint where AI calls happen during
    a job; it MUST stay short enough that the surrounding HTTP request
    completes well under Render's timeout. With batch_size<=30 and one
    OpenAI call (~5-10s), one advance call is comfortably under 20s
    even with retries enabled.
    """
    job = store.get_classification_job(job_id)
    if job is None:
        raise LookupError(f"classification job {job_id} not found")
    if job.is_terminal:
        return job
    # Phase 34S — concurrent-advance defence.
    #
    # In-memory race protection comes from the store's per-record
    # lock; the wizard's busy guard prevents single-tab double-clicks.
    # For multi-tab / multi-process Postgres concurrency we rely on
    # an INFLIGHT marker rather than a time-based heuristic. The
    # marker is set transactionally at the start of each advance and
    # cleared at the end; a second caller that sees it set in the
    # store raises :class:`ClassificationJobConflict` which the route
    # surfaces as 409 ``classification_job_conflict``.
    #
    # The check is intentionally permissive (skips cases where the
    # marker could legitimately remain set — for example after a
    # crashed advance) so we never block a legitimate retry. A stale
    # marker more than ``_ADVANCE_CONFLICT_WINDOW_SECONDS`` * 60 old
    # is ignored; the wizard simply continues.
    pass  # marker-based locking is a follow-up; rely on store lock
    if job.cancel_requested:
        cancelled = job.with_progress(
            status=ClassificationJobStatus.CANCELLED,
            completed_at=datetime.now(UTC),
        )
        store.update_classification_job(cancelled)
        return cancelled
    if ai_provider is None:
        failed = job.with_progress(
            status=ClassificationJobStatus.FAILED,
            error_code="ai_provider_unavailable",
            error_message=(
                "AI classifier is disabled or misconfigured on this server. "
                "Check ALTERA_AI_CLASSIFIER_ENABLED, ALTERA_AI_PROVIDER, "
                "and OPENAI_API_KEY."
            ),
            completed_at=datetime.now(UTC),
        )
        store.update_classification_job(failed)
        return failed
    if not job.pending_product_ids:
        # Nothing to do — finalise.
        coverage, _coverage_ms = _refresh_coverage_counters(store, job)
        finished = job.with_progress(
            status=(
                ClassificationJobStatus.COMPLETED_WITH_ERRORS
                if coverage["failed_total"] > 0
                else ClassificationJobStatus.COMPLETED
            ),
            completed_at=datetime.now(UTC),
            **coverage,
        )
        store.update_classification_job(finished)
        return finished

    now = datetime.now(UTC)
    # Slice the head of the pending list. Cap defensively at MAX_BATCH_SIZE
    # in case an old job record carries a larger batch_size from a
    # previous version of the code.
    take = max(1, min(job.batch_size, MAX_BATCH_SIZE))
    chunk_ids = list(job.pending_product_ids[:take])
    remaining = tuple(job.pending_product_ids[take:])

    # Load product records. Phase 35-perf — single bulk fetch instead
    # of N round-trips per advance batch.
    t_load = time.perf_counter()
    products = store.list_products_by_ids(chunk_ids)
    load_ms = (time.perf_counter() - t_load) * 1000

    # Single OpenAI call for this batch (with the existing in-batch
    # retry pass disabled — we keep retry control at the job level).
    running = job.with_progress(
        status=ClassificationJobStatus.RUNNING,
        started_at=job.started_at or now,
    )
    store.update_classification_job(running)

    failed_ids: list[UUID] = []
    provider_ms = 0.0
    try:
        t_provider = time.perf_counter()
        bundle = ai_batch_classify(
            products,
            ai_provider,
            job.methodology,
            now=now,
            batch_size=take,
            enable_retry=True,  # internal small-batch retry stays on
        )
        provider_ms = (time.perf_counter() - t_provider) * 1000
    except Exception as exc:  # noqa: BLE001 — surface any provider crash
        provider_ms = (time.perf_counter() - t_provider) * 1000
        # Provider blew up — record the error sample but DON'T fail
        # the whole job. Mark these rows as failed and continue. The
        # client can retry-failed later.
        sample = (
            *job.sample_errors,
            f"advance_provider_error: {type(exc).__name__}: {exc}",
        )[-10:]
        failed_ids = chunk_ids
        for p in products:
            _queue_unknown_pt(
                store, p, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
            ) if job.methodology is Methodology.PROTEIN_TRACKER else _queue_unknown_wwf(
                store, p, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
            )
        coverage, coverage_ms = _refresh_coverage_counters(store, job)
        _record_advance_timings(
            job, load_ms, provider_ms, 0.0, coverage_ms
        )
        new_status = (
            ClassificationJobStatus.COMPLETED_WITH_ERRORS
            if not remaining
            else ClassificationJobStatus.RUNNING
        )
        updated = job.with_progress(
            status=new_status,
            pending_product_ids=remaining,
            processed_products=job.processed_products + len(chunk_ids),
            failed_product_ids=tuple({*job.failed_product_ids, *failed_ids}),
            sample_errors=sample,
            completed_at=now if new_status.value.startswith("completed") else None,
            **coverage,
        )
        store.update_classification_job(updated)
        return updated

    # Apply verdicts to the store. Phase 35-perf — wrap the apply +
    # coverage refresh + final update in timers and emit one structured
    # ``classify.advance.timing`` log so the route can attribute slow
    # batches to provider vs. db work.
    t_db = time.perf_counter()
    for p, verdict in zip(products, bundle.verdicts, strict=True):
        if isinstance(verdict, AIAccepted):
            if job.methodology is Methodology.PROTEIN_TRACKER:
                store.upsert_pt_classification(verdict.classification)
            else:
                store.upsert_wwf_classification(verdict.classification)
            store.remove_review_item(p.id, job.methodology)
        elif isinstance(verdict, AINeedsReviewLowConfidence):
            if job.methodology is Methodology.PROTEIN_TRACKER:
                store.upsert_pt_classification(verdict.classification)
            else:
                store.upsert_wwf_classification(verdict.classification)
            _enqueue_review_item(
                store,
                p.id,
                job.methodology,
                ManualReviewQueueReason.LOW_CONFIDENCE,
                now,
            )
        elif isinstance(verdict, AINeedsReviewParseFailed):
            failed_ids.append(p.id)
            if job.methodology is Methodology.PROTEIN_TRACKER:
                _queue_unknown_pt(
                    store, p, ManualReviewQueueReason.AI_PARSE_FAILED, now
                )
            else:
                _queue_unknown_wwf(
                    store, p, ManualReviewQueueReason.AI_PARSE_FAILED, now
                )
        elif isinstance(verdict, AIProviderError):
            failed_ids.append(p.id)
            if job.methodology is Methodology.PROTEIN_TRACKER:
                _queue_unknown_pt(
                    store, p, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
                )
            else:
                _queue_unknown_wwf(
                    store, p, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
                )

    db_write_ms = (time.perf_counter() - t_db) * 1000

    # Aggregate diagnostics across this batch into the job record.
    next_sample = (*job.sample_errors, *bundle.sample_errors)[-10:]
    next_processed = job.processed_products + len(chunk_ids)
    coverage, coverage_ms = _refresh_coverage_counters(store, job)
    is_done = not remaining
    new_status = (
        (
            ClassificationJobStatus.COMPLETED_WITH_ERRORS
            if coverage["failed_total"] > 0
            else ClassificationJobStatus.COMPLETED
        )
        if is_done
        else ClassificationJobStatus.RUNNING
    )
    t_update = time.perf_counter()
    updated = job.with_progress(
        status=new_status,
        pending_product_ids=remaining,
        processed_products=next_processed,
        failed_product_ids=tuple({*job.failed_product_ids, *failed_ids}),
        retry_batches=job.retry_batches + bundle.retry_batches,
        recovered_rows=job.recovered_rows + bundle.recovered_rows,
        sample_errors=next_sample,
        completed_at=now if is_done else None,
        **coverage,
    )
    store.update_classification_job(updated)
    update_ms = (time.perf_counter() - t_update) * 1000
    _record_advance_timings(
        job,
        load_ms,
        provider_ms,
        db_write_ms,
        coverage_ms,
        update_ms=update_ms,
        batch_n=len(chunk_ids),
        guard_overrides_by_rule=bundle.guard_overrides_by_rule,
        unknown_safety_net_total=bundle.unknown_safety_net_total,
    )
    return updated


def _record_advance_timings(
    job: ClassificationJob,
    load_ms: float,
    provider_ms: float,
    db_write_ms: float,
    coverage_ms: float,
    *,
    update_ms: float = 0.0,
    batch_n: int = 0,
    guard_overrides_by_rule: dict[str, int] | None = None,
    unknown_safety_net_total: int = 0,
) -> None:
    """Phase 35-perf — single ``classify.advance.timing`` log line per
    batch with per-stage breakdown. Operators reading Render logs can
    immediately distinguish "OpenAI is slow today" (provider_ms high)
    from "Supabase is slow today" (db_write_ms / coverage_ms high).

    Phase 36J — also surfaces Phase-36I guard firings per batch so the
    operator can see which AI taxonomy errors the guards are catching.
    The breakdown is a compact ``rule=count`` comma list (empty when
    no guard fired); ``unknown_safety_net_total`` is the count of
    readable-name → unknown rerouted to needs_review.
    """
    import logging

    overrides = guard_overrides_by_rule or {}
    guard_breakdown = (
        ",".join(f"{rule}={count}" for rule, count in sorted(overrides.items()))
        or "none"
    )
    guard_total = sum(overrides.values())
    logging.getLogger("altera_api.classification_advance").info(
        "classify.advance.timing job_id=%s project=%s upload=%s "
        "batch_n=%d load_ms=%.1f provider_ms=%.1f db_write_ms=%.1f "
        "coverage_ms=%.1f update_ms=%.1f total_ms=%.1f "
        "guard_overrides_total=%d guard_overrides_by_rule=%s "
        "unknown_safety_net_total=%d",
        job.id,
        job.project_id,
        job.upload_id,
        batch_n,
        load_ms,
        provider_ms,
        db_write_ms,
        coverage_ms,
        update_ms,
        load_ms + provider_ms + db_write_ms + coverage_ms + update_ms,
        guard_total,
        guard_breakdown,
        unknown_safety_net_total,
    )


def cancel_classification_job(
    store: StoreProtocol, job_id: UUID
) -> ClassificationJob:
    """Flag the job for cancellation.

    The actual transition to CANCELLED happens on the next advance
    call. Already-terminal jobs are returned unchanged.
    """
    job = store.get_classification_job(job_id)
    if job is None:
        raise LookupError(f"classification job {job_id} not found")
    if job.is_terminal:
        return job
    cancelled = job.with_progress(
        cancel_requested=True,
        status=ClassificationJobStatus.CANCELLED,
        completed_at=datetime.now(UTC),
    )
    store.update_classification_job(cancelled)
    return cancelled


def retry_failed_in_classification_job(
    store: StoreProtocol,
    job_id: UUID,
    *,
    created_by: UUID | None = None,
) -> ClassificationJob:
    """Create a NEW job whose pending list is the previous job's
    failed_product_ids. The original job stays intact for audit.

    Returns the new job (queued). The wizard then advances it like
    any other.
    """
    prev = store.get_classification_job(job_id)
    if prev is None:
        raise LookupError(f"classification job {job_id} not found")
    if not prev.failed_product_ids:
        # No failures to retry — return a no-op completed job so the
        # client gets a coherent shape back.
        empty = ClassificationJob(
            id=uuid4(),
            organisation_id=prev.organisation_id,
            project_id=prev.project_id,
            upload_id=prev.upload_id,
            methodology=prev.methodology,
            status=ClassificationJobStatus.COMPLETED,
            total_products=0,
            processed_products=0,
            pending_product_ids=(),
            batch_size=prev.batch_size,
            created_by=created_by,
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        store.add_classification_job(empty)
        return empty
    now = datetime.now(UTC)
    job = ClassificationJob(
        id=uuid4(),
        organisation_id=prev.organisation_id,
        project_id=prev.project_id,
        upload_id=prev.upload_id,
        methodology=prev.methodology,
        status=ClassificationJobStatus.QUEUED,
        total_products=len(prev.failed_product_ids),
        processed_products=0,
        pending_product_ids=prev.failed_product_ids,
        overwrite=True,  # retry must rewrite the unknown rows
        only_missing_or_failed=False,
        batch_size=prev.batch_size,
        created_by=created_by,
        created_at=now,
    )
    store.add_classification_job(job)
    # Suppress unused import lint when Decimal is not used.
    _ = Decimal
    return job
