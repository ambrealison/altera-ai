"""Task execution — the actual work each job type does.

``execute_job`` is the single entry point: mark running, dispatch to
the right handler, mark succeeded/failed, emit audit events.

Handlers only use the store, the storage service, and the job payload
— no HTTP context. This keeps them runnable in any worker process.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from altera_api.api.orchestrator import (
    classify_upload,
    ingest_upload,
    render_export,
    run_calculation,
)
from altera_api.api.state import ExportRecord
from altera_api.domain.audit import AuditEvent, AuditEventType
from altera_api.domain.common import Methodology
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.ingestion.validators import validate_upload
from altera_api.persistence.protocol import StoreProtocol
from altera_api.storage.protocol import StorageProtocol

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute_job(
    job: Job,
    store: StoreProtocol,
    storage: StorageProtocol | None = None,
) -> Job:
    """Mark *job* running, call the appropriate handler, persist result."""
    now = datetime.now(UTC)
    running = job.model_copy(update={"status": JobStatus.RUNNING, "started_at": now})
    store.update_job(running)
    _audit(store, running, AuditEventType.JOB_STARTED)

    try:
        completed = _dispatch(running, store, storage)
        store.update_job(completed)
        _audit(store, completed, AuditEventType.JOB_SUCCEEDED)
        return completed
    except Exception as exc:
        failed = running.model_copy(update={
            "status": JobStatus.FAILED,
            "failed_at": datetime.now(UTC),
            "error_message": f"{type(exc).__name__}: {exc}",
        })
        store.update_job(failed)
        _audit(store, failed, AuditEventType.JOB_FAILED)
        return failed


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def _dispatch(job: Job, store: StoreProtocol, storage: StorageProtocol | None) -> Job:
    handlers = {
        JobType.VALIDATE_UPLOAD: _handle_validate_upload,
        JobType.INGEST_UPLOAD: _handle_ingest_upload,
        JobType.CLASSIFY_UPLOAD: _handle_classify_upload,
        JobType.RUN_CALCULATION: _handle_run_calculation,
        JobType.GENERATE_EXPORT: _handle_generate_export,
    }
    handler = handlers.get(job.job_type)
    if handler is None:
        raise NotImplementedError(f"no handler for job_type={job.job_type!r}")
    return handler(job, store, storage)


# ---------------------------------------------------------------------------
# File resolution helper
# ---------------------------------------------------------------------------

def _resolve_file(
    job: Job,
    store: StoreProtocol,
    storage: StorageProtocol | None,
) -> tuple[bytes, str, object]:
    """Return ``(file_bytes, filename, upload_record_or_None)``.

    Resolution order:
    1. If the upload record has a real (non-in_memory) storage_path, download
       from *storage*. Raises if *storage* is not configured.
    2. Fall back to ``file_bytes_b64`` in the job payload.
    3. Raise ``RuntimeError`` if neither source is available.
    """
    upload_id_str: str | None = job.payload.get("upload_id")
    filename: str = job.payload.get("filename") or "upload"
    rec = store.get_upload(UUID(upload_id_str)) if upload_id_str else None

    if rec is not None and not rec.upload.storage_path.startswith("in_memory/"):
        if storage is None:
            raise RuntimeError(
                f"upload {upload_id_str} has storage_path={rec.upload.storage_path!r} "
                "but no StorageService is available in this worker"
            )
        return storage.download(rec.upload.storage_path), filename, rec

    if "file_bytes_b64" in job.payload:
        import base64
        return base64.b64decode(job.payload["file_bytes_b64"]), filename, rec

    raise RuntimeError(
        f"cannot resolve file for upload {upload_id_str}: "
        "no storage configured and no file_bytes_b64 in job payload"
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_validate_upload(
    job: Job, store: StoreProtocol, storage: StorageProtocol | None
) -> Job:
    """Pre-flight file validation.

    Payload keys:
      upload_id      – UUID string of the target upload
      filename       – original filename (used for extension check)
      file_bytes_b64 – base64-encoded content (dev/test fallback)

    File bytes are resolved via ``_resolve_file``: real storage_path
    first, then b64 fallback.
    """
    file_bytes, filename, _rec = _resolve_file(job, store, storage)
    errors = validate_upload(filename, file_bytes)
    return _succeed(job, {"errors": errors, "is_valid": not errors})


def _handle_ingest_upload(
    job: Job, store: StoreProtocol, storage: StorageProtocol | None
) -> Job:
    """Full ingestion pipeline (parse + validate + normalise + persist).

    Payload keys:
      upload_id      – pre-allocated UUID string
      filename       – original filename
      file_bytes_b64 – base64-encoded content (dev/test fallback)
      storage_path   – override storage_path (optional; taken from upload
                       record when resolving via storage)
    """
    project = _require_project(job, store)
    file_bytes, filename, rec = _resolve_file(job, store, storage)

    upload_id_str: str | None = job.payload.get("upload_id")
    # Use the storage_path from the upload record when available; fall back
    # to the payload value (could be None → ingest_upload uses in_memory sentinel).
    resolved_storage_path: str | None = (
        rec.upload.storage_path
        if rec is not None
        else job.payload.get("storage_path")
    )

    summary = ingest_upload(
        store,
        project=project,
        file_bytes=file_bytes,
        original_filename=filename,
        uploaded_by=job.created_by,
        upload_id=UUID(upload_id_str) if upload_id_str else None,
        storage_path=resolved_storage_path,
    )
    return _succeed(
        job,
        {
            "upload_id": str(summary.upload.id),
            "status": summary.upload.status.value,
            "row_count": summary.report.total_rows,
            "products_count": summary.products_count,
            "errors": len(summary.report.errors),
        },
        upload_id=summary.upload.id,
    )


def _handle_classify_upload(
    job: Job, store: StoreProtocol, storage: StorageProtocol | None
) -> Job:
    """Deterministic rules + optional AI classification for one upload.

    Payload keys:
      upload_id   – UUID string
      methodology – "protein_tracker" | "wwf"

    AI provider is resolved from environment variables at job execution
    time (see ``altera_api.ai.config``). When disabled, pass-through
    products go directly to Altera manual review as before.
    """
    from altera_api.ai.config import get_ai_provider

    project = _require_project(job, store)
    upload_id = UUID(job.payload["upload_id"])
    methodology = Methodology(job.payload["methodology"])
    ai_provider = get_ai_provider()

    summary = classify_upload(
        store,
        project=project,
        upload_id=upload_id,
        methodology=methodology,
        ai_provider=ai_provider,
    )
    total = (
        summary.matched
        + summary.pass_through
        + summary.rule_collision
        + summary.contradictions
    )
    return _succeed(
        job,
        {
            "methodology": methodology.value,
            "matched": summary.matched,
            "pass_through": summary.pass_through,
            "rule_collision": summary.rule_collision,
            "contradictions": summary.contradictions,
            "queued_for_review": summary.queued_for_review,
            "ai_attempted": summary.ai_attempted,
            "ai_accepted": summary.ai_accepted,
            "ai_review": summary.ai_review,
            "ai_failed": summary.ai_failed,
            "total_products": total,
        },
    )


def _handle_run_calculation(
    job: Job, store: StoreProtocol, storage: StorageProtocol | None
) -> Job:
    """Execute the calculation pipeline and persist a RunRecord.

    Payload keys:
      methodology – "protein_tracker" | "wwf"
    """
    project = _require_project(job, store)
    methodology = Methodology(job.payload["methodology"])

    record = run_calculation(
        store,
        project=project,
        methodology=methodology,
        triggered_by=job.created_by,
    )
    return _succeed(
        job,
        {"run_id": str(record.id), "rows_count": record.rows_count},
        run_id=record.id,
    )


def _handle_generate_export(
    job: Job, store: StoreProtocol, storage: StorageProtocol | None
) -> Job:
    """Render a run export (CSV / JSON / Markdown).

    When *storage* is configured the rendered bytes are persisted to the
    exports bucket and an ``ExportRecord`` is created in the store.

    Payload keys:
      run_id – UUID string
      fmt    – "csv" | "json" | "md"
    """
    project = _require_project(job, store)
    run_id = UUID(job.payload["run_id"])
    fmt: Literal["csv", "json", "md"] = job.payload["fmt"]

    payload_bytes, _media_type, filename = render_export(
        store, project=project, run_id=run_id, fmt=fmt
    )
    result: dict = {
        "run_id": str(run_id),
        "fmt": fmt,
        "filename": filename,
        "size_bytes": len(payload_bytes),
    }

    if storage is not None:
        export_id = uuid4()
        storage_path = storage.export_storage_path(
            project.organisation_id, run_id, export_id, filename
        )
        storage.upload_export(storage_path, payload_bytes, filename)
        record = ExportRecord(
            id=export_id,
            run_id=run_id,
            organisation_id=project.organisation_id,
            format=fmt,
            status="success",
            storage_path=storage_path,
            filename=filename,
            size_bytes=len(payload_bytes),
            sha256=hashlib.sha256(payload_bytes).hexdigest(),
            requested_by=job.created_by,
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        store.add_export_record(record)
        result["export_id"] = str(export_id)
        result["storage_path"] = storage_path

    return _succeed(job, result, run_id=run_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _succeed(
    job: Job,
    result: dict,
    *,
    upload_id: UUID | None = None,
    run_id: UUID | None = None,
) -> Job:
    updates: dict = {
        "status": JobStatus.SUCCEEDED,
        "completed_at": datetime.now(UTC),
        "payload": {**job.payload, "result": result},
    }
    if upload_id is not None:
        updates["upload_id"] = upload_id
    if run_id is not None:
        updates["run_id"] = run_id
    return job.model_copy(update=updates)


def _require_project(job: Job, store: StoreProtocol):  # type: ignore[return]
    project = store.get_project(job.project_id)
    if project is None:
        raise LookupError(f"project {job.project_id} not found")
    return project


def _audit(store: StoreProtocol, job: Job, action: AuditEventType) -> None:
    try:
        store.append_audit(
            AuditEvent(
                id=uuid4(),
                organisation_id=job.organisation_id,
                actor_user_id=None,  # system events have no actor
                action=action,
                target_table="jobs",
                target_id=job.job_id,
                metadata={"job_type": job.job_type.value, "status": job.status.value},
                created_at=datetime.now(UTC),
            )
        )
    except Exception:
        pass  # audit failure must never kill the job
