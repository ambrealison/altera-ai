"""HTTP routes — projects, uploads, classify, review, runs, exports.

All routes are mounted under ``/api/v1``. Request and response bodies
are Pydantic models defined inline; they're intentionally narrower than
the full domain models so the wire contract is stable even as the
domain evolves.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from altera_api.api.dependencies import current_user_id, get_data_store, get_project
from altera_api.api.errors import (
    raise_forbidden,
)
from altera_api.api.orchestrator import (
    BulkActionResult,
    IngestSummary,
    assign_review_item,
    bulk_submit_decision,
    claim_review_item,
    classify_upload,
    create_upload_stub,
    ingest_upload,
    list_review,
    refresh_review_lock,
    release_review_item,
    render_export,
    run_calculation,
    submit_decision,
)
from altera_api.api.pagination import Page, PaginationParams, paginate
from altera_api.api.state import (
    ExportRecord,
    PersistedRecommendation,
    ScenarioOperationRecord,
    ScenarioRecord,
    ScenarioResultRecord,
)
from altera_api.auth import AuthContext, authed_user
from altera_api.domain.audit import AuditEvent, AuditEventType
from altera_api.domain.common import Methodology
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.project import Project
from altera_api.domain.report import ReportDocument
from altera_api.domain.report_exports import ReportApprovalStatus
from altera_api.domain.review import (
    ManualReviewPriority,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.scenario import (
    ScenarioOperation,
    ScenarioOperationType,
    ScenarioResult,
    ScenarioStatus,
)
from altera_api.exports.report import build_report_document
from altera_api.exports.summary_payload import (
    parse_pt_summary_payload,
    parse_wwf_summary_payload,
)
from altera_api.ingestion.validators import validate_upload
from altera_api.jobs.dependencies import get_worker
from altera_api.jobs.runner import WorkerBackend
from altera_api.persistence.protocol import StoreProtocol
from altera_api.storage.factory import get_storage_service
from altera_api.storage.service import StorageService

api_router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Current user (whoami)
# ---------------------------------------------------------------------------
class CurrentUserResponse(BaseModel):
    user_id: UUID
    email: str
    organisation_id: UUID
    role: str
    organisation_type: str
    auth_provider: str
    is_dev_auth: bool


@api_router.get("/me", response_model=CurrentUserResponse)
def whoami(auth: AuthContext = Depends(authed_user)) -> CurrentUserResponse:
    """The authenticated user's profile. Frontend uses it to show the
    user email + organisation context in the app shell."""
    return CurrentUserResponse(
        user_id=auth.user_id,
        email=auth.email,
        organisation_id=auth.organisation_id,
        role=auth.role.value,
        organisation_type=auth.organisation_type.value,
        auth_provider=auth.auth_provider.value,
        is_dev_auth=auth.is_dev_auth,
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    methodologies_enabled: list[Methodology] = Field(min_length=1)
    reporting_period_label: str = Field(min_length=1, max_length=80)


class ProjectResponse(BaseModel):
    id: UUID
    organisation_id: UUID
    name: str
    methodologies_enabled: list[str]
    reporting_period_label: str
    pt_validation_status: str
    upload_count: int
    review_queue_count: int
    run_count: int
    unclassified_pt_count: int = 0


def _project_response(store: StoreProtocol, project: Project) -> ProjectResponse:
    """Build a ProjectResponse, never raising for a single project.

    Phase 34P: per-field defensive so one bad classification row
    can't 500 the whole list.
    Phase 34W: bulk classification lookup — replaced the N+1 per-
    product loop with ``get_pt_classifications_bulk``.
    Phase 35-OOM: STOP loading the full ``NormalizedProduct`` list
    on every project response. The previous implementation called
    ``list_products_for_project`` just to count unclassified rows;
    each call materialised 1050 Pydantic objects (≈2-3 MB of
    Python memory) plus a parallel ``list_uploads_for_project``
    that ALSO loads all 1050 product ids. Compounded across
    concurrent GET /projects calls during a Render restart, this
    pushed the worker over the 512 MB limit and triggered OOM
    restarts that the wizard mistook for network failures.

    The new path uses the upload's ``product_ids`` tuple — already
    loaded by ``list_uploads_for_project`` — plus a single bulk
    classification fetch. Zero ``NormalizedProduct`` allocations.
    """

    def _safe_count(fn, default: int = 0) -> int:
        try:
            return fn()
        except Exception:
            return default

    # Phase 35-OOM — load uploads once; reuse the product_ids tuples
    # for the unclassified count instead of calling
    # ``list_products_for_project`` (which would re-fetch the same
    # rows AS full domain objects, then throw them away).
    try:
        upload_records = store.list_uploads_for_project(project.id)
    except Exception:
        upload_records = []

    unclassified_pt_count = 0
    if Methodology.PROTEIN_TRACKER in project.methodologies_enabled:
        all_product_ids: list[UUID] = []
        for ur in upload_records:
            try:
                all_product_ids.extend(ur.product_ids)
            except Exception:
                continue
        if all_product_ids:
            try:
                classified = store.get_pt_classifications_bulk(
                    all_product_ids
                )
                unclassified_pt_count = max(
                    0, len(all_product_ids) - len(classified)
                )
            except Exception:
                # Unreadable classifications shouldn't break the
                # projects list — surface the upper bound so the
                # wizard prompts the user to retry classify.
                unclassified_pt_count = len(all_product_ids)

    # Phase 36C — use the count-only probe instead of fetching every
    # review row just to call ``len(...)``. On a 1050-product project
    # the old path paid 8 PostgREST round-trips (2 to paginate product
    # ids + 6 to fetch ~38KB of review rows we then threw away) per
    # project. The new probe pays the same chunk count but each call
    # has ``head=True`` so no body bytes cross the wire.
    all_product_ids_for_reviews: list[UUID] = []
    for ur in upload_records:
        try:
            all_product_ids_for_reviews.extend(ur.product_ids)
        except Exception:
            continue
    review_queue_count = _safe_count(
        lambda: store.count_review_items_for_product_ids(
            all_product_ids_for_reviews
        )
    )

    return ProjectResponse(
        id=project.id,
        organisation_id=project.organisation_id,
        name=project.name,
        methodologies_enabled=sorted(m.value for m in project.methodologies_enabled),
        reporting_period_label=project.reporting_period_label,
        pt_validation_status=project.pt_validation_status.value,
        upload_count=len(upload_records),
        review_queue_count=review_queue_count,
        run_count=_safe_count(lambda: len(store.list_runs_for_project(project.id))),
        unclassified_pt_count=unclassified_pt_count,
    )


@api_router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreateRequest,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_data_store),
) -> ProjectResponse:
    if not auth.can_write_data:
        raise_forbidden("creating projects requires analyst, admin, or owner")
    project = store.create_project(
        name=body.name,
        methodologies_enabled=frozenset(body.methodologies_enabled),
        reporting_period_label=body.reporting_period_label,
        organisation_id=auth.organisation_id,
        created_by=auth.user_id,
    )
    return _project_response(store, project)


@api_router.get("/projects", response_model=Page[ProjectResponse])
def list_projects_route(
    pagination: Annotated[PaginationParams, Depends()],
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_data_store),
) -> Page[ProjectResponse]:
    """List every project visible to the caller.

    Phase 34P: per-project failures (e.g. an unreachable Supabase
    classification table after a failed classify, a stale enum value
    in one project's pt_validation_status) MUST NOT take down the whole
    response. We assemble a minimal-safe fallback for any project whose
    full response builder raised, so the workspace never goes blank.

    Phase 36C: emits ``projects_list.timing`` with per-stage breakdown
    so production logs reveal whether the cost is in ``list_projects``
    (cheap; one SELECT) or in the per-project ``_project_response``
    walk (which still touches uploads + classifications + reviews).
    """
    import logging
    import time

    t_total = time.perf_counter()
    t0 = time.perf_counter()
    try:
        projects = store.list_projects()
    except Exception:
        # Outer store call failing is exceptional but still must not
        # 500 the workspace — return an empty page so the wizard's
        # "Create your first project" path stays available.
        projects = []
    list_projects_ms = (time.perf_counter() - t0) * 1000
    if not auth.is_altera_internal:
        projects = [p for p in projects if p.organisation_id == auth.organisation_id]
    t0 = time.perf_counter()
    items: list[ProjectResponse] = []
    for p in projects:
        try:
            items.append(_project_response(store, p))
        except Exception:
            items.append(
                ProjectResponse(
                    id=p.id,
                    organisation_id=p.organisation_id,
                    name=p.name,
                    methodologies_enabled=sorted(
                        m.value for m in p.methodologies_enabled
                    ),
                    reporting_period_label=p.reporting_period_label,
                    pt_validation_status=p.pt_validation_status.value,
                    upload_count=0,
                    review_queue_count=0,
                    run_count=0,
                    unclassified_pt_count=0,
                )
            )
    per_project_summary_ms = (time.perf_counter() - t0) * 1000
    total_ms = (time.perf_counter() - t_total) * 1000
    logging.getLogger("altera_api.projects_list").info(
        "projects_list.timing n_projects=%d list_projects_ms=%.1f "
        "per_project_summary_ms=%.1f total_ms=%.1f",
        len(projects),
        list_projects_ms,
        per_project_summary_ms,
        total_ms,
    )
    return paginate(items, pagination)


@api_router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project_route(
    project: Project = Depends(get_project),
    store: StoreProtocol = Depends(get_data_store),
) -> ProjectResponse:
    return _project_response(store, project)


# ---------------------------------------------------------------------------
# Phase 34A — guided workflow status
# ---------------------------------------------------------------------------


class WorkflowBlockingReasonResponse(BaseModel):
    code: str
    label: str
    count: int = 0
    next_action: str | None = None


class WorkflowStepResponse(BaseModel):
    key: str
    label: str
    status: str
    progress_pct: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    blocking_reasons: list[WorkflowBlockingReasonResponse] = Field(default_factory=list)
    # Phase 34B — wizard fields
    accessible: bool = False
    editable: bool = False
    summary: str | None = None


class WorkflowNextActionResponse(BaseModel):
    label: str
    action: str
    href: str | None = None


class MethodologyClassificationCountsResponse(BaseModel):
    """Phase WWF-H — per-methodology classification status."""

    methodology: str
    total: int
    classified: int
    pending: int
    needs_review: int
    unknown: int
    failed: int = 0
    status: str


class WorkflowStatusResponse(BaseModel):
    project_id: str
    methodologies_enabled: list[str]
    overall_progress_pct: int
    current_step: str
    active_step: str | None = None   # Phase 34B alias
    next_action: WorkflowNextActionResponse | None
    steps: list[WorkflowStepResponse]
    # Phase WWF-H — backward-compatible per-methodology counts.
    classification_by_methodology: dict[
        str, MethodologyClassificationCountsResponse
    ] = Field(default_factory=dict)


@api_router.get(
    "/projects/{project_id}/workflow-status",
    response_model=WorkflowStatusResponse,
)
def get_workflow_status_route(
    project: Project = Depends(get_project),
    store: StoreProtocol = Depends(get_data_store),
) -> WorkflowStatusResponse:
    """Per-project guided-workflow state.

    The frontend's ``/projects/{id}/workflow`` page consumes this to
    render the stepper, progress bar, blocking reasons, and the single
    "next recommended action" CTA. The same payload also powers the
    Phase 34A run preflight so the gate is identical end-to-end.

    Phase 34Z-fix — production diagnostics. The workflow-status path
    is currently the most likely 1050-row trip-wire (PostgREST URL
    limits + Supabase RTT amplification). We log start + end + total
    duration so Render logs surface the slow project_id immediately
    when an incident recurs. The duration is the only extra cost on
    the hot path.
    """
    import logging
    import time

    from altera_api.api.workflow import compute_workflow_status

    log = logging.getLogger("altera_api.workflow_status")
    t0 = time.perf_counter()
    try:
        status_obj = compute_workflow_status(store, project)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.exception(
            "workflow_status.failed project_id=%s elapsed_ms=%.1f "
            "exc=%s: %s",
            project.id,
            elapsed_ms,
            type(exc).__name__,
            exc,
        )
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "workflow_status.ok project_id=%s elapsed_ms=%.1f steps=%d",
        project.id,
        elapsed_ms,
        len(status_obj.steps),
    )
    return WorkflowStatusResponse(
        project_id=status_obj.project_id,
        methodologies_enabled=status_obj.methodologies_enabled,
        overall_progress_pct=status_obj.overall_progress_pct,
        current_step=status_obj.current_step,
        next_action=(
            WorkflowNextActionResponse(
                label=status_obj.next_action.label,
                action=status_obj.next_action.action,
                href=status_obj.next_action.href,
            )
            if status_obj.next_action
            else None
        ),
        steps=[
            WorkflowStepResponse(
                key=s.key,
                label=s.label,
                status=s.status,
                progress_pct=s.progress_pct,
                counts=dict(s.counts),
                blocking_reasons=[
                    WorkflowBlockingReasonResponse(
                        code=r.code,
                        label=r.label,
                        count=r.count,
                        next_action=r.next_action,
                    )
                    for r in s.blocking_reasons
                ],
                accessible=s.accessible,
                editable=s.editable,
                summary=s.summary,
            )
            for s in status_obj.steps
        ],
        active_step=status_obj.active_step,
        classification_by_methodology={
            m: MethodologyClassificationCountsResponse(
                methodology=c.methodology,
                total=c.total,
                classified=c.classified,
                pending=c.pending,
                needs_review=c.needs_review,
                unknown=c.unknown,
                failed=c.failed,
                status=c.status,
            )
            for m, c in status_obj.classification_by_methodology.items()
        },
    )


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
class ValidationEntryResponse(BaseModel):
    row_number: int
    field: str | None
    code: str
    message: str


class UploadResponse(BaseModel):
    id: UUID
    project_id: UUID
    original_filename: str
    status: str
    row_count: int | None
    dropped_columns: list[str]
    products_count: int
    # Phase 34S — capped to UPLOAD_RESPONSE_DETAIL_LIMIT entries each.
    # A 1050-row CSV with errors on every row used to return a 1050-
    # item ``errors`` array (and another 1050-item ``warnings``),
    # ballooning the response to several megabytes and triggering
    # browser fetch timeouts. We now return at most ~50 entries plus
    # the total counts below; the UI says "Showing first 50 of N".
    errors: list[ValidationEntryResponse]
    warnings: list[ValidationEntryResponse]
    errors_total: int = 0
    warnings_total: int = 0
    # Phase 15 metadata
    file_size_bytes: int | None = None
    checksum_sha256: str | None = None
    duplicate_of: UUID | None = None
    validation_started_at: str | None = None
    validation_completed_at: str | None = None
    ingestion_started_at: str | None = None
    ingestion_completed_at: str | None = None


#: Phase 34S — maximum number of error/warning entries we serialise
#: into a single UploadResponse. Keeps the response well under 1 MB
#: even for a 15K-row CSV with errors on every row. The frontend
#: still gets the *total* counts via ``errors_total`` /
#: ``warnings_total`` so it can render "Showing first N of M".
UPLOAD_RESPONSE_DETAIL_LIMIT = 50


def _upload_response(summary: IngestSummary) -> UploadResponse:
    u = summary.upload
    # Phase 34S — cap the per-row error/warning lists. A 1050-row CSV
    # with errors on every row used to produce a ~1.5 MB response and
    # trip the browser's fetch timeout; capping to the first 50 keeps
    # the payload predictable while preserving the total counts the
    # wizard needs to show "Showing first 50 of N".
    all_errors = list(summary.report.errors)
    all_warnings = list(summary.report.warnings)
    return UploadResponse(
        id=u.id,
        project_id=u.project_id,
        original_filename=u.original_filename,
        status=u.status.value,
        row_count=u.row_count,
        dropped_columns=list(summary.dropped_columns),
        products_count=summary.products_count,
        errors=[
            ValidationEntryResponse(
                row_number=e.row_number, field=e.field, code=e.code, message=e.message
            )
            for e in all_errors[:UPLOAD_RESPONSE_DETAIL_LIMIT]
        ],
        warnings=[
            ValidationEntryResponse(
                row_number=w.row_number, field=w.field, code=w.code, message=w.message
            )
            for w in all_warnings[:UPLOAD_RESPONSE_DETAIL_LIMIT]
        ],
        errors_total=len(all_errors),
        warnings_total=len(all_warnings),
        file_size_bytes=u.file_size_bytes,
        checksum_sha256=u.checksum_sha256,
        duplicate_of=summary.duplicate_of,
        validation_started_at=u.validation_started_at.isoformat()
        if u.validation_started_at
        else None,
        validation_completed_at=u.validation_completed_at.isoformat()
        if u.validation_completed_at
        else None,
        ingestion_started_at=u.ingestion_started_at.isoformat() if u.ingestion_started_at else None,
        ingestion_completed_at=u.ingestion_completed_at.isoformat()
        if u.ingestion_completed_at
        else None,
    )


@api_router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_csv(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    user_id: Annotated[UUID, Depends(current_user_id)],
    file: Annotated[UploadFile, File(description="CSV file")],
    column_mapping: Annotated[
        str | None,
        Form(description="JSON-encoded dict mapping normalised_header → canonical_field | 'ignore'"),
    ] = None,
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="file is required")
    payload = await file.read()
    pre_errors = validate_upload(file.filename, payload, content_type=file.content_type)
    if pre_errors:
        raise HTTPException(status_code=400, detail="; ".join(pre_errors))
    parsed_mapping: dict[str, str] | None = None
    if column_mapping:
        try:
            parsed_mapping = json.loads(column_mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"column_mapping is not valid JSON: {exc}") from exc
    summary = ingest_upload(
        store,
        project=project,
        file_bytes=payload,
        original_filename=file.filename,
        uploaded_by=user_id,
        content_type=file.content_type,
        column_mapping=parsed_mapping,
    )
    return _upload_response(summary)


# ---------------------------------------------------------------------------
# Phase 34X — chunked CSV ingestion jobs
# ---------------------------------------------------------------------------


class IngestionJobResponse(BaseModel):
    """Compact projection of an :class:`IngestionJob` for the wizard.

    Intentionally excludes ``pending_payload`` (which can be several
    MB inline) so the polling loop stays cheap. The wizard never
    needs the raw pending rows; it only needs progress counters.
    """

    job_id: UUID
    project_id: UUID
    upload_id: UUID
    status: str
    total_rows: int
    processed_rows: int
    inserted_products: int
    progress_pct: float
    errors_total: int
    warnings_total: int
    sample_errors: list[str]
    chunk_size: int
    started_at: str | None
    completed_at: str | None
    error_code: str | None
    error_message: str | None


def _ingestion_job_response(job: object) -> IngestionJobResponse:
    from altera_api.domain.ingestion_job import IngestionJob

    assert isinstance(job, IngestionJob)
    return IngestionJobResponse(
        job_id=job.id,
        project_id=job.project_id,
        upload_id=job.upload_id,
        status=job.status.value,
        total_rows=job.total_rows,
        processed_rows=job.processed_rows,
        inserted_products=job.inserted_products,
        progress_pct=job.progress_pct,
        errors_total=job.errors_total,
        warnings_total=job.warnings_total,
        sample_errors=list(job.sample_errors),
        chunk_size=job.chunk_size,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_code=job.error_code,
        error_message=job.error_message,
    )


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/ingestion-jobs",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ingestion_job_route(
    upload_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    user_id: Annotated[UUID, Depends(current_user_id)],
    file: Annotated[UploadFile, File(description="CSV file")],
    column_mapping: Annotated[str | None, Form(...)] = None,
    chunk_size: Annotated[int, Form()] = 250,
) -> IngestionJobResponse:
    """Phase 34X — start a chunked CSV ingestion job.

    The route parses the CSV up-front (CPU-only, fast) and persists
    the parsed product list inline on the job row. No products are
    inserted into the products table here; that happens in the
    follow-up ``/advance`` polling loop.
    """
    from altera_api.api.ingestion_job_orchestrator import (
        create_ingestion_job,
    )
    from altera_api.api.orchestrator import (
        _create_upload_record_for_ingestion_job,
    )

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "missing_file",
                "message": "file is required",
            },
        )
    payload = await file.read()
    pre_errors = validate_upload(
        file.filename, payload, content_type=file.content_type
    )
    if pre_errors:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_csv",
                "message": "; ".join(pre_errors),
            },
        )
    parsed_mapping: dict[str, str] | None = None
    if column_mapping:
        try:
            parsed_mapping = json.loads(column_mapping)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "invalid_mapping",
                    "message": f"column_mapping is not valid JSON: {exc}",
                },
            ) from exc

    # Phase 35B — heavy-job guard. We don't know the row count
    # before parsing, but file size is a strong proxy: an average
    # retailer CSV row is ~100-200 bytes, so a file >=100 KB almost
    # certainly contains 1000+ rows. Apply the cheap pre-parse
    # check so we reject heavy ingestion before paying parse cost.
    if len(payload) >= 100 * 1024:
        # Phase 36A — only jobs with a recent advance count as
        # blockers; paused-but-resumable jobs (advanced > active
        # window but < stale window ago) are silently let through.
        try:
            active_class = store.count_active_heavy_classification_jobs(
                min_total_products=_HEAVY_CLASSIFICATION_THRESHOLD,
                max_age_minutes=_HEAVY_JOB_ACTIVE_MINUTES,
            )
            active_ingest = store.count_active_heavy_ingestion_jobs(
                min_total_rows=_HEAVY_INGESTION_THRESHOLD,
                max_age_minutes=_HEAVY_JOB_ACTIVE_MINUTES,
            )
        except Exception:
            active_class = active_ingest = 0
        # Phase 35-stale opportunistic self-heal (same pattern as
        # the classification guard above).
        if active_class + active_ingest == 0:
            try:
                healed_class = store.cancel_stale_classification_jobs(
                    stale_after_minutes=_HEAVY_JOB_STALE_MINUTES
                )
                healed_ingest = store.cancel_stale_ingestion_jobs(
                    stale_after_minutes=_HEAVY_JOB_STALE_MINUTES
                )
                if healed_class + healed_ingest > 0:
                    import logging

                    logging.getLogger(
                        "altera_api.heavy_job_guard"
                    ).info(
                        "heavy_job_guard.self_heal "
                        "scope=ingestion classification=%d "
                        "ingestion=%d",
                        healed_class,
                        healed_ingest,
                    )
            except Exception:
                pass
        if active_class + active_ingest > 0:
            import logging

            logging.getLogger("altera_api.heavy_job_guard").info(
                "heavy_job_guard.rejected scope=ingestion "
                "project_id=%s upload_id=%s file_kb=%d "
                "active_class=%d active_ingest=%d",
                project.id,
                upload_id,
                len(payload) // 1024,
                active_class,
                active_ingest,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "heavy_job_in_progress",
                    "message": (
                        "Un traitement volumineux est déjà en cours sur "
                        "la plateforme. Il peut provenir d'une autre "
                        "organisation. Réessayez dans quelques minutes."
                    ),
                    "active_classification_jobs": active_class,
                    "active_ingestion_jobs": active_ingest,
                },
            )

    # Parse CSV in pure Python (fast, no DB hits).
    try:
        parse_result = _create_upload_record_for_ingestion_job(
            store,
            project=project,
            upload_id=upload_id,
            file_bytes=payload,
            original_filename=file.filename,
            uploaded_by=user_id,
            content_type=file.content_type,
            column_mapping=parsed_mapping,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "ingestion_create_failed",
                "message": str(exc) or "could not parse CSV",
            },
        ) from exc

    job = create_ingestion_job(
        store,
        organisation_id=auth.organisation_id,
        project=project,
        upload_id=parse_result.upload.id,
        parsed_products=list(parse_result.products),
        mapping=parsed_mapping,
        chunk_size=chunk_size,
        created_by=user_id,
        initial_errors_total=len(parse_result.report.errors),
        initial_warnings_total=len(parse_result.report.warnings),
        initial_sample_errors=tuple(
            f"row {e.row_number}: {e.code}: {e.message}"
            for e in parse_result.report.errors[:20]
        ),
    )
    return _ingestion_job_response(job)


@api_router.get(
    "/projects/{project_id}/ingestion-jobs/{job_id}",
    response_model=IngestionJobResponse,
)
def get_ingestion_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> IngestionJobResponse:
    """Pure status read of an ingestion job."""
    job = store.get_ingestion_job(job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "ingestion_job_not_found",
                "message": f"ingestion job {job_id} not found",
            },
        )
    return _ingestion_job_response(job)


@api_router.post(
    "/projects/{project_id}/ingestion-jobs/{job_id}/advance",
    response_model=IngestionJobResponse,
)
def advance_ingestion_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> IngestionJobResponse:
    """Process the next chunk and return updated state.

    Phase 35-OOM — also emits a memory probe log on every advance so
    we can correlate Render OOM restarts with specific (project_id,
    upload_id, pending_count) triples. The probe uses
    ``resource.getrusage`` which is POSIX-standard and always
    available on Render's Linux containers.
    """
    import logging
    import time

    from altera_api.api.ingestion_job_orchestrator import (
        advance_ingestion_job,
    )

    log = logging.getLogger("altera_api.ingestion_advance")
    existing = store.get_ingestion_job(job_id)
    if existing is None or existing.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "ingestion_job_not_found",
                "message": f"ingestion job {job_id} not found",
            },
        )
    t0 = time.perf_counter()
    try:
        job = advance_ingestion_job(store, job_id, project=project)
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "ingestion_job_not_found",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "ingestion_advance_failed",
                "message": str(exc) or "advance crashed",
            },
        ) from exc
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Memory probe — ru_maxrss is in kilobytes on Linux. Logged
    # only if the import succeeds, so the probe never breaks the
    # request path.
    rss_mb: float | None = None
    try:
        import resource as _r

        rss_mb = _r.getrusage(_r.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        rss_mb = None
    log.info(
        "ingestion_advance.ok job_id=%s project=%s upload=%s "
        "batch_size=%d processed=%d→%d total=%d inserted=%d "
        "pending_remaining=%d status=%s duration_ms=%.1f rss_mb=%s",
        job_id,
        project.id,
        existing.upload_id,
        existing.chunk_size,
        existing.processed_rows,
        job.processed_rows,
        job.total_rows,
        job.inserted_products,
        max(0, job.total_rows - job.processed_rows),
        job.status.value,
        elapsed_ms,
        f"{rss_mb:.1f}" if rss_mb is not None else "?",
    )
    return _ingestion_job_response(job)


@api_router.get(
    "/projects/{project_id}/uploads",
    response_model=Page[UploadResponse],
)
def list_uploads_route(
    pagination: Annotated[PaginationParams, Depends()],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> Page[UploadResponse]:
    items = [
        UploadResponse(
            id=rec.upload.id,
            project_id=rec.upload.project_id,
            original_filename=rec.upload.original_filename,
            status=rec.upload.status.value,
            row_count=rec.upload.row_count,
            dropped_columns=list(rec.upload.dropped_columns),
            products_count=len(rec.product_ids),
            errors=[],
            warnings=[],
            file_size_bytes=rec.upload.file_size_bytes,
            checksum_sha256=rec.upload.checksum_sha256,
            duplicate_of=rec.duplicate_of,
        )
        for rec in store.list_uploads_for_project(project.id)
    ]
    return paginate(items, pagination)


@api_router.get(
    "/projects/{project_id}/uploads/{upload_id}",
    response_model=UploadResponse,
)
def get_upload_route(
    upload_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> UploadResponse:
    rec = store.get_upload(upload_id)
    if rec is None or rec.upload.project_id != project.id:
        raise HTTPException(status_code=404, detail="upload not found")
    report = store.get_upload_validation_report(upload_id)
    errors = list(report.errors) if report else []
    warnings = list(report.warnings) if report else []
    return UploadResponse(
        id=rec.upload.id,
        project_id=rec.upload.project_id,
        original_filename=rec.upload.original_filename,
        status=rec.upload.status.value,
        row_count=rec.upload.row_count,
        dropped_columns=list(rec.upload.dropped_columns),
        products_count=len(rec.product_ids),
        errors=[
            ValidationEntryResponse(
                row_number=e.row_number, field=e.field, code=e.code, message=e.message
            )
            for e in errors
        ],
        warnings=[
            ValidationEntryResponse(
                row_number=w.row_number, field=w.field, code=w.code, message=w.message
            )
            for w in warnings
        ],
        file_size_bytes=rec.upload.file_size_bytes,
        checksum_sha256=rec.upload.checksum_sha256,
        duplicate_of=rec.duplicate_of,
        validation_started_at=rec.upload.validation_started_at.isoformat()
        if rec.upload.validation_started_at
        else None,
        validation_completed_at=rec.upload.validation_completed_at.isoformat()
        if rec.upload.validation_completed_at
        else None,
        ingestion_started_at=rec.upload.ingestion_started_at.isoformat()
        if rec.upload.ingestion_started_at
        else None,
        ingestion_completed_at=rec.upload.ingestion_completed_at.isoformat()
        if rec.upload.ingestion_completed_at
        else None,
    )


@api_router.delete(
    "/projects/{project_id}/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_upload_route(
    upload_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> Response:
    """Delete an upload and every record that references it.

    Removes the upload, its products, their PT/WWF classifications, manual
    review items, and enrichment records. Calculation runs are preserved
    because they're not tied to a specific upload via FK.
    """
    if not auth.can_write_data:
        raise_forbidden("deleting uploads requires analyst, admin, or owner")
    rec = store.get_upload(upload_id)
    if rec is None or rec.upload.project_id != project.id:
        raise HTTPException(status_code=404, detail="upload not found")
    try:
        store.delete_upload(upload_id)
    except APIError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "upload_delete_failed",
                "message": f"Upload could not be deleted: {exc.message}",
                "postgrest_code": getattr(exc, "code", None),
            },
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Storage-backed upload flow (Phase 13D)
# ---------------------------------------------------------------------------
class PrepareUploadResponse(BaseModel):
    upload_id: UUID
    storage_path: str
    signed_url: str
    expires_in: int


class PrepareUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


@api_router.post(
    "/projects/{project_id}/uploads/prepare",
    response_model=PrepareUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def prepare_upload_route(
    body: PrepareUploadRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    user_id: Annotated[UUID, Depends(current_user_id)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
) -> PrepareUploadResponse:
    """Reserve an upload ID, create a stub record, and return a signed URL."""
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Supabase Storage is not configured on this server",
        )
    upload_id = uuid4()
    expires_in = 300
    storage_path = storage.storage_path(
        project.organisation_id, project.id, upload_id, body.filename
    )
    signed_url = storage.generate_upload_url(storage_path, expires_in=expires_in)
    create_upload_stub(
        store,
        project=project,
        upload_id=upload_id,
        original_filename=body.filename,
        storage_path=storage_path,
        uploaded_by=user_id,
    )
    return PrepareUploadResponse(
        upload_id=upload_id,
        storage_path=storage_path,
        signed_url=signed_url,
        expires_in=expires_in,
    )


class IngestFromStorageRequest(BaseModel):
    storage_path: str
    original_filename: str
    column_mapping: dict[str, str] | None = None


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/ingest",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_from_storage_route(
    upload_id: UUID,
    body: IngestFromStorageRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    user_id: Annotated[UUID, Depends(current_user_id)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
) -> UploadResponse:
    """Download the file from Storage and run the ingestion pipeline."""
    if storage is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Supabase Storage is not configured on this server",
        )
    try:
        payload = storage.download(body.storage_path)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not fetch file from storage: {exc}"
        ) from exc
    pre_errors = validate_upload(body.original_filename, payload)
    if pre_errors:
        raise HTTPException(status_code=400, detail="; ".join(pre_errors))
    summary = ingest_upload(
        store,
        project=project,
        file_bytes=payload,
        original_filename=body.original_filename,
        uploaded_by=user_id,
        upload_id=upload_id,
        storage_path=body.storage_path,
        column_mapping=body.column_mapping,
    )
    return _upload_response(summary)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    methodology: Methodology
    # Phase 34B — when True, skip AI and run deterministic rules only.
    deterministic_only: bool = False
    # Phase 34I — when True (new normal-user default), skip the
    # deterministic rule engine entirely and use AI as the primary
    # classifier for every eligible product (except those whose
    # current classification is manually locked). Cannot be set
    # alongside deterministic_only=True.
    skip_deterministic: bool = False


class ClassifyResponse(BaseModel):
    methodology: str
    matched: int
    pass_through: int
    rule_collision: int
    queued_for_review: int
    # Phase 34C — whether AI was configured and active for this run.
    ai_enabled: bool = False
    # Phase 34D — full diagnostic counts so the wizard can never appear silent.
    total_products: int = 0
    ai_attempted: int = 0
    ai_accepted: int = 0
    ai_review: int = 0
    ai_failed: int = 0
    # Why was AI disabled (if it was)? One of:
    #   "deterministic_only" — caller passed deterministic_only=true
    #   "classifier_disabled" — ALTERA_AI_CLASSIFIER_ENABLED is false
    #   "provider_disabled" — ALTERA_AI_PROVIDER=disabled
    #   "provider_misconfigured" — provider name set but API key missing
    #   None — AI ran (ai_enabled is true)
    ai_disabled_reason: str | None = None
    # Phase 34F — finer-grained diagnostic counts so the wizard can
    # show *why* AI rejected a classification (parse vs unsupported
    # category vs provider error), plus a sample of error strings.
    ai_parse_failures: int = 0
    ai_unsupported_category_failures: int = 0
    ai_provider_errors: int = 0
    ai_batch_count: int = 0
    ai_sample_errors: list[str] = Field(default_factory=list)
    # Phase 34P — retry diagnostics. ``ai_retry_batches`` is how many
    # extra small-batch calls the orchestrator issued after a parse or
    # provider failure in the main pass; ``ai_recovered_rows`` is how
    # many of those rows came back with a usable verdict. Both are 0
    # when the main pass succeeded outright.
    ai_retry_batches: int = 0
    ai_recovered_rows: int = 0
    # Phase 34Q — coverage-oriented counters. ``categorized_total``
    # includes review_required rows (they have a proposed pt_group)
    # so the wizard's Step 4 banner can stop misleadingly implying
    # that review = uncategorized.
    categorized_total: int = 0
    accepted_total: int = 0
    review_required_total: int = 0
    out_of_scope_total: int = 0
    unknown_total: int = 0


def _ai_disabled_reason(deterministic_only: bool) -> str | None:
    """Inspect AI settings and return a machine-readable reason when AI is off.

    Returns None when AI is fully configured and the caller did not request
    deterministic-only — i.e. when ``get_ai_provider()`` would return a real
    provider. The reasons are stable codes the frontend maps to French
    messages so the wizard can never silently do nothing.
    """
    if deterministic_only:
        return "deterministic_only"
    from altera_api.ai.config import AISettings

    s = AISettings()
    if not s.altera_ai_classifier_enabled:
        return "classifier_disabled"
    provider = s.altera_ai_provider.lower()
    if provider == "disabled":
        return "provider_disabled"
    if provider == "openai" and not s.openai_api_key:
        return "provider_misconfigured"
    return None


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/classify",
    response_model=ClassifyResponse,
)
def classify_route(
    upload_id: UUID,
    body: ClassifyRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> ClassifyResponse:
    from altera_api.ai.config import get_ai_provider

    # Phase 34B — deterministic_only skips AI entirely.
    # Phase 34D — when AI settings are misconfigured (e.g. OPENAI_API_KEY
    # missing while ALTERA_AI_PROVIDER=openai), fall back to deterministic
    # rather than crashing the route. The diagnostic reason is surfaced
    # to the wizard so the user sees a clear "indisponible" banner
    # instead of an opaque 500.
    ai_provider = None
    if not body.deterministic_only:
        try:
            ai_provider = get_ai_provider()
        except ValueError:
            ai_provider = None
    if body.deterministic_only and body.skip_deterministic:
        raise HTTPException(
            status_code=400,
            detail="deterministic_only and skip_deterministic are mutually exclusive",
        )
    try:
        summary = classify_upload(
            store,
            project=project,
            upload_id=upload_id,
            methodology=body.methodology,
            ai_provider=ai_provider,
            skip_deterministic=body.skip_deterministic,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "upload_not_found",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "classify_invalid_request",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 — last-resort guard
        # Phase 34P — every other failure mode must become a structured
        # 502 with a machine-readable error_code, never a bare 500 with
        # a stack trace. The wizard handler maps "classify_failed" to a
        # short French banner and lets the user retry.
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "classify_failed",
                "message": str(exc) or "classify orchestrator raised",
            },
        ) from exc
    upload_record = store.get_upload(upload_id)
    total = len(upload_record.product_ids) if upload_record is not None else 0
    return ClassifyResponse(
        methodology=summary.methodology.value,
        matched=summary.matched,
        pass_through=summary.pass_through,
        rule_collision=summary.rule_collision,
        queued_for_review=summary.queued_for_review,
        ai_enabled=ai_provider is not None,
        total_products=total,
        ai_attempted=summary.ai_attempted,
        ai_accepted=summary.ai_accepted,
        ai_review=summary.ai_review,
        ai_failed=summary.ai_failed,
        ai_disabled_reason=_ai_disabled_reason(body.deterministic_only),
        ai_parse_failures=summary.ai_parse_failures,
        ai_unsupported_category_failures=summary.ai_unsupported_category_failures,
        ai_provider_errors=summary.ai_provider_errors,
        ai_batch_count=summary.ai_batch_count,
        ai_sample_errors=list(summary.ai_sample_errors),
        ai_retry_batches=summary.ai_retry_batches,
        ai_recovered_rows=summary.ai_recovered_rows,
        categorized_total=summary.categorized_total,
        accepted_total=summary.accepted_total,
        review_required_total=summary.review_required_total,
        out_of_scope_total=summary.out_of_scope_total,
        unknown_total=summary.unknown_total,
    )


# ---------------------------------------------------------------------------
# Phase 34R — async, chunked AI classification jobs
# ---------------------------------------------------------------------------


class ClassificationJobCreateRequest(BaseModel):
    methodology: Methodology = Methodology.PROTEIN_TRACKER
    overwrite: bool = False
    only_missing_or_failed: bool = True
    # Phase 35-perf — None means "use server default from env
    # ALTERA_AI_CLASSIFICATION_BATCH_SIZE" (defaults to 25). Lets ops
    # bump this without a frontend redeploy.
    batch_size: int | None = None


class ClassificationJobResponse(BaseModel):
    """Public shape of a classification job.

    Stays compact so the frontend's 2s polling loop is cheap and
    survives a temporary network blip — there's no nested product list
    here, just counters + sample errors.
    """

    job_id: UUID
    project_id: UUID
    upload_id: UUID
    methodology: str
    status: str
    total_products: int
    processed_products: int
    progress_pct: float
    categorized_total: int
    accepted_total: int
    review_required_total: int
    failed_total: int
    unknown_total: int
    out_of_scope_total: int
    retry_batches: int
    recovered_rows: int
    failed_product_count: int
    started_at: str | None
    completed_at: str | None
    error_code: str | None
    error_message: str | None
    sample_errors: list[str]
    # Phase 35-perf — surface the effective batch size so ops can
    # verify the env override (ALTERA_AI_CLASSIFICATION_BATCH_SIZE)
    # is reaching the job without scraping logs.
    batch_size: int


def _classification_job_response(job: object) -> ClassificationJobResponse:
    from altera_api.domain.classification_job import ClassificationJob

    assert isinstance(job, ClassificationJob)
    return ClassificationJobResponse(
        job_id=job.id,
        project_id=job.project_id,
        upload_id=job.upload_id,
        methodology=job.methodology.value,
        status=job.status.value,
        total_products=job.total_products,
        processed_products=job.processed_products,
        progress_pct=job.progress_pct,
        categorized_total=job.categorized_total,
        accepted_total=job.accepted_total,
        review_required_total=job.review_required_total,
        failed_total=job.failed_total,
        unknown_total=job.unknown_total,
        out_of_scope_total=job.out_of_scope_total,
        retry_batches=job.retry_batches,
        recovered_rows=job.recovered_rows,
        failed_product_count=len(job.failed_product_ids),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_code=job.error_code,
        error_message=job.error_message,
        sample_errors=list(job.sample_errors),
        batch_size=job.batch_size,
    )


# Phase 35B — heavy-job thresholds. A job is considered "heavy"
# when the relevant size signal meets/exceeds the threshold.
# Below the threshold the guard is bypassed so small uploads
# (100-row pilot CSVs) never get queued behind a 10K-row import.
#
# Override-able via environment so Render Pro can tune in
# staging without a code change. Defaults match the product
# decision in Phase 35:
#   - 500 eligible products triggers classification guard
#   - 1000 rows triggers ingestion guard
_HEAVY_CLASSIFICATION_THRESHOLD: int = int(
    os.environ.get("ALTERA_HEAVY_CLASSIFICATION_THRESHOLD", "500")
)
_HEAVY_INGESTION_THRESHOLD: int = int(
    os.environ.get("ALTERA_HEAVY_INGESTION_THRESHOLD", "1000")
)
# Phase 35-stale — a job left in ``queued|running`` after this many
# minutes is treated as dead. The heavy-job guard skips it (so it
# stops blocking new heavy jobs) AND opportunistically transitions
# it to a terminal state on the next guard check. Production
# scenario this protects against: Render OOM-restart leaves stale
# jobs that would otherwise lock out every subsequent classification
# / ingestion until manual cleanup.
_HEAVY_JOB_STALE_MINUTES: int = int(
    os.environ.get("ALTERA_HEAVY_JOB_STALE_MINUTES", "30")
)
# Phase 36A — distinguish "actively running right now" from "paused
# but resumable". The heavy-job guard only treats a job as a blocker
# if it has advanced within ``_HEAVY_JOB_ACTIVE_MINUTES``; anything
# older is treated as paused (still findable via Phase 35A resume
# short-circuit for its own upload, but invisible to the cross-org
# guard). Anything older than ``_HEAVY_JOB_STALE_MINUTES`` is stale
# and gets auto-cancelled by the self-heal pass.
#
# Default of 2 min matches Render's typical advance cadence: the
# wizard polls every ~1.5s, so a healthy job always has an
# ``updated_at`` < 60s. 2 min gives plenty of headroom for one slow
# OpenAI batch without letting a closed-tab job lock out the
# platform indefinitely.
_HEAVY_JOB_ACTIVE_MINUTES: int = int(
    os.environ.get("ALTERA_HEAVY_JOB_ACTIVE_MINUTES", "2")
)


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/classification-jobs",
    response_model=ClassificationJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_classification_job_route(
    upload_id: UUID,
    body: ClassificationJobCreateRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: AuthContext = Depends(authed_user),
) -> ClassificationJobResponse:
    """Create a new chunked AI classification job.

    Returns immediately with the job id and total eligible product
    count. The browser then polls ``advance`` to process batches.
    """
    from altera_api.api.classification_job_orchestrator import (
        create_classification_job,
    )

    if body.methodology not in project.methodologies_enabled:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "methodology_not_enabled",
                "message": (
                    f"methodology {body.methodology.value} is not "
                    f"enabled on project {project.id}"
                ),
            },
        )
    upload_record = store.get_upload(upload_id)
    if upload_record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "upload_not_found",
                "message": f"upload {upload_id} not found",
            },
        )
    if upload_record.upload.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "upload_not_found",
                "message": "upload does not belong to this project",
            },
        )

    # Phase 35A — resume short-circuit. If a non-terminal job
    # already exists for this exact (upload, methodology) pair,
    # return it instead of creating a duplicate. The frontend
    # treats 409 with error_code=classification_job_active +
    # job_id as the "resume the existing job" signal.
    existing_active = store.find_active_classification_job(
        upload_id=upload_id, methodology=body.methodology
    )
    if existing_active is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "classification_job_active",
                "message": (
                    "Une classification est déjà en cours pour ce "
                    "fichier. Reprenez le job existant."
                ),
                "job_id": str(existing_active.id),
                "status": existing_active.status.value,
                "processed_products": existing_active.processed_products,
                "total_products": existing_active.total_products,
            },
        )

    # Phase 35B — heavy-job guard. We define "heavy" as
    # total_products >= 500. The new job's total_products isn't
    # known until create_classification_job filters eligible
    # products, but we can probe upfront on the upload's product
    # count. We use the upload record's product_ids length as a
    # cheap O(1) proxy.
    #
    # Phase 35-stale — only count jobs whose updated_at is recent
    # enough to be plausibly alive. Stale jobs (worker died, never
    # transitioned) used to block every subsequent creation; now
    # they're invisible to the guard AND we opportunistically clean
    # them up below.
    will_be_heavy = (
        len(upload_record.product_ids) >= _HEAVY_CLASSIFICATION_THRESHOLD
    )
    if will_be_heavy:
        # Phase 36A — guard uses the ACTIVE window (2 min) not the
        # stale window (30 min). A paused job whose last advance is
        # 5 min old is resumable (same-upload short-circuit above
        # would have caught it) but does NOT block other heavy jobs
        # on other uploads / orgs.
        try:
            active_heavy_class = (
                store.count_active_heavy_classification_jobs(
                    min_total_products=_HEAVY_CLASSIFICATION_THRESHOLD,
                    max_age_minutes=_HEAVY_JOB_ACTIVE_MINUTES,
                )
            )
            active_heavy_ingest = store.count_active_heavy_ingestion_jobs(
                min_total_rows=_HEAVY_INGESTION_THRESHOLD,
                max_age_minutes=_HEAVY_JOB_ACTIVE_MINUTES,
            )
        except Exception:
            active_heavy_class = active_heavy_ingest = 0
        active_heavy_total = active_heavy_class + active_heavy_ingest

        # Phase 35-stale — opportunistic self-heal. If the recent-
        # only count is zero but the unfiltered count is non-zero,
        # we have stale ghosts; clean them up so future polls don't
        # hit the same edge case.
        if active_heavy_total == 0:
            try:
                healed_class = store.cancel_stale_classification_jobs(
                    stale_after_minutes=_HEAVY_JOB_STALE_MINUTES
                )
                healed_ingest = store.cancel_stale_ingestion_jobs(
                    stale_after_minutes=_HEAVY_JOB_STALE_MINUTES
                )
                if healed_class + healed_ingest > 0:
                    import logging

                    logging.getLogger(
                        "altera_api.heavy_job_guard"
                    ).info(
                        "heavy_job_guard.self_heal "
                        "classification=%d ingestion=%d",
                        healed_class,
                        healed_ingest,
                    )
            except Exception:
                pass  # best-effort cleanup; never block the create

        if active_heavy_total > 0:
            import logging

            logging.getLogger("altera_api.heavy_job_guard").info(
                "heavy_job_guard.rejected scope=classification "
                "project_id=%s upload_id=%s active_class=%d active_ingest=%d",
                project.id,
                upload_id,
                active_heavy_class,
                active_heavy_ingest,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "heavy_job_in_progress",
                    "message": (
                        "Un traitement volumineux est actuellement en cours "
                        "sur la plateforme. Il peut provenir d'une autre "
                        "organisation. Réessayez dans quelques minutes — "
                        "un traitement en pause sur votre fichier reste "
                        "reprenable."
                    ),
                    "active_classification_jobs": active_heavy_class,
                    "active_ingestion_jobs": active_heavy_ingest,
                },
            )

    try:
        job = create_classification_job(
            store,
            organisation_id=auth.organisation_id,
            project_id=project.id,
            upload_id=upload_id,
            methodology=body.methodology,
            overwrite=body.overwrite,
            only_missing_or_failed=body.only_missing_or_failed,
            batch_size=body.batch_size,
            created_by=auth.user_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "classify_failed",
                "message": str(exc) or "could not create classification job",
            },
        ) from exc
    return _classification_job_response(job)


# Phase 35A — IMPORTANT: ``/active`` is a literal path segment and
# must be declared BEFORE ``/{job_id}``. FastAPI matches routes in
# declaration order; if ``/{job_id}`` came first, the framework
# would try to parse "active" as a UUID and 422 the request.
@api_router.get(
    "/projects/{project_id}/classification-jobs/active",
    response_model=ClassificationJobResponse,
)
def get_active_classification_job_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    upload_id: UUID,
    methodology: Methodology = Methodology.PROTEIN_TRACKER,
) -> ClassificationJobResponse:
    """Phase 35A — find the most-recent non-terminal classification
    job for the (upload, methodology) pair.

    The wizard calls this on Step 4 mount. If a job is returned,
    the wizard renders "Reprendre la classification" pointing at
    the existing job instead of creating a new one — preventing
    duplicate work after a network blip, page refresh, or Render
    restart.

    Returns 404 ``no_active_job`` when nothing is in flight. The
    frontend treats 404 as the "nothing to resume" signal and shows
    the regular "Lancer la classification IA" button.
    """
    job = store.find_active_classification_job(
        upload_id=upload_id, methodology=methodology
    )
    if job is None or job.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "no_active_job",
                "message": (
                    f"no in-flight classification job for upload "
                    f"{upload_id} + methodology {methodology.value}"
                ),
            },
        )
    return _classification_job_response(job)


@api_router.get(
    "/projects/{project_id}/classification-jobs/{job_id}",
    response_model=ClassificationJobResponse,
)
def get_classification_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> ClassificationJobResponse:
    """Pure read of a classification job's current state.

    No side effects — does NOT advance the job. Used when the wizard
    re-mounts mid-job and wants the latest persisted progress.
    """
    job = store.get_classification_job(job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "job_not_found",
                "message": f"classification job {job_id} not found",
            },
        )
    return _classification_job_response(job)


@api_router.post(
    "/projects/{project_id}/classification-jobs/{job_id}/advance",
    response_model=ClassificationJobResponse,
)
def advance_classification_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> ClassificationJobResponse:
    """Process the next batch and return updated state.

    Each call takes one batch (default 25 products) and at most ~10–20s
    even with retries. The browser polls this endpoint until the
    response's ``status`` is terminal.

    Phase 35D — emits one structured log line per advance call so
    Render Pro logs can attribute slow batches to the right
    (org, project, upload). The log includes pre/post processed
    counts so a stuck-at-N pattern is immediately visible.
    """
    import logging
    import time

    from altera_api.ai.config import get_ai_provider
    from altera_api.api.classification_job_orchestrator import (
        advance_classification_job,
    )

    log = logging.getLogger("altera_api.classification_advance")
    existing = store.get_classification_job(job_id)
    if existing is None or existing.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "job_not_found",
                "message": f"classification job {job_id} not found",
            },
        )
    try:
        ai_provider = get_ai_provider()
    except ValueError:
        ai_provider = None
    from altera_api.api.classification_job_orchestrator import (
        ClassificationJobConflict,
    )

    t0 = time.perf_counter()
    try:
        job = advance_classification_job(
            store, job_id, ai_provider=ai_provider
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.info(
            "advance.ok job_id=%s org=%s project=%s upload=%s "
            "batch_size=%d processed=%d→%d total=%d failed=%d "
            "retry_batches=%d recovered=%d status=%s duration_ms=%.1f",
            job_id,
            existing.organisation_id,
            project.id,
            existing.upload_id,
            existing.batch_size,
            existing.processed_products,
            job.processed_products,
            job.total_products,
            job.failed_total,
            job.retry_batches,
            job.recovered_rows,
            job.status.value,
            elapsed_ms,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "job_not_found", "message": str(exc)},
        ) from exc
    except ClassificationJobConflict as exc:
        # Two-tab race or rapid double-click — surface as 409 so the
        # wizard can back off briefly before retrying its poll.
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "classification_job_conflict",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001
        # The orchestrator itself catches provider errors per-batch.
        # An exception escaping here means a true programming bug.
        # Return structured 502 so the wizard surfaces a clean banner.
        elapsed_ms = (time.perf_counter() - t0) * 1000
        log.exception(
            "advance.failed job_id=%s project=%s elapsed_ms=%.1f exc=%s: %s",
            job_id,
            project.id,
            elapsed_ms,
            type(exc).__name__,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "advance_failed",
                "message": str(exc) or "advance crashed",
            },
        ) from exc
    return _classification_job_response(job)


@api_router.post(
    "/projects/{project_id}/classification-jobs/{job_id}/cancel",
    response_model=ClassificationJobResponse,
)
def cancel_classification_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> ClassificationJobResponse:
    from altera_api.api.classification_job_orchestrator import (
        cancel_classification_job,
    )

    existing = store.get_classification_job(job_id)
    if existing is None or existing.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "job_not_found",
                "message": f"classification job {job_id} not found",
            },
        )
    job = cancel_classification_job(store, job_id)
    return _classification_job_response(job)


@api_router.post(
    "/projects/{project_id}/classification-jobs/{job_id}/retry-failed",
    response_model=ClassificationJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_failed_classification_job_route(
    job_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: AuthContext = Depends(authed_user),
) -> ClassificationJobResponse:
    """Create a fresh job whose pending list is the prior failures."""
    from altera_api.api.classification_job_orchestrator import (
        retry_failed_in_classification_job,
    )

    existing = store.get_classification_job(job_id)
    if existing is None or existing.project_id != project.id:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "job_not_found",
                "message": f"classification job {job_id} not found",
            },
        )
    job = retry_failed_in_classification_job(
        store, job_id, created_by=auth.user_id
    )
    return _classification_job_response(job)


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
class ReviewItemResponse(BaseModel):
    product_id: UUID
    upload_id: UUID | None
    external_product_id: str
    product_name: str
    brand: str | None
    methodology: str
    status: str
    reason: str
    queued_at: str
    current_category: str | None
    # confidence: 1.0 for deterministic matches, <1 for AI-classified items,
    # None when the item was never classified (e.g. parse-failed before AI ran).
    confidence: float | None
    # Phase 19B — safe classification rationale (no commercial fields)
    source: str | None = None
    rule_id: str | None = None
    ai_model: str | None = None
    ai_prompt_version: str | None = None
    rationale_notes: list[str] = []
    # Phase 19D — lock and assignment (no commercial fields)
    locked_by_user_id: UUID | None = None
    locked_by_email: str | None = None
    locked_at: str | None = None
    lock_expires_at: str | None = None
    lock_status: str = "unlocked"
    assigned_to_user_id: UUID | None = None
    assigned_to_email: str | None = None
    # Phase 19E — priority (derived from queue reason; no commercial fields)
    priority_level: str = "low"
    priority_reasons: list[str] = []
    # Excluded intentionally: items_purchased, items_sold, weight_per_item_kg,
    # revenue, margin, supplier terms — all commercial fields.


class WWFCorrectionPayload(BaseModel):
    """Phase WWF-O — explicit WWF correction payload.

    Lets the reviewer pin every WWF field directly instead of relying
    on the orchestrator's safe-default fallback (``_build_wwf_target``).
    Domain invariants are enforced by ``WWFProductClassification``'s
    Pydantic validators when the route assembles the target; a
    malformed combination yields a 400.
    """

    wwf_food_group: str
    wwf_is_composite: bool = False
    fg1_subgroup: str | None = None
    fg2_subgroup: str | None = None
    fg3_subgroup: str | None = None
    fg5_grain_kind: str | None = None
    fg7_snack_kind: str | None = None
    composite_step1_bucket: str | None = None
    confidence: float | None = None


class DecisionRequest(BaseModel):
    decision: Literal["accepted", "changed", "deferred"]
    to_category: str | None = None
    reason: str | None = None
    # Phase WWF-O — explicit WWF correction. Takes precedence over
    # ``to_category`` when supplied (methodology must be wwf).
    wwf: WWFCorrectionPayload | None = None


def _review_response(v: object) -> ReviewItemResponse:
    """Serialise a ReviewItemView → ReviewItemResponse."""
    from altera_api.api.orchestrator import ReviewItemView

    assert isinstance(v, ReviewItemView)
    return ReviewItemResponse(
        product_id=v.product_id,
        upload_id=v.upload_id,
        external_product_id=v.external_product_id,
        product_name=v.product_name,
        brand=v.brand,
        methodology=v.methodology.value,
        status=v.status.value,
        reason=v.reason.value,
        queued_at=v.queued_at.isoformat(),
        current_category=v.current_category,
        confidence=float(v.confidence) if v.confidence is not None else None,
        source=v.source.value if v.source is not None else None,
        rule_id=v.rule_id,
        ai_model=v.ai_model,
        ai_prompt_version=v.ai_prompt_version,
        rationale_notes=list(v.rationale_notes),
        locked_by_user_id=v.locked_by_user_id,
        locked_by_email=v.locked_by_email,
        locked_at=v.locked_at.isoformat() if v.locked_at is not None else None,
        lock_expires_at=v.lock_expires_at.isoformat() if v.lock_expires_at is not None else None,
        lock_status=v.lock_status,
        assigned_to_user_id=v.assigned_to_user_id,
        assigned_to_email=v.assigned_to_email,
        priority_level=v.priority_level,
        priority_reasons=list(v.priority_reasons),
    )


@api_router.get(
    "/projects/{project_id}/review",
    response_model=Page[ReviewItemResponse],
)
def list_review_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    pagination: Annotated[PaginationParams, Depends()],
    methodology: Methodology | None = None,
    status: ManualReviewStatus | None = None,
    reason: ManualReviewQueueReason | None = None,
    priority_level: ManualReviewPriority | None = None,
    upload_id: UUID | None = None,
    product_search: str | None = None,
    sort: Literal["oldest", "newest", "priority"] = "oldest",
) -> Page[ReviewItemResponse]:
    """List review items for a project with optional filtering and sorting.

    Filters: methodology, status, reason, priority_level, upload_id,
    product_search (name or external_product_id substring, case-insensitive).

    Sort: oldest (default) | newest | priority (critical first).

    Pagination: limit (default 50, max 200) and offset (default 0).

    # TODO(Phase 29B): add rate limiting — this endpoint is called on every
    # reviewer page load and during active review sessions.
    """
    views = list_review(
        store,
        project=project,
        methodology=methodology,
        status=status,
        reason=reason,
        priority_level=priority_level,
        upload_id=upload_id,
        product_search=product_search,
        sort=sort,
        viewer_user_id=auth.user_id if auth.can_review else None,
    )
    all_items = [_review_response(v) for v in views]
    return paginate(all_items, pagination)


# ---------------------------------------------------------------------------
# Phase 34F — paginated classifications endpoint
# ---------------------------------------------------------------------------


class ClassificationRow(BaseModel):
    """One row in the wizard's category validation table.

    Carries only non-commercial fields (the same allowlist used for AI
    payloads — product_name, brand, retailer_category/subcategory).
    Commercial fields (weight, volume, prices, margins) are deliberately
    NOT included in this response.
    """

    product_id: UUID
    product_name: str
    brand: str | None
    retailer_category: str | None
    retailer_subcategory: str | None
    # Protein Tracker
    pt_group: str | None
    pt_source: str | None             # "deterministic" | "ai" | "manual_review"
    pt_confidence: float | None
    pt_rule_id: str | None
    pt_ai_model: str | None
    # WWF (null when WWF not enabled on the project)
    wwf_food_group: str | None
    wwf_source: str | None
    wwf_confidence: float | None
    # Phase WWF-I — full WWF subgroup + composite payload so the
    # WWF validation view can render food group + subgroup +
    # composite bucket + confidence without a second round-trip.
    wwf_fg1_subgroup: str | None = None
    wwf_fg2_subgroup: str | None = None
    wwf_fg3_subgroup: str | None = None
    wwf_fg5_grain_kind: str | None = None
    wwf_fg7_snack_kind: str | None = None
    wwf_is_composite: bool | None = None
    wwf_composite_step1_bucket: str | None = None
    wwf_rule_id: str | None = None
    # Review state
    review_status: str | None         # "in_queue" | "reviewing" | "accepted" |
                                      #  "changed" | "deferred" | null
    # Phase WWF-N — which methodology this row's review/decision is
    # about. ``None`` for legacy "all products" rows (one row per
    # product). ``"protein_tracker"`` or ``"wwf"`` when the row was
    # emitted by ``view=review`` (one row per ``(product, methodology)``
    # review item).
    methodology: str | None = None
    # Phase WWF-N — WWF review state (separate from PT review_status).
    wwf_review_status: str | None = None


class ClassificationsResponse(BaseModel):
    items: list[ClassificationRow]
    total: int
    # Aggregate counters so the wizard can show "153 by deterministic /
    # 78 by AI / 5 manual / 0 unknown" without re-paginating the entire
    # list. These are computed over the FILTERED set, not the global set,
    # so they update when the user applies a filter.
    counts_by_source: dict[str, int]
    counts_by_pt_group: dict[str, int]
    pt_eligible_total: int            # products with PT methodology enabled
    # Phase WWF-R — global review-queue totals (open statuses
    # ``in_queue`` + ``reviewing``) per methodology. Computed from the
    # review queues already fetched above, so no extra DB round-trip.
    # The product-mode side-by-side table uses these for the top
    # "Total à valider" banner; the values match the row count of
    # ``view=review`` with the matching methodology filter.
    pt_review_total: int = 0
    wwf_review_total: int = 0


@api_router.get(
    "/projects/{project_id}/classifications",
    response_model=ClassificationsResponse,
)
def list_classifications_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    pagination: Annotated[PaginationParams, Depends()],
    source: Literal["deterministic", "ai", "manual_review", "unknown"] | None = None,
    pt_group: str | None = None,
    min_confidence: float | None = None,
    max_confidence: float | None = None,
    review_status: ManualReviewStatus | None = None,
    product_search: str | None = None,
    # Phase WWF-N — new view modes.
    view: Literal["products", "review"] = "products",
    methodology: Literal["protein_tracker", "wwf"] | None = None,
) -> ClassificationsResponse:
    """Paginated category validation table for the wizard.

    Used by Step 5 (validation) to let the analyst see every product's
    assigned Protein Tracker / WWF category, the source (rule / AI /
    manual), confidence, and current review status.

    Phase 36B — kills the N+1 that made the table 60–90s slow on
    1050-row projects. Previously the route looped over every product
    and called ``get_pt_classification`` (+ ``get_wwf_classification``
    when WWF was enabled) per id — 1050–2100 PostgREST round trips
    at ~40ms each before serialisation. Now we bulk-fetch
    classifications once, project to lightweight dicts for filtering
    + counting, and only materialise ``ClassificationRow`` Pydantic
    objects for the requested page.

    Filters apply in conjunction (AND). ``product_search`` is a
    case-insensitive substring match on product_name or brand.
    """
    import logging
    import time

    t_total = time.perf_counter()
    pt_enabled = Methodology.PROTEIN_TRACKER in project.methodologies_enabled
    wwf_enabled = Methodology.WWF in project.methodologies_enabled

    t0 = time.perf_counter()
    products = store.list_products_for_project(project.id)
    products_ms = (time.perf_counter() - t0) * 1000

    product_ids = [p.id for p in products]

    t0 = time.perf_counter()
    pt_map = (
        store.get_pt_classifications_bulk(product_ids)
        if pt_enabled and product_ids
        else {}
    )
    wwf_map = (
        store.get_wwf_classifications_bulk(product_ids)
        if wwf_enabled and product_ids
        else {}
    )
    classifications_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    # PT review queue. ``list_review_items_for_project`` already
    # chunks the IN() filter at 200 ids/URL — Phase 34Z-fix.
    review_items_pt = (
        {
            item.product_id: item
            for item in store.list_review_items_for_project(
                project.id, methodology=Methodology.PROTEIN_TRACKER
            )
        }
        if pt_enabled
        else {}
    )
    # Phase WWF-N — also fetch the WWF review queue so the unified
    # validation table can render per-methodology review rows.
    review_items_wwf = (
        {
            item.product_id: item
            for item in store.list_review_items_for_project(
                project.id, methodology=Methodology.WWF
            )
        }
        if wwf_enabled
        else {}
    )
    review_ms = (time.perf_counter() - t0) * 1000

    # Phase 36B — build a lightweight projection dict per product
    # carrying ONLY the fields needed for filtering + counting.
    # Avoids 1000+ Pydantic ``ClassificationRow`` instantiations when
    # the caller only wants page 1 of 50.
    t0 = time.perf_counter()
    pt_eligible_total = 0
    projections: list[dict[str, Any]] = []
    for product in products:
        is_pt_eligible = (
            pt_enabled
            and Methodology.PROTEIN_TRACKER in product.methodologies_enabled
        )
        if is_pt_eligible:
            pt_eligible_total += 1
        pt = pt_map.get(product.id) if pt_enabled else None
        wwf = wwf_map.get(product.id) if wwf_enabled else None
        review_item = review_items_pt.get(product.id)
        wwf_review_item = review_items_wwf.get(product.id)
        projections.append(
            {
                "product": product,
                "pt_group": pt.pt_group.value if pt is not None else None,
                "pt_source": pt.source.value if pt is not None else None,
                "pt_confidence": (
                    float(pt.confidence) if pt is not None else None
                ),
                "pt_rule_id": pt.rule_id if pt is not None else None,
                "pt_ai_model": pt.ai_model if pt is not None else None,
                "wwf": wwf,
                "review_status": (
                    review_item.status.value
                    if review_item is not None
                    else None
                ),
                # Phase WWF-N — methodology-specific WWF review state.
                "wwf_review_status": (
                    wwf_review_item.status.value
                    if wwf_review_item is not None
                    else None
                ),
            }
        )

    def _keep(proj: dict[str, Any]) -> bool:
        if source is not None:
            if source == "unknown":
                if proj["pt_source"] is not None:
                    return False
            elif proj["pt_source"] != source:
                return False
        if pt_group is not None and proj["pt_group"] != pt_group:
            return False
        if min_confidence is not None:
            c = proj["pt_confidence"]
            if c is None or c < min_confidence:
                return False
        if max_confidence is not None:
            c = proj["pt_confidence"]
            if c is None or c > max_confidence:
                return False
        if review_status is not None:
            if proj["review_status"] != review_status.value:
                return False
        if product_search:
            q = product_search.lower()
            product = proj["product"]
            hay = (product.product_name + " " + (product.brand or "")).lower()
            if q not in hay:
                return False
        return True

    filtered = [p for p in projections if _keep(p)]

    counts_by_source: dict[str, int] = {}
    counts_by_pt_group: dict[str, int] = {}
    for proj in filtered:
        key = proj["pt_source"] or "unknown"
        counts_by_source[key] = counts_by_source.get(key, 0) + 1
        if proj["pt_group"] is not None:
            counts_by_pt_group[proj["pt_group"]] = (
                counts_by_pt_group.get(proj["pt_group"], 0) + 1
            )
    counts_ms = (time.perf_counter() - t0) * 1000

    # Phase WWF-N — pivot into per-(product, methodology) rows when
    # ``view=review``. A product with both PT and WWF in review
    # produces TWO rows. ``methodology=protein_tracker|wwf`` further
    # filters the result.
    open_statuses = {
        ManualReviewStatus.IN_QUEUE.value,
        ManualReviewStatus.REVIEWING.value,
    }
    if view == "review":
        review_projections: list[dict[str, Any]] = []
        for proj in filtered:
            if (
                methodology in (None, "protein_tracker")
                and proj["review_status"] in open_statuses
            ):
                review_projections.append({**proj, "_methodology": "protein_tracker"})
            if (
                methodology in (None, "wwf")
                and proj["wwf_review_status"] in open_statuses
            ):
                review_projections.append({**proj, "_methodology": "wwf"})
        page_source = review_projections
    else:
        # view=products — optionally filter to methodology-eligible rows.
        if methodology == "protein_tracker":
            page_source = [p for p in filtered if p["pt_group"] is not None]
        elif methodology == "wwf":
            page_source = [p for p in filtered if p["wwf"] is not None]
        else:
            page_source = filtered

    # Phase 36B — materialise Pydantic rows only for the page.
    t0 = time.perf_counter()
    page_projections = page_source[
        pagination.offset : pagination.offset + pagination.limit
    ]
    items: list[ClassificationRow] = []
    for proj in page_projections:
        product = proj["product"]
        wwf = proj["wwf"]
        items.append(
            ClassificationRow(
                product_id=product.id,
                product_name=product.product_name,
                brand=product.brand,
                retailer_category=product.retailer_category,
                retailer_subcategory=product.retailer_subcategory,
                pt_group=proj["pt_group"],
                pt_source=proj["pt_source"],
                pt_confidence=proj["pt_confidence"],
                pt_rule_id=proj["pt_rule_id"],
                pt_ai_model=proj["pt_ai_model"],
                wwf_food_group=(
                    wwf.wwf_food_group.value if wwf is not None else None
                ),
                wwf_source=(
                    wwf.source.value if wwf is not None else None
                ),
                wwf_confidence=(
                    float(wwf.confidence) if wwf is not None else None
                ),
                # Phase WWF-I — WWF subgroup + composite fields for
                # the WWF validation view. ``None`` when the row
                # has no WWF classification or the subgroup doesn't
                # apply to the current food group.
                wwf_fg1_subgroup=(
                    wwf.fg1_subgroup.value
                    if wwf is not None and wwf.fg1_subgroup is not None
                    else None
                ),
                wwf_fg2_subgroup=(
                    wwf.fg2_subgroup.value
                    if wwf is not None and wwf.fg2_subgroup is not None
                    else None
                ),
                wwf_fg3_subgroup=(
                    wwf.fg3_subgroup.value
                    if wwf is not None and wwf.fg3_subgroup is not None
                    else None
                ),
                wwf_fg5_grain_kind=(
                    wwf.fg5_grain_kind.value
                    if wwf is not None and wwf.fg5_grain_kind is not None
                    else None
                ),
                wwf_fg7_snack_kind=(
                    wwf.fg7_snack_kind.value
                    if wwf is not None and wwf.fg7_snack_kind is not None
                    else None
                ),
                wwf_is_composite=(
                    wwf.wwf_is_composite if wwf is not None else None
                ),
                wwf_composite_step1_bucket=(
                    wwf.composite_step1_bucket.value
                    if wwf is not None and wwf.composite_step1_bucket is not None
                    else None
                ),
                wwf_rule_id=(
                    wwf.rule_id if wwf is not None else None
                ),
                review_status=proj["review_status"],
                # Phase WWF-N — methodology + wwf_review_status so the
                # frontend can render per-(product, methodology) review
                # rows and accept/correct each methodology separately.
                methodology=proj.get("_methodology"),
                wwf_review_status=proj["wwf_review_status"],
            )
        )
    serialize_ms = (time.perf_counter() - t0) * 1000

    total_ms = (time.perf_counter() - t_total) * 1000
    logging.getLogger("altera_api.classification_table").info(
        "classification_table.timing project_id=%s limit=%d offset=%d "
        "n_products=%d products_ms=%.1f classifications_ms=%.1f "
        "review_ms=%.1f counts_ms=%.1f serialize_ms=%.1f total_ms=%.1f "
        "rows_returned=%d total_filtered=%d",
        project.id,
        pagination.limit,
        pagination.offset,
        len(products),
        products_ms,
        classifications_ms,
        review_ms,
        counts_ms,
        serialize_ms,
        total_ms,
        len(items),
        len(filtered),
    )
    # Phase WWF-R — global review-queue totals so the product-mode
    # side-by-side table can show "PT à vérifier: X · WWF à vérifier:
    # Y" without an extra round-trip. Counts the open statuses
    # (``in_queue`` + ``reviewing``) across the project's queues we
    # already fetched above.
    pt_review_total = sum(
        1
        for item in review_items_pt.values()
        if item.status.value in open_statuses
    )
    wwf_review_total = sum(
        1
        for item in review_items_wwf.values()
        if item.status.value in open_statuses
    )
    return ClassificationsResponse(
        items=items,
        # Phase WWF-N — ``total`` reflects the page-source size so the
        # frontend pagination matches the page-row count even in
        # ``view=review`` mode (where a product can produce two rows).
        total=len(page_source),
        counts_by_source=counts_by_source,
        counts_by_pt_group=counts_by_pt_group,
        pt_eligible_total=pt_eligible_total,
        pt_review_total=pt_review_total,
        wwf_review_total=wwf_review_total,
    )


# ---------------------------------------------------------------------------
# Phase 34L — nutrition validation table
# ---------------------------------------------------------------------------


class NutritionValidationRow(BaseModel):
    """One row in the wizard's nutrition validation table.

    Surfaces every PT-eligible product's protein attribution state:
    where the protein values come from, whether a split exists, and
    what action (if any) the user must take before calculation. Only
    non-commercial fields are exposed.
    """

    product_id: UUID
    product_name: str
    pt_group: str | None
    protein_pct: str | None              # final value used for calc
    plant_protein_pct: str | None
    animal_protein_pct: str | None
    retailer_protein_pct: str | None     # original CSV value if any
    source: str                          # retailer_csv | nevo | ciqual | manual | missing
    match_method: str | None             # deterministic | ai_assisted | manual | none
    split_source: str                    # nevo_official_split | classification_assumption
                                          # | manual | missing
    confidence: float | None
    reference_name: str | None
    reference_code: str | None
    status: str                          # ready | needs_review | missing | excluded
    reason: str | None                   # short rationale for missing/needs_review
    # Quality-V2-AB — human-friendly matched-reference label for V2-applied
    # records (e.g. "NEVO V2: Muesli w fruit/seeds"). Optional + backward
    # compatible: None for V1/retailer/manual rows (frontend falls back to
    # ``reason``). Derived from ``source_metadata``; never affects values.
    source_display_label: str | None = None


class NutritionValidationsResponse(BaseModel):
    items: list[NutritionValidationRow]
    total: int
    counts_by_status: dict[str, int]
    counts_by_source: dict[str, int]


def _v2_display_from_metadata(record: Any) -> dict[str, Any] | None:
    """Quality-V2-AB — surface the approved NEVO food for a V2-applied record.

    Reads ``source_metadata`` (works pre- and post-display-backfill): prefers
    an explicit ``display_label``, else the matched NEVO food name/code under
    any of the known keys. Returns None for non-V2 / no-metadata records so V1
    behaviour is unchanged."""
    md = getattr(record, "source_metadata", None)
    if not isinstance(md, dict):
        return None
    name = (md.get("nevo_food_name") or md.get("parent_nevo_food_name")
            or md.get("approved_nevo_name"))
    code = (md.get("nevo_code") or md.get("parent_nevo_code")
            or md.get("approved_nevo_code"))
    label = md.get("display_label")
    if not label and name:
        label = f"NEVO V2: {name}"
    if not (label or name):
        return None
    return {"reference_name": name or None, "reference_code": code or None,
            "display_label": label or None}


def _nutrition_row_fields(
    product: Any,
    classification: Any,
    records: list[Any],
) -> dict[str, Any]:
    """Phase 36F — pure computation of a nutrition validation row.

    Extracted from ``_nutrition_row_for`` so the list endpoint can
    feed pre-fetched classification + records (via bulk lookups)
    instead of triggering N+1 ``get_pt_classification`` /
    ``get_enrichment_records_for_product`` calls per product.

    Returns the row as a dict — callers wrap it in
    ``NutritionValidationRow`` only when they actually need the
    Pydantic object (for serialisation). The filter + count pass
    works directly on the dict to avoid 1050 Pydantic
    instantiations for one page of 50.
    """
    from altera_api.domain.enrichment import (
        NutritionEnrichmentSource as _NES,
    )
    from altera_api.domain.enrichment import (
        NutritionEnrichmentStatus as _NSt,
    )
    pt_group = classification.pt_group.value if classification is not None else None
    retailer_pct = (
        str(product.pt_fields.protein_pct)
        if product.pt_fields is not None and product.pt_fields.protein_pct is not None
        else None
    )

    # Phase Hotfix-Validation — when multiple ENRICHED records exist
    # for the same nutrient (NEVO matched first, then user overrode
    # manually), the read path must prefer the manual override.
    # Previous behaviour picked the FIRST record from the underlying
    # list, which is insertion-order in both InMemoryStore and the
    # un-ordered Postgres query — manual overrides were silently
    # ignored on reload. We now sort by source priority
    # (MANUAL_ALTERA > NEVO > CIQUAL > anything else), then by
    # ``created_at`` desc as a tiebreaker.
    def _record_priority(r: Any) -> tuple[int, datetime]:
        # Lower priority number wins.
        source_rank = {
            _NES.MANUAL_ALTERA: 0,
            _NES.NEVO: 1,
            _NES.CIQUAL: 2,
        }.get(r.source, 3)
        # ``created_at`` is a datetime; we sort descending so newest
        # wins within the same source bucket.
        return (source_rank, -r.created_at.timestamp())

    def _pick(nutrient: str) -> Any | None:
        candidates = [
            r
            for r in records
            if r.nutrient == nutrient
            and r.status is _NSt.ENRICHED
            and r.enriched_value is not None
        ]
        if not candidates:
            return None
        candidates.sort(key=_record_priority)
        return candidates[0]

    protein_rec = _pick("protein_pct")
    plant_rec = _pick("plant_protein_pct")
    animal_rec = _pick("animal_protein_pct")

    # Decide source / status. Retailer-provided values are always
    # preferred over enrichment.
    source = "missing"
    match_method: str | None = None
    split_source = "missing"
    confidence: float | None = None
    reference_name: str | None = None
    reference_code: str | None = None
    source_display_label: str | None = None
    status = "missing"
    reason: str | None = None
    final_protein = retailer_pct
    final_plant: str | None = None
    final_animal: str | None = None

    if retailer_pct is not None:
        source = "retailer_csv"
        status = "ready"
        # Retailer values for plant/animal if provided.
        if product.pt_fields is not None:
            if product.pt_fields.plant_protein_pct is not None:
                final_plant = str(product.pt_fields.plant_protein_pct)
            if product.pt_fields.animal_protein_pct is not None:
                final_animal = str(product.pt_fields.animal_protein_pct)
        split_source = (
            "retailer_csv"
            if final_plant is not None and final_animal is not None
            else "missing"
        )
    elif protein_rec is not None:
        final_protein = str(protein_rec.enriched_value)
        source = (
            "nevo"
            if protein_rec.source is _NES.NEVO
            else "ciqual"
            if protein_rec.source is _NES.CIQUAL
            else "manual"
            if protein_rec.source is _NES.MANUAL_ALTERA
            else "ciqual"
        )
        match_method = protein_rec.match_method
        confidence = float(protein_rec.confidence) if protein_rec.confidence else None
        # Quality-V2-AB — for V2-applied records, surface the matched NEVO food
        # name/code + a friendly label instead of the generic apply rationale.
        _display = _v2_display_from_metadata(protein_rec)
        if _display is not None:
            reference_name = _display["reference_name"]
            reference_code = _display["reference_code"]
            source_display_label = _display["display_label"]
        # Try to extract the reference name from the rationale (fallback).
        if protein_rec.rationale:
            reason = protein_rec.rationale[:200]
        if plant_rec is not None and animal_rec is not None:
            final_plant = str(plant_rec.enriched_value)
            final_animal = str(animal_rec.enriched_value)
            split_source = (
                "classification_assumption"
                if plant_rec.rationale
                and "classification_assumption" in plant_rec.rationale
                else "nevo_official_split"
                if protein_rec.source is _NES.NEVO
                else "missing"
            )
            # Phase 34M — tier status by confidence so the wizard's
            # nutrition validation table can distinguish high-
            # confidence ready rows from low-confidence suggestions
            # that the user must accept or override.
            if confidence is None or confidence >= 0.85:
                status = "ready"
            elif confidence >= 0.70:
                status = "ready_medium_confidence"
            elif confidence >= 0.50:
                status = "needs_review_low_confidence"
            elif confidence >= 0.30:
                status = "suggested_very_low_confidence"
            else:
                status = "needs_review"
        else:
            split_source = "missing"
            status = "needs_review"
            reason = (
                "Composite ou catégorie ambiguë — split plant/animal manquant"
                if pt_group in ("composite_products", "unknown", None)
                else reason
            )
    else:
        # No retailer, no enrichment.
        reason = (
            "Aucune correspondance NEVO trouvée pour ce produit"
            if classification is not None
            else "Produit non classifié"
        )
    return {
        "product_id": product.id,
        "product_name": product.product_name,
        "pt_group": pt_group,
        "protein_pct": final_protein,
        "plant_protein_pct": final_plant,
        "animal_protein_pct": final_animal,
        "retailer_protein_pct": retailer_pct,
        "source": source,
        "match_method": match_method,
        "split_source": split_source,
        "confidence": confidence,
        "reference_name": reference_name,
        "reference_code": reference_code,
        "status": status,
        "reason": reason,
        "source_display_label": source_display_label,
    }


def _nutrition_row_for(
    store: StoreProtocol,
    product: Any,
    *,
    classification: Any = None,
    records: list[Any] | None = None,
) -> NutritionValidationRow:
    """Build one validation row for a PT product.

    Phase 36F — accepts pre-fetched ``classification`` + ``records``
    so the list endpoint can drive the per-row computation off of
    bulk lookups. When the caller doesn't pass them in (e.g. the
    manual-nutrition single-product route) we fall back to the
    original point lookups so the endpoint behaviour is unchanged.
    """
    if classification is None:
        classification = store.get_pt_classification(product.id)
    if records is None:
        records = store.get_enrichment_records_for_product(product.id)
    return NutritionValidationRow(
        **_nutrition_row_fields(product, classification, records)
    )


@api_router.get(
    "/projects/{project_id}/nutrition-validations",
    response_model=NutritionValidationsResponse,
)
def list_nutrition_validations_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    pagination: Annotated[PaginationParams, Depends()],
    status: Literal[
        "ready",
        "ready_medium_confidence",
        "needs_review",
        "needs_review_low_confidence",
        "suggested_very_low_confidence",
        "missing",
        "excluded",
    ] | None = None,
    source: Literal["retailer_csv", "nevo", "ciqual", "manual", "missing"] | None = None,
    product_search: str | None = None,
) -> NutritionValidationsResponse:
    """Paginated nutrition validation table (Phase 34L).

    One row per PT-eligible product showing the final protein values
    that would be used in the calculation plus their provenance. Used
    by the wizard's Step 6 to let the user inspect what NEVO produced
    before allowing the calculation to run.

    Phase 36F — kills the N+1 that made this endpoint 60–90s on
    1050-product projects. Previously every row paid two PostgREST
    point lookups (``get_pt_classification`` +
    ``get_enrichment_records_for_product``) AND was wrapped in a full
    Pydantic ``NutritionValidationRow`` even when the caller wanted
    a single page of 50. Now we bulk-fetch both tables once, filter
    + count on lightweight dicts, and only materialise Pydantic rows
    for the requested page — exactly the Phase 36B pattern.
    """
    import logging
    import time

    t_total = time.perf_counter()
    t0 = time.perf_counter()
    products = [
        p for p in store.list_products_for_project(project.id)
        if p.pt_fields is not None
        and Methodology.PROTEIN_TRACKER in p.methodologies_enabled
    ]
    products_ms = (time.perf_counter() - t0) * 1000

    product_ids = [p.id for p in products]
    t0 = time.perf_counter()
    classification_map = (
        store.get_pt_classifications_bulk(product_ids)
        if product_ids
        else {}
    )
    classifications_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    enrichment_map = (
        store.get_enrichment_records_bulk(product_ids)
        if product_ids
        else {}
    )
    enrichment_bulk_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    projections: list[dict[str, Any]] = []
    for p in products:
        fields = _nutrition_row_fields(
            p,
            classification_map.get(p.id),
            enrichment_map.get(p.id, []),
        )
        projections.append(fields)

    def _keep(fields: dict[str, Any]) -> bool:
        if status is not None and fields["status"] != status:
            return False
        if source is not None and fields["source"] != source:
            return False
        if product_search:
            q = product_search.lower()
            if q not in fields["product_name"].lower():
                return False
        return True

    filtered = [p for p in projections if _keep(p)]
    counts_by_status: dict[str, int] = {}
    counts_by_source: dict[str, int] = {}
    for fields in filtered:
        counts_by_status[fields["status"]] = (
            counts_by_status.get(fields["status"], 0) + 1
        )
        counts_by_source[fields["source"]] = (
            counts_by_source.get(fields["source"], 0) + 1
        )
    counts_ms = (time.perf_counter() - t0) * 1000

    # Phase 36F — materialise Pydantic rows only for the page.
    t0 = time.perf_counter()
    page_fields = filtered[
        pagination.offset : pagination.offset + pagination.limit
    ]
    items = [NutritionValidationRow(**fields) for fields in page_fields]
    serialize_ms = (time.perf_counter() - t0) * 1000

    total_ms = (time.perf_counter() - t_total) * 1000
    logging.getLogger("altera_api.nutrition_table").info(
        "nutrition_table.timing project_id=%s limit=%d offset=%d "
        "total_products=%d products_ms=%.1f classifications_ms=%.1f "
        "enrichment_bulk_ms=%.1f counts_ms=%.1f serialize_ms=%.1f "
        "total_ms=%.1f rows_returned=%d total_filtered=%d",
        project.id,
        pagination.limit,
        pagination.offset,
        len(products),
        products_ms,
        classifications_ms,
        enrichment_bulk_ms,
        counts_ms,
        serialize_ms,
        total_ms,
        len(items),
        len(filtered),
    )
    return NutritionValidationsResponse(
        items=items,
        total=len(filtered),
        counts_by_status=counts_by_status,
        counts_by_source=counts_by_source,
    )


# ---------------------------------------------------------------------------
# Phase 34N — calculation preflight diagnostic
# ---------------------------------------------------------------------------


class CalculationPreflightResponse(BaseModel):
    """Per-product breakdown of why each row will or will not be in the
    next calculation run. Lets the wizard show non-contradictory
    readiness ("Lignes éligibles: N" matches the actual rows_count
    when the run is executed) and surface explicit exclusion reasons.
    """

    total_products: int
    classified_products: int
    products_with_volume: int
    products_with_weight: int
    products_with_total_protein: int
    products_with_plant_animal_split: int
    products_ready_for_calculation: int
    products_missing_nutrition: int
    products_missing_volume_or_weight: int
    products_missing_classification: int
    products_out_of_scope: int
    sample_exclusion_reasons: list[str]
    nevo_total_references: int
    nevo_attempted: bool
    # Phase Product-UX-A — which methodology this preflight reflects.
    # ``protein_tracker`` keeps the legacy nutrition-aware shape;
    # ``wwf`` reports WWF readiness (no nutrition requirement). The
    # frontend uses this to render methodology-appropriate copy.
    methodology: str = "protein_tracker"
    requires_nutrition: bool = True


def _wwf_calculation_preflight(
    store: StoreProtocol,
    project: Project,
) -> CalculationPreflightResponse:
    """Phase Product-UX-A — WWF calculation readiness.

    A WWF product is ready once it has a non-unknown WWF food group +
    a usable per-item weight. WWF never uses protein nutrition, so the
    nutrition counters are reported as zero and ``requires_nutrition``
    is False. Mirrors what ``calculate_wwf_run`` actually needs.
    """
    from altera_api.domain.wwf import WWFFoodGroup

    all_products = store.list_products_for_project(project.id)
    products = [
        p
        for p in all_products
        if p.wwf_fields is not None
        and Methodology.WWF in p.methodologies_enabled
    ]
    product_ids = [p.id for p in products]
    cls_map = (
        store.get_wwf_classifications_bulk(product_ids) if product_ids else {}
    )

    classified = 0
    with_weight = 0
    ready = 0
    missing_classification = 0
    missing_weight = 0
    out_of_scope = 0
    sample_reasons: list[str] = []

    def _sample(reason: str) -> None:
        if len(sample_reasons) < 10:
            sample_reasons.append(reason)

    for p in products:
        cls = cls_map.get(p.id)
        has_weight = p.weight_per_item_kg > 0
        if has_weight:
            with_weight += 1
        if cls is None:
            missing_classification += 1
            _sample(f"{p.product_name}: classification WWF manquante")
            continue
        classified += 1
        if cls.wwf_food_group is WWFFoodGroup.OUT_OF_SCOPE:
            out_of_scope += 1
        usable = cls.wwf_food_group.is_methodology_group
        if usable and has_weight:
            ready += 1
        else:
            parts = []
            if not usable:
                parts.append("classification WWF inconnue / hors périmètre")
            if not has_weight:
                parts.append("poids manquant")
                missing_weight += 1
            _sample(f"{p.product_name}: " + ", ".join(parts or ["non éligible"]))

    return CalculationPreflightResponse(
        total_products=len(products),
        classified_products=classified,
        # WWF uses sales volume + weight, not PT items_purchased; we
        # report weight coverage as the volume proxy so the frontend's
        # generic "volume/poids" condition is satisfiable.
        products_with_volume=with_weight,
        products_with_weight=with_weight,
        products_with_total_protein=0,
        products_with_plant_animal_split=0,
        products_ready_for_calculation=ready,
        products_missing_nutrition=0,
        products_missing_volume_or_weight=missing_weight,
        products_missing_classification=missing_classification,
        products_out_of_scope=out_of_scope,
        sample_exclusion_reasons=sample_reasons,
        nevo_total_references=0,
        nevo_attempted=False,
        methodology="wwf",
        requires_nutrition=False,
    )


@api_router.get(
    "/projects/{project_id}/calculation-preflight",
    response_model=CalculationPreflightResponse,
)
def calculation_preflight_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    methodology: Literal["protein_tracker", "wwf"] | None = None,
) -> CalculationPreflightResponse:
    """Phase 34N — single source of truth for what the next
    calculation will include.

    Phase Product-UX-A — methodology-aware. When ``methodology=wwf``
    (or omitted on a WWF-only project) the preflight reports WWF
    readiness: a product is ready once it has a non-unknown WWF food
    group + a usable weight. WWF never requires protein nutrition, so
    the nutrition counters are zero and ``requires_nutrition=False``.
    The default / ``protein_tracker`` path is unchanged.

    Walks each PT-eligible product and computes:
    - whether it has accepted classification
    - whether it has volume (items_purchased) and weight (per item)
    - whether protein_pct is resolved (retailer OR enrichment)
    - whether plant + animal split is resolved (retailer OR enrichment)
    - the explicit exclusion reason when it would NOT be in the run

    The aggregate counts here MUST match the rows_count the
    subsequent /runs call produces; they are computed by walking the
    same data the calculation engine walks. The wizard reads this to
    decide whether to enable the "Calculer sur les données
    disponibles" button.

    Phase 36C — kills the two N+1 patterns that made this endpoint
    40–46s on 1050-product projects:
      * ``store.get_pt_classification(p.id)`` per product
      * ``store.get_enrichment_records_for_product(p.id)`` per product

    Plus drops the ``list_nevo_entries()`` call (materialised 2300+
    Pydantic objects for ``len()``) in favour of ``count_nevo_entries``.
    """
    import logging
    import time

    # Phase Product-UX-A — resolve the effective methodology. When the
    # caller doesn't pass one, infer from the project: PT if enabled,
    # else WWF. This keeps PT-only + PT+WWF projects on the legacy PT
    # preflight by default while letting a WWF-only project work.
    pt_enabled = Methodology.PROTEIN_TRACKER in project.methodologies_enabled
    effective = methodology
    if effective is None:
        effective = "protein_tracker" if pt_enabled else "wwf"

    if effective == "wwf":
        return _wwf_calculation_preflight(store, project)

    t_total = time.perf_counter()
    t0 = time.perf_counter()
    all_products = store.list_products_for_project(project.id)
    products = [
        p for p in all_products
        if p.pt_fields is not None
        and Methodology.PROTEIN_TRACKER in p.methodologies_enabled
    ]
    list_products_ms = (time.perf_counter() - t0) * 1000

    product_ids = [p.id for p in products]
    t0 = time.perf_counter()
    classification_map = (
        store.get_pt_classifications_bulk(product_ids)
        if product_ids
        else {}
    )
    classifications_bulk_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    enrichment_map = (
        store.get_enrichment_records_bulk(product_ids)
        if product_ids
        else {}
    )
    enrichment_bulk_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    nevo_total = store.count_nevo_entries()
    nevo_refs_ms = (time.perf_counter() - t0) * 1000

    classified = 0
    with_volume = 0
    with_weight = 0
    with_protein = 0
    with_split = 0
    ready = 0
    missing_nutrition = 0
    missing_volume_weight = 0
    missing_classification = 0
    out_of_scope = 0
    sample_reasons: list[str] = []

    from altera_api.enrichment.selection import select_protein_enrichment

    def _sample(reason: str) -> None:
        if len(sample_reasons) < 10:
            sample_reasons.append(reason)

    nevo_attempted = False
    t0 = time.perf_counter()
    for p in products:
        classification = classification_map.get(p.id)
        pt = p.pt_fields
        assert pt is not None  # filtered above

        has_volume = pt.items_purchased is not None and pt.items_purchased > 0
        has_weight = p.weight_per_item_kg > 0
        if has_volume:
            with_volume += 1
        if has_weight:
            with_weight += 1

        if classification is None:
            missing_classification += 1
            _sample(
                f"{p.product_name}: missing classification"
            )
            continue
        classified += 1
        if classification.pt_group.value in ("out_of_scope", "unknown"):
            out_of_scope += 1

        # Resolve protein.
        records = enrichment_map.get(p.id, [])
        if records:
            nevo_attempted = True
        resolved = (
            None
            if pt.protein_pct is not None
            else select_protein_enrichment(records)
        )
        has_protein = pt.protein_pct is not None or resolved is not None
        if has_protein:
            with_protein += 1

        # Split.
        has_retailer_split = (
            pt.plant_protein_pct is not None and pt.animal_protein_pct is not None
        )
        has_enriched_split = resolved is not None and (
            resolved.plant_protein_pct is not None
            and resolved.animal_protein_pct is not None
        )
        if has_retailer_split or has_enriched_split:
            with_split += 1

        # Ready criteria — what the calculation engine actually
        # requires: classification + volume + weight + protein_pct.
        # The engine handles split internally (falling back to
        # classification assumption); rows still emit without a split
        # but contribute to the correct group's plant/animal column
        # via the assumption.
        if (
            has_volume
            and has_weight
            and has_protein
            and classification.pt_group.value not in ("unknown",)
        ):
            ready += 1
        else:
            reason_parts = []
            if not has_volume:
                reason_parts.append("no volume")
            if not has_weight:
                reason_parts.append("no weight")
            if not has_protein:
                reason_parts.append("no protein data")
            if classification.pt_group.value == "unknown":
                reason_parts.append("classification=unknown")
            if not has_protein:
                missing_nutrition += 1
            if not has_volume or not has_weight:
                missing_volume_weight += 1
            _sample(
                f"{p.product_name}: " + ", ".join(reason_parts or ["unknown reason"])
            )
    loop_ms = (time.perf_counter() - t0) * 1000

    total_ms = (time.perf_counter() - t_total) * 1000
    logging.getLogger("altera_api.calculation_preflight").info(
        "calculation_preflight.timing project_id=%s "
        "total_products=%d list_products_ms=%.1f "
        "classifications_bulk_ms=%.1f enrichment_bulk_ms=%.1f "
        "nevo_refs_ms=%.1f loop_ms=%.1f total_ms=%.1f "
        "ready=%d missing_nutrition=%d",
        project.id,
        len(products),
        list_products_ms,
        classifications_bulk_ms,
        enrichment_bulk_ms,
        nevo_refs_ms,
        loop_ms,
        total_ms,
        ready,
        missing_nutrition,
    )
    return CalculationPreflightResponse(
        total_products=len(products),
        classified_products=classified,
        products_with_volume=with_volume,
        products_with_weight=with_weight,
        products_with_total_protein=with_protein,
        products_with_plant_animal_split=with_split,
        products_ready_for_calculation=ready,
        products_missing_nutrition=missing_nutrition,
        products_missing_volume_or_weight=missing_volume_weight,
        products_missing_classification=missing_classification,
        products_out_of_scope=out_of_scope,
        sample_exclusion_reasons=sample_reasons,
        nevo_total_references=nevo_total,
        nevo_attempted=nevo_attempted,
    )


# ---------------------------------------------------------------------------
# Phase 34L — manual nutrition override + product exclusion
# ---------------------------------------------------------------------------


class ManualNutritionRequest(BaseModel):
    protein_pct: Decimal = Field(ge=0, le=100)
    plant_protein_pct: Decimal = Field(ge=0, le=100)
    animal_protein_pct: Decimal = Field(ge=0, le=100)
    rationale: str | None = None


@api_router.post(
    "/projects/{project_id}/nutrition-validations/{product_id}/manual",
    response_model=NutritionValidationRow,
)
def submit_manual_nutrition_route(
    product_id: UUID,
    body: ManualNutritionRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> NutritionValidationRow:
    """Record a manual override for a product's protein values.

    Persists three enrichment records (protein_pct, plant_protein_pct,
    animal_protein_pct) with source=manual / match_method=manual and
    confidence=1.0 so the calculation engine picks them up. Existing
    enrichment records for the same product+nutrient are superseded by
    the new one (the store appends; lookup picks the latest ENRICHED).
    """
    from altera_api.domain.enrichment import (
        NutritionEnrichmentRecord,
        NutritionEnrichmentSource,
        NutritionEnrichmentStatus,
    )
    # Soft sanity: plant + animal should sum to the total within 2 pp
    # tolerance. We don't auto-correct — the user is responsible.
    total_check = body.plant_protein_pct + body.animal_protein_pct
    if abs(total_check - body.protein_pct) > Decimal("2"):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "split_does_not_match_total",
                "message": (
                    "La somme plant + animal doit correspondre au "
                    "total protein_pct (tolérance 2pp)."
                ),
                "sum": str(total_check),
                "total": str(body.protein_pct),
            },
        )
    product = store.get_product(product_id)
    if product is None or product.project_id != project.id:
        raise HTTPException(status_code=404, detail="product not found")

    now = datetime.now(UTC)
    rationale = (
        body.rationale or "manual nutrition override (Phase 34L)"
    )[:240]
    for nutrient, value in (
        ("protein_pct", body.protein_pct),
        ("plant_protein_pct", body.plant_protein_pct),
        ("animal_protein_pct", body.animal_protein_pct),
    ):
        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient=nutrient,
                original_value=None,
                enriched_value=value,
                unit="g_per_100g",
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                confidence=Decimal("1"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale=rationale,
                created_at=now,
                created_by=auth.user_id,
                match_method="manual",
            )
        )
    return _nutrition_row_for(store, product)


@api_router.post(
    "/projects/{project_id}/review/{product_id}/{methodology}/decision",
    response_model=ReviewItemResponse,
)
def submit_decision_route(
    product_id: UUID,
    methodology: Methodology,
    body: DecisionRequest,
    _project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReviewItemResponse:
    if not auth.can_review:
        raise_forbidden("only Altera staff can submit review decisions")
    user_id = auth.user_id
    # Phase WWF-O — when the caller supplies an explicit WWF payload,
    # build the target classification here so the orchestrator gets a
    # ready-to-store record (with full subgroup + bucket pinned).
    wwf_target = None
    if body.wwf is not None:
        if methodology is not Methodology.WWF:
            raise HTTPException(
                status_code=400,
                detail="wwf payload only valid for methodology=wwf",
            )
        from datetime import UTC
        from datetime import datetime as _dt
        from decimal import Decimal as _Decimal

        from pydantic import ValidationError as _PydanticValidationError

        from altera_api.api.orchestrator import build_wwf_target_explicit

        try:
            wwf_target = build_wwf_target_explicit(
                product_id,
                wwf_food_group=body.wwf.wwf_food_group,
                wwf_is_composite=body.wwf.wwf_is_composite,
                fg1_subgroup=body.wwf.fg1_subgroup,
                fg2_subgroup=body.wwf.fg2_subgroup,
                fg3_subgroup=body.wwf.fg3_subgroup,
                fg5_grain_kind=body.wwf.fg5_grain_kind,
                fg7_snack_kind=body.wwf.fg7_snack_kind,
                composite_step1_bucket=body.wwf.composite_step1_bucket,
                confidence=(
                    _Decimal(str(body.wwf.confidence))
                    if body.wwf.confidence is not None
                    else None
                ),
                now=_dt.now(UTC),
            )
        except (ValueError, _PydanticValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        view = submit_decision(
            store,
            product_id=product_id,
            methodology=methodology,
            decision=body.decision,
            reviewer_user_id=user_id,
            to_category=body.to_category,
            reason=body.reason,
            wwf_target=wwf_target,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _review_response(view)


class AssignRequest(BaseModel):
    assign_to_user_id: UUID


@api_router.post(
    "/projects/{project_id}/review/{product_id}/{methodology}/claim",
    response_model=ReviewItemResponse,
)
def claim_item_route(
    product_id: UUID,
    methodology: Methodology,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReviewItemResponse:
    if not auth.can_review:
        raise_forbidden("only Altera staff can claim review items")
    try:
        view = claim_review_item(
            store,
            project=project,
            product_id=product_id,
            methodology=methodology,
            reviewer_user_id=auth.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _review_response(view)


@api_router.post(
    "/projects/{project_id}/review/{product_id}/{methodology}/release",
    response_model=ReviewItemResponse,
)
def release_item_route(
    product_id: UUID,
    methodology: Methodology,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReviewItemResponse:
    if not auth.can_review:
        raise_forbidden("only Altera staff can release review items")
    try:
        view = release_review_item(
            store,
            project=project,
            product_id=product_id,
            methodology=methodology,
            reviewer_user_id=auth.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _review_response(view)


@api_router.post(
    "/projects/{project_id}/review/{product_id}/{methodology}/refresh-lock",
    response_model=ReviewItemResponse,
)
def refresh_lock_route(
    product_id: UUID,
    methodology: Methodology,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReviewItemResponse:
    if not auth.can_review:
        raise_forbidden("only Altera staff can refresh review locks")
    try:
        view = refresh_review_lock(
            store,
            project=project,
            product_id=product_id,
            methodology=methodology,
            reviewer_user_id=auth.user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _review_response(view)


@api_router.post(
    "/projects/{project_id}/review/{product_id}/{methodology}/assign",
    response_model=ReviewItemResponse,
)
def assign_item_route(
    product_id: UUID,
    methodology: Methodology,
    body: AssignRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReviewItemResponse:
    if not auth.can_review:
        raise_forbidden("only Altera staff can assign review items")
    try:
        view = assign_review_item(
            store,
            project=project,
            product_id=product_id,
            methodology=methodology,
            assigner_user_id=auth.user_id,
            assign_to_user_id=body.assign_to_user_id,
            auth_can_assign_others=auth.can_approve_report,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _review_response(view)


class BulkActionRequest(BaseModel):
    action: Literal["bulk_accept", "bulk_defer", "bulk_change_pt_group"]
    methodology: Methodology
    product_ids: list[UUID] = Field(min_length=1, max_length=100)
    to_pt_group: str | None = None
    reason: str | None = None


class BulkActionResponse(BaseModel):
    action: str
    requested_count: int
    updated_count: int
    decision_ids: list[UUID]


def _bulk_response(r: BulkActionResult) -> BulkActionResponse:
    return BulkActionResponse(
        action=r.action,
        requested_count=r.requested_count,
        updated_count=r.updated_count,
        decision_ids=list(r.decision_ids),
    )


@api_router.post(
    "/projects/{project_id}/review/bulk-action",
    response_model=BulkActionResponse,
    status_code=200,
)
def bulk_action_route(
    body: BulkActionRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> BulkActionResponse:
    """Apply a bulk review action across multiple products.

    Only Altera reviewers may call this endpoint (403 for GMS/client users).
    All items are validated before any state changes — the operation is
    all-or-nothing. Returns 400 with a descriptive error if any item is
    missing, already terminal, or belongs to a different organisation.
    """
    if not auth.can_review:
        raise_forbidden("only Altera staff can submit review decisions")
    try:
        result = bulk_submit_decision(
            store,
            project=project,
            product_ids=body.product_ids,
            methodology=body.methodology,
            action=body.action,
            reviewer_user_id=auth.user_id,
            to_pt_group=body.to_pt_group,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _bulk_response(result)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
class RunCreateRequest(BaseModel):
    methodology: Methodology
    # Phase 34M — default True. The guided wizard is now the canonical
    # flow and NEVO/manual enrichment records are the normal nutrition
    # source. The previous False-default + Altera-only gate caused the
    # "Lignes éligibles: 7 / Aucun produit ne dispose de données
    # protéiques exploitables" contradiction: the workflow aggregator
    # counted enriched products as eligible but the calc engine
    # ignored their enrichment records, producing 0 rows.
    use_enriched_nutrition: bool = True
    # Phase 34K — when True, the run is allowed even if some products
    # are missing usable nutrition data. The calculation engine
    # naturally skips those products; the run summary carries explicit
    # coverage metrics so the report can disclose what fraction of
    # the input is actually represented in the result.
    allow_partial: bool = False


class RunResponse(BaseModel):
    id: UUID
    project_id: UUID
    methodology: str
    rows_count: int
    started_at: str
    finished_at: str | None
    summary: dict[str, object]


@api_router.post(
    "/projects/{project_id}/runs",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_run(
    body: RunCreateRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    user_id: Annotated[UUID, Depends(current_user_id)],
) -> RunResponse:
    # Phase 34M — the Altera-only gate was the root of the
    # "eligible 7 / 0 usable nutrition" contradiction. NEVO + manual
    # nutrition records ARE the normal source now, so all authenticated
    # users get the enriched calculation by default. Altera-internal
    # is still required for the underlying apply-references endpoint
    # that writes those enrichment records, so this gate is no longer
    # necessary on the run side.
    _ = auth  # auth context retained for audit logging

    # Phase 34A — strict pre-flight: never let a run persist with 0
    # eligible rows. The workflow status aggregator centralises the
    # blocking-reasons logic so the runs page and the guided workflow
    # page see the same gate.
    # Phase 34K — when ``allow_partial=True``, a remaining
    # ``nutrition_required`` blocker is acceptable: the calculation
    # engine skips products without usable nutrition and the run
    # summary carries coverage metrics so the report discloses what
    # the calculation actually covers. Classification / review /
    # zero-eligible blockers still hard-block the run.
    if body.methodology is Methodology.PROTEIN_TRACKER:
        from altera_api.api.workflow import compute_workflow_status

        status_payload = compute_workflow_status(store, project)
        calc_step = next(
            (s for s in status_payload.steps if s.key == "calculation"),
            None,
        )
        blockers = list(calc_step.blocking_reasons) if calc_step else []
        if body.allow_partial:
            # Phase 34V — partial mode is the user explicitly opting
            # in to "compute on whatever rows are ready". Strip the
            # blockers that describe per-product readiness gaps; the
            # calc engine drops those rows naturally and the
            # zero_usable_nutrition guard catches the catastrophic
            # "nothing is ready" case after the run executes.
            #
            # Hard blockers that stay even in partial mode:
            #   - no_eligible_products (literally zero PT products)
            #
            # Stripped in partial mode (these describe rows the
            # engine will skip, not rows that block the engine):
            #   - nutrition_required   — no usable protein on some rows
            #   - review_pending       — products still in needs_review
            #   - classification_required — products without category
            _PARTIAL_OK_CODES = {
                "nutrition_required",
                "review_pending",
                "classification_required",
            }
            blockers = [
                b for b in blockers if b.code not in _PARTIAL_OK_CODES
            ]
        run_ready = (
            calc_step is not None
            and (
                calc_step.status == "ready"
                or (body.allow_partial and not blockers)
            )
        )
        if not run_ready:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "run_not_ready",
                    "message": "Le calcul ne peut pas être lancé pour le moment.",
                    "blocking_reasons": [
                        {
                            "code": r.code,
                            "label": r.label,
                            "count": r.count,
                            "next_action": r.next_action,
                        }
                        for r in (calc_step.blocking_reasons if calc_step else [])
                    ],
                    "current_step": status_payload.current_step,
                    "overall_progress_pct": status_payload.overall_progress_pct,
                    # Kept for backwards-compatibility with the Phase 33D
                    # ``classification_required`` error code that the
                    # frontend's runs page already handles.
                    **(
                        {
                            "error_code": "classification_required",
                            "unclassified_count": next(
                                (
                                    r.count
                                    for r in (
                                        calc_step.blocking_reasons if calc_step else []
                                    )
                                    if r.code == "classification_required"
                                ),
                                0,
                            ),
                        }
                        if calc_step
                        and any(
                            r.code == "classification_required"
                            for r in calc_step.blocking_reasons
                        )
                        else {}
                    ),
                },
            )

    try:
        record = run_calculation(
            store,
            project=project,
            methodology=body.methodology,
            triggered_by=user_id,
            use_enriched_nutrition=body.use_enriched_nutrition,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except APIError as exc:
        # Surface PostgREST/PostgreSQL failures as structured JSON rather
        # than letting them propagate as a raw 500. The original message
        # (which may contain RLS/check-constraint details) is included so
        # the frontend and Render logs both see the same payload.
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "run_persistence_failed",
                "message": f"Run computed but could not be persisted: {exc.message}",
                "postgrest_code": getattr(exc, "code", None),
                "postgrest_hint": getattr(exc, "hint", None),
            },
        ) from exc
    # Phase 34L — zero-row partial-run guard. Even with allow_partial,
    # the calculation must include at least one usable product. The
    # previous behaviour persisted runs with 0 rows / 0 protein, which
    # surfaced as a misleading "Le ratio a été calculé sur 0 % des
    # produits" in the wizard.
    if record.rows_count == 0:
        try:
            store.delete_run(record.id)  # type: ignore[attr-defined]
        except AttributeError:
            pass  # store may not implement delete_run yet
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "zero_usable_nutrition",
                "message": (
                    "Aucun produit ne dispose de données protéiques "
                    "exploitables. Complétez au moins une ligne dans "
                    "la validation nutritionnelle ou excluez les "
                    "produits non exploitables."
                ),
                "rows_count": 0,
            },
        )
    # Phase 34K — coverage metrics. Compute what fraction of the
    # eligible PT products and eligible volume actually made it into
    # the calculated run. The run record itself is unchanged (the
    # calculation engine already filtered to usable rows); we
    # decorate the response summary so the frontend can show the
    # coverage banner without a second round-trip.
    if body.methodology is Methodology.PROTEIN_TRACKER:
        coverage = _compute_pt_coverage(store, project, record.rows_count)
        decorated_summary = dict(record.summary_payload)
        decorated_summary["coverage"] = coverage
    else:
        decorated_summary = record.summary_payload
    return RunResponse(
        id=record.id,
        project_id=record.project_id,
        methodology=record.methodology.value,
        rows_count=record.rows_count,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        summary=decorated_summary,
    )


def _compute_pt_coverage(
    store: StoreProtocol, project: Project, rows_count: int
) -> dict[str, object]:
    """Coverage metrics for a Protein Tracker run.

    Counts how many of the project's PT-eligible products actually
    contributed to the calculation versus how many were dropped for
    lack of usable nutrition. The frontend uses these to render the
    "Le ratio a été calculé sur X% des produits" disclosure.
    """
    products = store.list_products_for_project(project.id)
    pt_total = 0
    volume_total = Decimal("0")
    volume_eligible = Decimal("0")
    eligible_ids: set[UUID] = set()
    from altera_api.domain.enrichment import NutritionEnrichmentStatus

    for p in products:
        if p.pt_fields is None:
            continue
        pt_total += 1
        items = p.pt_fields.items_purchased
        if items is not None:
            volume_total += Decimal(str(items))
        classification = store.get_pt_classification(p.id)
        if classification is None or classification.pt_group.value == "unknown":
            continue
        has_retailer_value = p.pt_fields.protein_pct is not None
        has_enrichment = False
        if not has_retailer_value:
            records = store.get_enrichment_records_for_product(p.id)
            has_enrichment = any(
                r.nutrient == "protein_pct"
                and r.status is NutritionEnrichmentStatus.ENRICHED
                and r.enriched_value is not None
                for r in records
            )
        if has_retailer_value or has_enrichment:
            eligible_ids.add(p.id)
            if items is not None:
                volume_eligible += Decimal(str(items))

    pt_eligible = len(eligible_ids)
    excluded = max(0, pt_total - rows_count)
    # Phase 34U — guard against NaN/Inf reaching JSON serialization.
    # The previous guard ``if pt_total > 0`` was not sufficient: a
    # Decimal volume_total accumulated from many zero-items rows can
    # be Decimal("0.0") which is > 0 but still produces a Decimal
    # division by zero when items_eligible is non-zero in degenerate
    # data. We use a strict-positive threshold via a try/except so any
    # pathological input becomes 0.0 rather than NaN/Inf.
    import math as _math

    def _safe_pct(num: float, denom: float) -> float:
        if denom is None or denom == 0:
            return 0.0
        try:
            v = float(num) / float(denom) * 100.0
        except (ZeroDivisionError, ArithmeticError, ValueError):
            return 0.0
        if _math.isnan(v) or _math.isinf(v):
            return 0.0
        return round(max(0.0, min(100.0, v)), 1)

    product_pct = _safe_pct(rows_count, pt_total)
    volume_pct = _safe_pct(float(volume_eligible), float(volume_total))
    return {
        "total_products_start": pt_total,
        "eligible_products_total": pt_eligible,
        "products_included_in_calculation": rows_count,
        "products_excluded_missing_nutrition": excluded,
        "product_coverage_pct": product_pct,
        "volume_total_start": str(volume_total),
        "volume_included_in_calculation": str(volume_eligible),
        "volume_coverage_pct": volume_pct,
        "is_partial": excluded > 0,
    }


@api_router.get(
    "/projects/{project_id}/runs",
    response_model=Page[RunResponse],
)
def list_runs_route(
    pagination: Annotated[PaginationParams, Depends()],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> Page[RunResponse]:
    items = [
        RunResponse(
            id=rec.id,
            project_id=rec.project_id,
            methodology=rec.methodology.value,
            rows_count=rec.rows_count,
            started_at=rec.started_at.isoformat(),
            finished_at=rec.finished_at.isoformat() if rec.finished_at else None,
            summary=rec.summary_payload,
        )
        for rec in store.list_runs_for_project(project.id)
    ]
    return paginate(items, pagination)


@api_router.get(
    "/projects/{project_id}/runs/{run_id}",
    response_model=RunResponse,
)
def get_run_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> RunResponse:
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")
    return RunResponse(
        id=record.id,
        project_id=record.project_id,
        methodology=record.methodology.value,
        rows_count=record.rows_count,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        summary=record.summary_payload,
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

_CLIENT_VISIBLE_STATUSES = {
    ReportApprovalStatus.APPROVED.value,
    ReportApprovalStatus.DELIVERED.value,
}


class ExportRecordResponse(BaseModel):
    id: UUID
    run_id: UUID
    format: str
    approval_status: str
    filename: str
    size_bytes: int
    created_at: str
    # Phase 20 — approval/delivery metadata
    approved_by: str | None = None
    approved_at: str | None = None
    rejected_by: str | None = None
    rejected_at: str | None = None
    rejection_reason: str | None = None
    under_review_by: str | None = None
    under_review_at: str | None = None
    delivered_by: str | None = None
    delivered_at: str | None = None
    client_download_count: int = 0
    client_downloaded_at: str | None = None


class ApproveExportRequest(BaseModel):
    rejection_reason: str | None = None


def _to_export_response(r: ExportRecord) -> ExportRecordResponse:
    return ExportRecordResponse(
        id=r.id,
        run_id=r.run_id,
        format=r.format,
        approval_status=r.approval_status,
        filename=r.filename,
        size_bytes=r.size_bytes,
        created_at=r.created_at.isoformat(),
        approved_by=str(r.approved_by) if r.approved_by else None,
        approved_at=r.approved_at.isoformat() if r.approved_at else None,
        rejected_by=str(r.rejected_by) if r.rejected_by else None,
        rejected_at=r.rejected_at.isoformat() if r.rejected_at else None,
        rejection_reason=r.rejection_reason,
        under_review_by=str(r.under_review_by) if r.under_review_by else None,
        under_review_at=r.under_review_at.isoformat() if r.under_review_at else None,
        delivered_by=str(r.delivered_by) if r.delivered_by else None,
        delivered_at=r.delivered_at.isoformat() if r.delivered_at else None,
        client_download_count=r.client_download_count,
        client_downloaded_at=r.client_downloaded_at.isoformat() if r.client_downloaded_at else None,
    )


@api_router.get("/projects/{project_id}/runs/{run_id}/export")
def export_run_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
    fmt: Literal["csv", "json", "md"] = "json",
) -> Response:
    # When Storage is configured: enforce the approval gate for client users.
    # Clients can download approved or delivered exports; Altera always gets a fresh render.
    if storage is not None and not auth.is_altera_internal:
        exports = store.get_exports_for_run(run_id)
        available = [
            e for e in exports if e.approval_status in _CLIENT_VISIBLE_STATUSES and e.format == fmt
        ]
        if not available:
            raise_forbidden("no approved export available for this run")
        latest = max(available, key=lambda e: e.created_at)
        try:
            store.record_client_download(latest.id)
            store.append_audit(
                AuditEvent(
                    id=uuid4(),
                    organisation_id=project.organisation_id,
                    actor_user_id=auth.user_id,
                    action=AuditEventType.EXPORT_DOWNLOADED,
                    target_table="report_exports",
                    target_id=latest.id,
                    metadata={"format": fmt, "export_id": str(latest.id)},
                    created_at=datetime.now(UTC),
                )
            )
            signed_url = storage.generate_export_download_url(latest.storage_path, latest.filename)
            return Response(status_code=302, headers={"Location": signed_url})
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="could not generate download URL",
            ) from exc

    # Altera users (or dev mode without Storage): render fresh export.
    try:
        payload, media_type, filename = render_export(
            store, project=project, run_id=run_id, fmt=fmt
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if storage is not None:
        export_id = uuid4()
        storage_path = storage.export_storage_path(
            project.organisation_id, run_id, export_id, filename
        )
        try:
            storage.upload_export(storage_path, payload, filename)
            record = ExportRecord(
                id=export_id,
                run_id=run_id,
                organisation_id=project.organisation_id,
                format=fmt,
                status="success",
                storage_path=storage_path,
                filename=filename,
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                requested_by=auth.user_id,
                created_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
            store.add_export_record(record)
            store.append_audit(
                AuditEvent(
                    id=uuid4(),
                    organisation_id=project.organisation_id,
                    actor_user_id=auth.user_id,
                    action=AuditEventType.EXPORT_GENERATED,
                    target_table="report_exports",
                    target_id=export_id,
                    metadata={"format": fmt, "filename": filename},
                    created_at=datetime.now(UTC),
                )
            )
            signed_url = storage.generate_export_download_url(storage_path, filename)
            return Response(status_code=302, headers={"Location": signed_url})
        except Exception:
            pass  # fall through to in-memory response on storage error

    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@api_router.get(
    "/projects/{project_id}/runs/{run_id}/exports",
    response_model=Page[ExportRecordResponse],
)
def list_exports_route(
    run_id: UUID,
    pagination: Annotated[PaginationParams, Depends()],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> Page[ExportRecordResponse]:
    exports = store.get_exports_for_run(run_id)
    # Clients only see approved/delivered exports; Altera sees all.
    if not auth.is_altera_internal:
        exports = [e for e in exports if e.approval_status in _CLIENT_VISIBLE_STATUSES]
    items = [_to_export_response(e) for e in exports if e.run_id == run_id]
    return paginate(items, pagination)


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/exports/{export_id}/submit-for-review",
    response_model=ExportRecordResponse,
)
def submit_export_for_review_route(
    run_id: UUID,
    export_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ExportRecordResponse:
    if not auth.is_altera_internal:
        raise_forbidden("only Altera internal users can submit exports for review")
    record = store.get_export_record(export_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(status_code=404, detail="export not found")
    if record.approval_status == ReportApprovalStatus.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot submit an already-delivered export for review",
        )
    updated = store.mark_export_under_review(export_id, by_user_id=auth.user_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="submit for review failed")
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.EXPORT_SUBMITTED_FOR_REVIEW,
            target_table="report_exports",
            target_id=export_id,
            metadata={"export_id": str(export_id)},
            created_at=datetime.now(UTC),
        )
    )
    return _to_export_response(updated)


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/exports/{export_id}/approve",
    response_model=ExportRecordResponse,
)
def approve_export_route(
    run_id: UUID,
    export_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ExportRecordResponse:
    if not auth.can_approve_report:
        raise_forbidden("only altera_methodology_lead can approve exports")
    record = store.get_export_record(export_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(status_code=404, detail="export not found")
    updated = store.update_export_approval(
        export_id, approval_status="approved", by_user_id=auth.user_id
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="approval failed")
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.EXPORT_APPROVED,
            target_table="report_exports",
            target_id=export_id,
            metadata={"export_id": str(export_id)},
            created_at=datetime.now(UTC),
        )
    )
    return _to_export_response(updated)


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/exports/{export_id}/reject",
    response_model=ExportRecordResponse,
)
def reject_export_route(
    run_id: UUID,
    export_id: UUID,
    body: ApproveExportRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ExportRecordResponse:
    if not auth.can_approve_report:
        raise_forbidden("only altera_methodology_lead can reject exports")
    record = store.get_export_record(export_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(status_code=404, detail="export not found")
    updated = store.update_export_approval(
        export_id,
        approval_status="rejected",
        by_user_id=auth.user_id,
        rejection_reason=body.rejection_reason,
    )
    if updated is None:
        raise HTTPException(status_code=500, detail="rejection failed")
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.EXPORT_REJECTED,
            target_table="report_exports",
            target_id=export_id,
            metadata={
                "export_id": str(export_id),
                "rejection_reason": body.rejection_reason or "",
            },
            created_at=datetime.now(UTC),
        )
    )
    return _to_export_response(updated)


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/exports/{export_id}/deliver",
    response_model=ExportRecordResponse,
)
def deliver_export_route(
    run_id: UUID,
    export_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ExportRecordResponse:
    if not auth.can_deliver_report:
        raise_forbidden("only altera_methodology_lead or altera_admin can deliver exports")
    record = store.get_export_record(export_id)
    if record is None or record.run_id != run_id:
        raise HTTPException(status_code=404, detail="export not found")
    if record.approval_status != ReportApprovalStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot deliver export with status '{record.approval_status}'; "
            "export must be approved first",
        )
    updated = store.deliver_export(export_id, by_user_id=auth.user_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="delivery failed")
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.EXPORT_DELIVERED,
            target_table="report_exports",
            target_id=export_id,
            metadata={"export_id": str(export_id)},
            created_at=datetime.now(UTC),
        )
    )
    return _to_export_response(updated)


# ---------------------------------------------------------------------------
# Report (Phase 21)
# ---------------------------------------------------------------------------


@api_router.get(
    "/projects/{project_id}/runs/{run_id}/report",
    response_model=ReportDocument,
)
def get_report_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ReportDocument:
    """Return a structured ReportDocument for a run.

    Access is already organisation-scoped by ``get_project`` (a client
    can only reach projects in their own organisation; everything else
    is a 404). Within that scope the run's own organisation may always
    view its report — including the draft produced immediately after a
    guided calculation, before any Altera approval. This makes the
    self-service guided workflow show the full report inline rather than
    falling back to a compact summary (Phase Product-UX-D).

    The approval lifecycle is preserved as metadata: ``meta.approval_status``
    still reflects draft / under_review / approved / delivered, and the
    ``is_altera`` flag still governs recommendation visibility (clients
    see only proposed/accepted recommendations). No commercial fields
    are ever exposed in the report.
    """
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    exports = store.get_exports_for_run(run_id)
    # Prefer the latest client-visible (approved/delivered) export so the
    # report carries the most authoritative approval metadata; otherwise
    # fall back to the latest export of any status, or None for a brand
    # new run with no export yet (draft).
    visible = [e for e in exports if e.approval_status in _CLIENT_VISIBLE_STATUSES]
    pool = visible or exports
    export = max(pool, key=lambda e: e.created_at) if pool else None

    return build_report_document(store, record, project, export, is_altera=auth.is_altera_internal)


# ---------------------------------------------------------------------------
# Recommendations (Phase 25B)
# ---------------------------------------------------------------------------


class RecommendationResponse(BaseModel):
    id: UUID
    run_id: UUID
    action_type: str
    category: str
    title: str
    description: str
    rationale: str
    expected_direction: str
    priority: str
    confidence: str
    evidence: list[str]
    status: str
    caveats: list[str]
    client_facing: bool
    created_at: str
    updated_at: str


def _rec_response(rec: PersistedRecommendation) -> RecommendationResponse:
    return RecommendationResponse(
        id=rec.id,
        run_id=rec.run_id,
        action_type=rec.action_type,
        category=rec.category,
        title=rec.title,
        description=rec.description,
        rationale=rec.rationale,
        expected_direction=rec.expected_direction,
        priority=rec.priority,
        confidence=rec.confidence,
        evidence=rec.evidence,
        status=rec.status,
        caveats=rec.caveats,
        client_facing=rec.client_facing,
        created_at=rec.created_at.isoformat(),
        updated_at=rec.updated_at.isoformat(),
    )


_CLIENT_VISIBLE_REC_STATUSES = {"proposed", "accepted"}


@api_router.get(
    "/projects/{project_id}/runs/{run_id}/recommendations",
    response_model=Page[RecommendationResponse],
)
def list_recommendations_route(
    run_id: UUID,
    pagination: Annotated[PaginationParams, Depends()],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> Page[RecommendationResponse]:
    """List persisted recommendations for a run.

    Altera users see all statuses; clients see only proposed and accepted.
    """
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    recs = store.list_recommendations_for_run(run_id)

    if not auth.is_altera_internal:
        recs = [r for r in recs if r.status in _CLIENT_VISIBLE_REC_STATUSES]

    return paginate([_rec_response(r) for r in recs], pagination)


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/recommendations/generate",
    response_model=list[RecommendationResponse],
    status_code=status.HTTP_201_CREATED,
)
def generate_recommendations_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[RecommendationResponse]:
    """Generate and persist recommendations for a run (Altera only).

    Existing recommendations are upserted with status preservation — already
    proposed/accepted/dismissed/archived items keep their status.
    """
    if not auth.can_generate_recommendations:
        raise_forbidden("altera internal access required")

    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    from altera_api.exports.coverage import build_coverage_section
    from altera_api.recommendations.engine import generate_recommendations as _gen

    coverage = build_coverage_section(store, record, project)

    from altera_api.domain.common import Methodology as _M

    if record.methodology is _M.PROTEIN_TRACKER:
        s_pt = parse_pt_summary_payload(record.summary_payload)
        ephemeral = _gen(
            _M.PROTEIN_TRACKER,
            pt_summary=s_pt,
            uncertainty_level=coverage.uncertainty_level,
            products_total=coverage.products_total,
            products_unknown=coverage.products_unknown,
            products_ai_classified=coverage.products_ai_classified,
            products_with_missing_protein=coverage.products_with_missing_protein,
        )
    else:
        s_wwf = parse_wwf_summary_payload(record.summary_payload)
        step2_map = store.get_wwf_ingredients_by_project(project.id)
        wwf_step2_applied = len(step2_map)
        product_ids_in_run = {row["product_id"] for row in record.rows_payload if row.get("product_id")}
        products_in_run_list = [
            p
            for p in store.list_products_for_project(project.id)
            if str(p.id) in product_ids_in_run or p.id in product_ids_in_run
        ]
        own_brand_count = 0
        branded_count = 0
        for p in products_in_run_list:
            if p.wwf_fields is None:
                continue
            clf = store.get_wwf_classification(p.id)
            if clf is None or not clf.wwf_is_composite:
                continue
            if p.wwf_fields.is_own_brand:
                own_brand_count += 1
            else:
                branded_count += 1
        ephemeral = _gen(
            _M.WWF,
            wwf_summary=s_wwf,
            uncertainty_level=coverage.uncertainty_level,
            products_total=coverage.products_total,
            products_unknown=coverage.products_unknown,
            products_ai_classified=coverage.products_ai_classified,
            wwf_step2_applied_count=wwf_step2_applied,
            wwf_own_brand_composite_count=own_brand_count,
            wwf_branded_composite_count=branded_count,
        )

    now = datetime.now(UTC)
    persisted_records = [
        PersistedRecommendation(
            id=uuid4(),
            organisation_id=project.organisation_id,
            project_id=project.id,
            run_id=run_id,
            methodology=record.methodology.value,
            action_type=r.action_type,
            category=r.category,
            title=r.title,
            description=r.description,
            rationale=r.rationale,
            expected_direction=r.expected_direction,
            priority=r.priority,
            confidence=r.confidence,
            evidence=r.evidence,
            caveats=r.caveats,
            status="draft",
            client_facing=r.client_facing,
            created_at=now,
            updated_at=now,
            created_by=auth.user_id,
        )
        for r in ephemeral
    ]

    if persisted_records:
        store.upsert_recommendations_for_run(persisted_records)

    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.RECOMMENDATION_GENERATED,
            target_table="recommendations",
            target_id=run_id,
            metadata={"run_id": str(run_id), "count": len(persisted_records)},
            created_at=now,
        )
    )

    return [_rec_response(r) for r in store.list_recommendations_for_run(run_id)]


def _transition_recommendation(
    recommendation_id: UUID,
    new_status: str,
    audit_action: AuditEventType,
    store: StoreProtocol,
    auth: AuthContext,
) -> RecommendationResponse:
    """Shared helper for propose/dismiss/archive/accept transitions."""
    rec = store.get_recommendation(recommendation_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    # Cross-org guard
    if rec.organisation_id != auth.organisation_id and not auth.is_altera_internal:
        raise_forbidden("access denied")

    updated = store.update_recommendation_status(
        recommendation_id, status=new_status, by_user_id=auth.user_id
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="recommendation not found")

    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=rec.organisation_id,
            actor_user_id=auth.user_id,
            action=audit_action,
            target_table="recommendations",
            target_id=recommendation_id,
            metadata={"new_status": new_status},
            created_at=datetime.now(UTC),
        )
    )

    return _rec_response(updated)


@api_router.post(
    "/recommendations/{recommendation_id}/propose",
    response_model=RecommendationResponse,
)
def propose_recommendation_route(
    recommendation_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> RecommendationResponse:
    """Propose a recommendation (promote from draft → proposed). Altera METHODOLOGY_LEAD/ADMIN only."""
    if not auth.can_propose_recommendation:
        raise_forbidden("insufficient permissions to propose recommendations")
    return _transition_recommendation(
        recommendation_id, "proposed", AuditEventType.RECOMMENDATION_PROPOSED, store, auth
    )


@api_router.post(
    "/recommendations/{recommendation_id}/dismiss",
    response_model=RecommendationResponse,
)
def dismiss_recommendation_route(
    recommendation_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> RecommendationResponse:
    """Dismiss a recommendation (Altera internal only)."""
    if not auth.is_altera_internal:
        raise_forbidden("altera internal access required")
    return _transition_recommendation(
        recommendation_id, "dismissed", AuditEventType.RECOMMENDATION_DISMISSED, store, auth
    )


@api_router.post(
    "/recommendations/{recommendation_id}/archive",
    response_model=RecommendationResponse,
)
def archive_recommendation_route(
    recommendation_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> RecommendationResponse:
    """Archive a recommendation (Altera internal only)."""
    if not auth.is_altera_internal:
        raise_forbidden("altera internal access required")
    return _transition_recommendation(
        recommendation_id, "archived", AuditEventType.RECOMMENDATION_ARCHIVED, store, auth
    )


@api_router.post(
    "/recommendations/{recommendation_id}/accept",
    response_model=RecommendationResponse,
)
def accept_recommendation_route(
    recommendation_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> RecommendationResponse:
    """Accept a recommendation (proposed → accepted). Altera METHODOLOGY_LEAD/ADMIN only."""
    if not auth.can_propose_recommendation:
        raise_forbidden("insufficient permissions to accept recommendations")
    return _transition_recommendation(
        recommendation_id, "accepted", AuditEventType.RECOMMENDATION_ACCEPTED, store, auth
    )


# ---------------------------------------------------------------------------
# Scenarios (Phase 26A)
# ---------------------------------------------------------------------------


class ScenarioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    base_run_id: UUID


class ScenarioOperationRequest(BaseModel):
    operation_type: str
    parameters: dict = Field(default_factory=dict)
    rationale: str = ""
    order: int = 0


class ScenarioOperationResponse(BaseModel):
    id: UUID
    scenario_id: UUID
    operation_type: str
    parameters: dict
    rationale: str
    order: int
    created_at: str


class ScenarioResponse(BaseModel):
    id: UUID
    organisation_id: UUID
    project_id: UUID
    base_run_id: UUID
    name: str
    description: str
    status: str
    methodology: str
    created_by: UUID
    created_at: str
    updated_at: str
    operation_count: int


class PTProjectedGroupResponse(BaseModel):
    pt_group: str
    base_protein_kg: str
    projected_protein_kg: str
    delta_protein_kg: str


class PTProjectedSummaryResponse(BaseModel):
    base_plant_protein_kg: str
    base_animal_protein_kg: str
    base_total_protein_kg: str
    base_plant_share_pct: str | None
    projected_plant_protein_kg: str
    projected_animal_protein_kg: str
    projected_total_protein_kg: str
    projected_plant_share_pct: str | None
    projected_animal_share_pct: str | None
    delta_plant_protein_kg: str
    delta_animal_protein_kg: str
    delta_plant_share_pct: str | None
    per_group: list[PTProjectedGroupResponse]


class ScenarioResultResponse(BaseModel):
    scenario_id: UUID
    base_run_id: UUID
    methodology: str
    pt_projected: PTProjectedSummaryResponse | None
    warnings: list[str]
    created_at: str


def _scenario_response(store: StoreProtocol, rec: ScenarioRecord) -> ScenarioResponse:
    op_count = len(store.list_scenario_operations(rec.id))
    return ScenarioResponse(
        id=rec.id,
        organisation_id=rec.organisation_id,
        project_id=rec.project_id,
        base_run_id=rec.base_run_id,
        name=rec.name,
        description=rec.description,
        status=rec.status,
        methodology=rec.methodology,
        created_by=rec.created_by,
        created_at=rec.created_at.isoformat(),
        updated_at=rec.updated_at.isoformat(),
        operation_count=op_count,
    )


def _result_response(result: ScenarioResult) -> ScenarioResultResponse:
    pt = None
    if result.pt_projected is not None:
        p = result.pt_projected
        pt = PTProjectedSummaryResponse(
            base_plant_protein_kg=str(p.base_plant_protein_kg),
            base_animal_protein_kg=str(p.base_animal_protein_kg),
            base_total_protein_kg=str(p.base_total_protein_kg),
            base_plant_share_pct=str(p.base_plant_share_pct) if p.base_plant_share_pct is not None else None,
            projected_plant_protein_kg=str(p.projected_plant_protein_kg),
            projected_animal_protein_kg=str(p.projected_animal_protein_kg),
            projected_total_protein_kg=str(p.projected_total_protein_kg),
            projected_plant_share_pct=str(p.projected_plant_share_pct) if p.projected_plant_share_pct is not None else None,
            projected_animal_share_pct=str(p.projected_animal_share_pct) if p.projected_animal_share_pct is not None else None,
            delta_plant_protein_kg=str(p.delta_plant_protein_kg),
            delta_animal_protein_kg=str(p.delta_animal_protein_kg),
            delta_plant_share_pct=str(p.delta_plant_share_pct) if p.delta_plant_share_pct is not None else None,
            per_group=[
                PTProjectedGroupResponse(
                    pt_group=g.pt_group,
                    base_protein_kg=str(g.base_protein_kg),
                    projected_protein_kg=str(g.projected_protein_kg),
                    delta_protein_kg=str(g.delta_protein_kg),
                )
                for g in p.per_group
            ],
        )
    return ScenarioResultResponse(
        scenario_id=result.scenario_id,
        base_run_id=result.base_run_id,
        methodology=result.methodology,
        pt_projected=pt,
        warnings=result.warnings,
        created_at=result.created_at.isoformat(),
    )


@api_router.post(
    "/projects/{project_id}/scenarios",
    response_model=ScenarioResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_scenario_route(
    body: ScenarioCreateRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ScenarioResponse:
    """Create a scenario attached to a base run (Altera only)."""
    if not auth.can_create_scenario:
        raise_forbidden("altera internal access required")

    run = store.get_run(body.base_run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(status_code=404, detail="base run not found")

    if run.methodology.value != "protein_tracker":
        raise HTTPException(
            status_code=422,
            detail="Phase 26A only supports Protein Tracker scenarios. "
                   "WWF scenario modelling is not yet implemented.",
        )

    now = datetime.now(UTC)
    rec = ScenarioRecord(
        id=uuid4(),
        organisation_id=project.organisation_id,
        project_id=project.id,
        base_run_id=body.base_run_id,
        name=body.name,
        description=body.description,
        status=ScenarioStatus.DRAFT.value,
        methodology="protein_tracker",
        created_by=auth.user_id,
        created_at=now,
        updated_at=now,
    )
    store.add_scenario(rec)
    return _scenario_response(store, rec)


@api_router.get(
    "/projects/{project_id}/scenarios",
    response_model=Page[ScenarioResponse],
)
def list_scenarios_route(
    pagination: Annotated[PaginationParams, Depends()],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> Page[ScenarioResponse]:
    """List scenarios for a project.

    Altera sees all statuses. Clients see only active scenarios.
    """
    scenarios = store.list_scenarios_for_project(project.id)
    if not auth.is_altera_internal:
        scenarios = [s for s in scenarios if s.status == ScenarioStatus.ACTIVE.value]
    return paginate([_scenario_response(store, s) for s in scenarios], pagination)


@api_router.get(
    "/scenarios/{scenario_id}/operations",
    response_model=list[ScenarioOperationResponse],
)
def list_scenario_operations_route(
    scenario_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[ScenarioOperationResponse]:
    """List operations for a scenario (Altera only)."""
    if not auth.can_create_scenario:
        raise_forbidden("altera internal access required")

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")

    ops = store.list_scenario_operations(scenario_id)
    return [
        ScenarioOperationResponse(
            id=op.id,
            scenario_id=op.scenario_id,
            operation_type=op.operation_type,
            parameters=op.parameters,
            rationale=op.rationale,
            order=op.order,
            created_at=op.created_at.isoformat(),
        )
        for op in ops
    ]


@api_router.post(
    "/scenarios/{scenario_id}/operations",
    response_model=ScenarioOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_scenario_operation_route(
    scenario_id: UUID,
    body: ScenarioOperationRequest,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ScenarioOperationResponse:
    """Add an operation to a scenario (Altera only)."""
    if not auth.can_create_scenario:
        raise_forbidden("altera internal access required")

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    if scenario.organisation_id != auth.organisation_id and not auth.is_altera_internal:
        raise_forbidden("access denied")

    # Validate operation type
    try:
        ScenarioOperationType(body.operation_type)
    except ValueError:
        valid = [o.value for o in ScenarioOperationType]
        raise HTTPException(
            status_code=422,
            detail=f"Unknown operation_type {body.operation_type!r}. Valid: {valid}",
        ) from None

    now = datetime.now(UTC)
    op = ScenarioOperationRecord(
        id=uuid4(),
        scenario_id=scenario_id,
        operation_type=body.operation_type,
        parameters=body.parameters,
        rationale=body.rationale,
        order=body.order,
        created_at=now,
    )
    store.add_scenario_operation(op)

    return ScenarioOperationResponse(
        id=op.id,
        scenario_id=op.scenario_id,
        operation_type=op.operation_type,
        parameters=op.parameters,
        rationale=op.rationale,
        order=op.order,
        created_at=op.created_at.isoformat(),
    )


@api_router.post(
    "/scenarios/{scenario_id}/run",
    response_model=ScenarioResultResponse,
)
def run_scenario_route(
    scenario_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ScenarioResultResponse:
    """Execute a scenario projection and persist the result (Altera only)."""
    if not auth.can_create_scenario:
        raise_forbidden("altera internal access required")

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")

    run = store.get_run(scenario.base_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="base run not found")

    from altera_api.scenarios.pt_projection import project_pt_scenario

    try:
        base_summary = parse_pt_summary_payload(run.summary_payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse base run summary: {exc}") from exc

    ops_records = store.list_scenario_operations(scenario_id)
    ops = [
        ScenarioOperation(
            id=op.id,
            scenario_id=op.scenario_id,
            operation_type=ScenarioOperationType(op.operation_type),
            parameters=op.parameters,
            rationale=op.rationale,
            order=op.order,
        )
        for op in ops_records
    ]

    result = project_pt_scenario(base_summary, ops, scenario_id=scenario_id)

    store.save_scenario_result(
        ScenarioResultRecord(
            scenario_id=scenario_id,
            base_run_id=scenario.base_run_id,
            methodology=scenario.methodology,
            result_payload=result.model_dump(mode="json"),
            created_at=result.created_at,
        )
    )

    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=scenario.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.SCENARIO_RUN,
            target_table="scenarios",
            target_id=scenario_id,
            metadata={
                "base_run_id": str(scenario.base_run_id),
                "operations_count": len(ops),
            },
            created_at=datetime.now(UTC),
        )
    )

    # Promote to active on first successful run
    if scenario.status == ScenarioStatus.DRAFT.value:
        store.update_scenario_status(scenario_id, status=ScenarioStatus.ACTIVE.value)

    return _result_response(result)


@api_router.get(
    "/scenarios/{scenario_id}/result",
    response_model=ScenarioResultResponse,
)
def get_scenario_result_route(
    scenario_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> ScenarioResultResponse:
    """Return the most recent projection result for a scenario."""
    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")

    # Cross-org access: clients may only read their own org's scenarios
    if not auth.is_altera_internal and scenario.organisation_id != auth.organisation_id:
        raise HTTPException(status_code=404, detail="scenario not found")

    # Clients only see active scenarios
    if not auth.is_altera_internal and scenario.status != ScenarioStatus.ACTIVE.value:
        raise_forbidden("scenario not yet active")

    result_record = store.get_scenario_result(scenario_id)
    if result_record is None:
        raise HTTPException(status_code=404, detail="no result yet — run the scenario first")

    result = ScenarioResult.model_validate(result_record.result_payload)
    return _result_response(result)


# ---------------------------------------------------------------------------
# Jobs (Phase 16)
# ---------------------------------------------------------------------------


class JobResponse(BaseModel):
    job_id: UUID
    organisation_id: UUID
    project_id: UUID
    upload_id: UUID | None
    run_id: UUID | None
    job_type: str
    status: str
    progress_pct: int | None
    created_by: UUID
    created_at: str
    started_at: str | None
    completed_at: str | None
    failed_at: str | None
    error_message: str | None
    retry_count: int
    idempotency_key: str | None
    result: dict | None


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        job_id=job.job_id,
        organisation_id=job.organisation_id,
        project_id=job.project_id,
        upload_id=job.upload_id,
        run_id=job.run_id,
        job_type=job.job_type.value,
        status=job.status.value,
        progress_pct=job.progress_pct,
        created_by=job.created_by,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        failed_at=job.failed_at.isoformat() if job.failed_at else None,
        error_message=job.error_message,
        retry_count=job.retry_count,
        idempotency_key=job.idempotency_key,
        result=job.payload.get("result"),
    )


def _create_and_dispatch(
    *,
    job_type: JobType,
    project: Project,
    store: StoreProtocol,
    worker: WorkerBackend,
    auth: AuthContext,
    payload: dict,
    storage: StorageService | None = None,
    upload_id: UUID | None = None,
    run_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Create a job, check idempotency, dispatch to worker.

    Returns ``(job, created)`` where *created* is False if an active job
    with the same idempotency_key was found and returned instead.
    """
    if idempotency_key is not None:
        existing = store.find_active_job(job_type=job_type, idempotency_key=idempotency_key)
        if existing is not None:
            return existing, False

    now = datetime.now(UTC)
    job = Job(
        job_id=uuid4(),
        organisation_id=project.organisation_id,
        project_id=project.id,
        upload_id=upload_id,
        run_id=run_id,
        job_type=job_type,
        status=JobStatus.QUEUED,
        created_by=auth.user_id,
        created_at=now,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    store.add_job(job)
    # Emit audit event for job creation (user-initiated → has actor)
    try:
        store.append_audit(
            AuditEvent(
                id=uuid4(),
                organisation_id=project.organisation_id,
                actor_user_id=auth.user_id,
                action=AuditEventType.JOB_CREATED,
                target_table="jobs",
                target_id=job.job_id,
                metadata={"job_type": job_type.value},
                created_at=now,
            )
        )
    except Exception:
        pass

    completed = worker.dispatch(job, store, storage)
    return completed, True


# --- Upload-scoped job endpoints -------------------------------------------


class ValidateUploadJobRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    file_bytes_b64: str | None = None  # dev/test fallback; omit when upload has real storage_path


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/jobs/validate",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_validate_upload(
    upload_id: UUID,
    body: ValidateUploadJobRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    worker: Annotated[WorkerBackend, Depends(get_worker)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
) -> JobResponse:
    payload: dict = {"upload_id": str(upload_id), "filename": body.filename}
    if body.file_bytes_b64 is not None:
        payload["file_bytes_b64"] = body.file_bytes_b64
    idem_key = f"validate_upload:{upload_id}"
    job, _created = _create_and_dispatch(
        job_type=JobType.VALIDATE_UPLOAD,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload=payload,
        storage=storage,
        upload_id=upload_id,
        idempotency_key=idem_key,
    )
    return _job_response(job)


class IngestUploadJobRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    file_bytes_b64: str | None = None  # dev/test fallback; omit when upload has real storage_path
    storage_path: str | None = None


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/jobs/ingest",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_ingest_upload(
    upload_id: UUID,
    body: IngestUploadJobRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    worker: Annotated[WorkerBackend, Depends(get_worker)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
) -> JobResponse:
    payload: dict = {"upload_id": str(upload_id), "filename": body.filename}
    if body.file_bytes_b64 is not None:
        payload["file_bytes_b64"] = body.file_bytes_b64
    if body.storage_path is not None:
        payload["storage_path"] = body.storage_path
    idem_key = f"ingest_upload:{upload_id}"
    job, _created = _create_and_dispatch(
        job_type=JobType.INGEST_UPLOAD,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload=payload,
        storage=storage,
        upload_id=upload_id,
        idempotency_key=idem_key,
    )
    return _job_response(job)


class ClassifyUploadJobRequest(BaseModel):
    methodology: Methodology


@api_router.post(
    "/projects/{project_id}/uploads/{upload_id}/jobs/classify",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_classify_upload(
    upload_id: UUID,
    body: ClassifyUploadJobRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    worker: Annotated[WorkerBackend, Depends(get_worker)],
) -> JobResponse:
    idem_key = f"classify_upload:{upload_id}:{body.methodology.value}"
    job, _created = _create_and_dispatch(
        job_type=JobType.CLASSIFY_UPLOAD,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload={"upload_id": str(upload_id), "methodology": body.methodology.value},
        upload_id=upload_id,
        idempotency_key=idem_key,
    )
    return _job_response(job)


# --- Project-scoped calculation job -----------------------------------------


class CalculateJobRequest(BaseModel):
    methodology: Methodology
    use_enriched_nutrition: bool = False


@api_router.post(
    "/projects/{project_id}/jobs/calculate",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_calculate(
    body: CalculateJobRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    worker: Annotated[WorkerBackend, Depends(get_worker)],
) -> JobResponse:
    if body.use_enriched_nutrition and not auth.is_altera_internal:
        raise_forbidden("use_enriched_nutrition may only be enabled by Altera internal users")
    job, _created = _create_and_dispatch(
        job_type=JobType.RUN_CALCULATION,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload={
            "methodology": body.methodology.value,
            "use_enriched_nutrition": body.use_enriched_nutrition,
        },
    )
    return _job_response(job)


# --- Run-scoped export job --------------------------------------------------


class ExportJobRequest(BaseModel):
    fmt: Literal["csv", "json", "md"] = "json"


@api_router.post(
    "/projects/{project_id}/runs/{run_id}/jobs/export",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_export(
    run_id: UUID,
    body: ExportJobRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    worker: Annotated[WorkerBackend, Depends(get_worker)],
    storage: Annotated[StorageService | None, Depends(get_storage_service)],
) -> JobResponse:
    idem_key = f"generate_export:{run_id}:{body.fmt}"
    job, _created = _create_and_dispatch(
        job_type=JobType.GENERATE_EXPORT,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload={"run_id": str(run_id), "fmt": body.fmt},
        storage=storage,
        run_id=run_id,
        idempotency_key=idem_key,
    )
    return _job_response(job)


# --- Generic job lookup -----------------------------------------------------


@api_router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job_route(
    job_id: UUID,
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> JobResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.organisation_id != auth.organisation_id and not auth.is_altera_internal:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_response(job)


@api_router.get("/projects/{project_id}/jobs", response_model=Page[JobResponse])
def list_jobs_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    pagination: Annotated[PaginationParams, Depends()],
    job_type: JobType | None = None,
) -> Page[JobResponse]:
    # TODO(Phase 29B): add rate limiting — this endpoint is polled during active job processing
    jobs = store.list_jobs_for_project(project.id)
    if job_type is not None:
        jobs = [j for j in jobs if j.job_type is job_type]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return paginate([_job_response(j) for j in jobs], pagination)


# ---------------------------------------------------------------------------
# Nutrition enrichment (Phase 23B)
# ---------------------------------------------------------------------------


class EnrichmentRecordResponse(BaseModel):
    product_id: UUID
    nutrient: str
    original_value: str | None
    enriched_value: str | None
    unit: str
    source: str
    confidence: str | None
    status: str
    rationale: str
    created_at: str
    created_by: UUID | None


class ManualEnrichmentRequest(BaseModel):
    enriched_value: float = Field(ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    rationale: str = Field(min_length=1)


def _enrichment_response(r: object) -> EnrichmentRecordResponse:
    from altera_api.domain.enrichment import NutritionEnrichmentRecord

    assert isinstance(r, NutritionEnrichmentRecord)
    return EnrichmentRecordResponse(
        product_id=r.product_id,
        nutrient=r.nutrient,
        original_value=str(r.original_value) if r.original_value is not None else None,
        enriched_value=str(r.enriched_value) if r.enriched_value is not None else None,
        unit=r.unit,
        source=r.source.value,
        confidence=str(r.confidence) if r.confidence is not None else None,
        status=r.status.value,
        rationale=r.rationale,
        created_at=r.created_at.isoformat(),
        created_by=r.created_by,
    )


def _resolve_pt_product(
    project_id: UUID,
    product_id: UUID,
    store: StoreProtocol,
) -> object:
    """Return the NormalizedProduct or raise 404/422."""
    from altera_api.domain.common import Methodology
    from altera_api.domain.product import NormalizedProduct

    product = store.get_product(product_id)
    if product is None or product.project_id != project_id:
        raise HTTPException(status_code=404, detail="product not found")
    assert isinstance(product, NormalizedProduct)
    if Methodology.PROTEIN_TRACKER not in product.methodologies_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="product does not have Protein Tracker enabled",
        )
    return product


@api_router.get(
    "/projects/{project_id}/products/{product_id}/enrichments",
    response_model=list[EnrichmentRecordResponse],
)
def list_enrichments_route(
    product_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[EnrichmentRecordResponse]:
    """List all enrichment records for a product. Altera-only."""
    if not auth.is_altera_internal:
        raise_forbidden("altera internal access required")
    _resolve_pt_product(project.id, product_id, store)
    records = store.get_enrichment_records_for_product(product_id)
    records.sort(key=lambda r: r.created_at)
    return [_enrichment_response(r) for r in records]


@api_router.post(
    "/projects/{project_id}/products/{product_id}/enrichments/manual",
    response_model=EnrichmentRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_manual_enrichment_route(
    product_id: UUID,
    body: ManualEnrichmentRequest,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> EnrichmentRecordResponse:
    """Create a manual protein enrichment record. Altera-only.

    Rejected if the product already has a retailer-provided protein_pct —
    enrichment is only for products with missing label data.
    """
    from decimal import Decimal as D

    from altera_api.domain.enrichment import (
        NutritionEnrichmentRecord,
        NutritionEnrichmentSource,
        NutritionEnrichmentStatus,
    )
    from altera_api.domain.product import NormalizedProduct

    if not auth.can_apply_enrichment:
        raise_forbidden("altera internal access required")

    product = _resolve_pt_product(project.id, product_id, store)
    assert isinstance(product, NormalizedProduct)

    if product.pt_fields is not None and product.pt_fields.protein_pct is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "product already has a retailer-provided protein_pct; "
                "manual enrichment is only allowed for products with missing label data"
            ),
        )

    now = datetime.now(UTC)
    original_value = (
        product.pt_fields.protein_pct if product.pt_fields is not None else None
    )
    confidence = D(str(body.confidence)) if body.confidence is not None else None

    record = NutritionEnrichmentRecord(
        product_id=product_id,
        nutrient="protein_pct",
        original_value=original_value,
        enriched_value=D(str(body.enriched_value)),
        unit="g_per_100g",
        source=NutritionEnrichmentSource.MANUAL_ALTERA,
        confidence=confidence,
        status=NutritionEnrichmentStatus.ENRICHED,
        rationale=body.rationale,
        created_at=now,
        created_by=auth.user_id,
    )
    store.add_enrichment_record(record)
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.ENRICHMENT_APPLIED,
            target_table="nutrition_enrichment_records",
            target_id=product_id,
            metadata={"nutrient": record.nutrient, "source": str(record.source)},
            created_at=datetime.now(UTC),
        )
    )
    return _enrichment_response(record)


@api_router.post(
    "/projects/{project_id}/products/{product_id}/enrichments/category-average",
    response_model=EnrichmentRecordResponse,
    status_code=status.HTTP_201_CREATED,
)
def apply_category_average_enrichment_route(
    product_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> EnrichmentRecordResponse:
    """Apply the category-average protein value for a product. Altera-only.

    Requires the product to have a PT classification so the correct
    group average can be selected. Rejected if the product already has
    a retailer-provided protein_pct, or if its PT group has no average
    in the static table (out_of_scope, unknown).
    """
    from altera_api.domain.product import NormalizedProduct
    from altera_api.enrichment.providers.category_average import CategoryAverageProvider

    if not auth.can_apply_enrichment:
        raise_forbidden("altera internal access required")

    product = _resolve_pt_product(project.id, product_id, store)
    assert isinstance(product, NormalizedProduct)

    if product.pt_fields is not None and product.pt_fields.protein_pct is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "product already has a retailer-provided protein_pct; "
                "category-average enrichment is only allowed for products with missing label data"
            ),
        )

    classification = store.get_pt_classification(product_id)
    if classification is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="product has no PT classification; classify before enriching",
        )

    now = datetime.now(UTC)
    provider = CategoryAverageProvider()
    record = provider.enrich_by_group(
        product_id,
        classification.pt_group,
        "protein_pct",
        now=now,
        created_by=auth.user_id,
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no category average available for pt_group={classification.pt_group.value}; "
                "only plant_based_core, plant_based_non_core, composite_products, and "
                "animal_core groups have averages"
            ),
        )

    store.add_enrichment_record(record)
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.ENRICHMENT_APPLIED,
            target_table="nutrition_enrichment_records",
            target_id=product_id,
            metadata={"nutrient": record.nutrient, "source": str(record.source)},
            created_at=datetime.now(UTC),
        )
    )
    return _enrichment_response(record)


# ---------------------------------------------------------------------------
# Phase 33H — apply nutrition references (NEVO → CIQUAL fallback)
# ---------------------------------------------------------------------------


class NutritionReferencesStatsResponse(BaseModel):
    """Phase 34D — diagnostic endpoint for NEVO / CIQUAL table state.

    The guided wizard reads this on Step 6 so it can show a clear
    admin-facing error when the reference tables are empty, instead of
    silently reporting "0 matched". Altera-internal access only.

    Phase 34O — adds ``nevo_sanity_pass`` so the wizard can flag a
    truncated import (e.g. only 1000 rows reaching the DB) without
    forcing the user to look at the row count and remember the
    expected threshold.
    """

    nevo_total: int
    nevo_with_protein: int
    nevo_with_split: int
    nevo_sample_names: list[str]
    ciqual_total: int
    ciqual_with_protein: int
    ciqual_sample_names: list[str]
    # Phase 34O — sanity-pass flag and the threshold the diagnostic
    # used. Mirrors the importer's row-count floor so frontend and
    # backend agree on what "the full NEVO is loaded" means.
    nevo_expected_min: int = 2000
    nevo_sanity_pass: bool = False


@api_router.get(
    "/admin/nutrition-references/stats",
    response_model=NutritionReferencesStatsResponse,
)
def nutrition_references_stats_route(
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> NutritionReferencesStatsResponse:
    if not auth.can_apply_enrichment:
        raise_forbidden("altera internal access required")
    nevo_entries = store.list_nevo_entries()
    ciqual_entries = store.list_ciqual_entries()
    nevo_with_protein = sum(
        1 for e in nevo_entries if e.protein_g_per_100g is not None
    )
    nevo_with_split = sum(
        1
        for e in nevo_entries
        if e.plant_protein_g_per_100g is not None
        and e.animal_protein_g_per_100g is not None
    )
    ciqual_with_protein = sum(
        1 for e in ciqual_entries if e.protein_g_per_100g is not None
    )
    # Phase 34O — sanity threshold mirrors scripts/import_nevo.py's
    # _EXPECTED_MIN_ROWS. Hard-coded here to avoid importing the
    # script module from the route layer.
    _NEVO_EXPECTED_MIN = 2000
    return NutritionReferencesStatsResponse(
        nevo_total=len(nevo_entries),
        nevo_with_protein=nevo_with_protein,
        nevo_with_split=nevo_with_split,
        nevo_sample_names=[
            (e.food_name_en or e.food_name_nl) for e in nevo_entries[:5]
        ],
        nevo_expected_min=_NEVO_EXPECTED_MIN,
        nevo_sanity_pass=len(nevo_entries) >= _NEVO_EXPECTED_MIN,
        ciqual_total=len(ciqual_entries),
        ciqual_with_protein=ciqual_with_protein,
        ciqual_sample_names=[e.food_name_en for e in ciqual_entries[:5]],
    )


class ApplyReferencesRequest(BaseModel):
    # Phase 34B — limit which providers to run. None/empty means all.
    # Accepted values: "nevo", "ciqual". Unknown values are ignored.
    providers: list[str] | None = None


class ProductEnrichmentDetail(BaseModel):
    """Per-product outcome of the apply-references pipeline (Phase 34C)."""

    product_id: str
    product_name: str
    outcome: str   # "nevo_matched" | "ciqual_matched" | "ai_matched" | "no_match"
                   # | "skipped_has_retailer_value" | "skipped_no_pt_fields"
    source: str | None = None          # "nevo" | "ciqual"
    reference_name: str | None = None  # matched entry name
    match_type: str | None = None      # "exact_name_en" | "exact_name_nl" | etc.
    has_split: bool = False            # True iff plant+animal split was stored


class ApplyReferencesResponse(BaseModel):
    # Deterministic matches (exact / alias on the reference table).
    nevo_matched: int
    nevo_with_split: int
    ciqual_matched: int
    # Phase 33I-AI — AI-assisted matches. AI never supplies nutrition
    # values; it only picks which NEVO/CIQUAL reference row to look up.
    nevo_ai_assisted_matched: int = 0
    nevo_ai_assisted_with_split: int = 0
    ciqual_ai_assisted_matched: int = 0
    ai_needs_review: int = 0
    no_match: int
    skipped_has_retailer_value: int
    skipped_no_pt_fields: int
    ai_enabled: bool = False
    ai_model: str | None = None
    # Phase 34C — per-product detail for wizard UI.
    # Phase 34T — CAPPED to APPLY_REFERENCES_DETAIL_LIMIT entries to keep
    # the response bounded at 10K-15K product scale. The wizard shows
    # "Showing first 100 of N"; the full counts are still exposed in
    # the diagnostic counters above.
    product_results: list[ProductEnrichmentDetail] = []
    product_results_total: int = 0
    # Phase 34D — diagnostic table size + warning so wizard can never
    # show a silent "0 matched" result. If the NEVO table is empty, the
    # wizard surfaces an admin-facing error message instead of an
    # ambiguous green "complete" state.
    nevo_total_references: int = 0
    ciqual_total_references: int = 0
    warning: str | None = None


#: Phase 34T — maximum number of per-product enrichment details we
#: serialise into a single ApplyReferencesResponse. Keeps the response
#: payload bounded for 10K-15K row CSVs; the totals in the counters
#: above expose the full aggregate.
APPLY_REFERENCES_DETAIL_LIMIT = 100


@api_router.post(
    "/projects/{project_id}/enrichments/apply-references",
    response_model=ApplyReferencesResponse,
)
def apply_reference_enrichment_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    body: ApplyReferencesRequest | None = None,
) -> ApplyReferencesResponse:
    """Apply NEVO (preferred) then CIQUAL enrichment to every PT product
    in the project that lacks a retailer-provided protein_pct. Altera-only.

    For each candidate product, exact case-insensitive match on
    ``product.product_name`` is tried against NEVO first (English then
    Dutch food names). On a NEVO match an enrichment record is stored
    for ``protein_pct``; if the entry also publishes PROTPL/PROTAN,
    sibling records are stored for ``plant_protein_pct`` and
    ``animal_protein_pct`` carrying the NEVO source. CIQUAL is tried
    only when NEVO does not match; CIQUAL never contributes a
    plant/animal split.
    """
    from altera_api.ai.config import get_nutrition_ai_provider
    from altera_api.ai.nutrition_candidates import candidates_for_product
    from altera_api.ai.nutrition_matcher import (
        build_product_card,
        propose_match,
    )
    from altera_api.domain.enrichment import (
        NutritionEnrichmentRecord,
        NutritionEnrichmentSource,
        NutritionEnrichmentStatus,
    )
    from altera_api.enrichment.providers.ciqual import CiqualProvider
    from altera_api.enrichment.providers.nevo import NevoProvider

    if not auth.can_apply_enrichment:
        raise_forbidden("altera internal access required")

    # Phase 34B — providers filter ("nevo", "ciqual"; None/empty = all).
    _body = body or ApplyReferencesRequest()
    _requested = {p.lower() for p in (_body.providers or [])} or {"nevo", "ciqual"}
    _run_nevo = "nevo" in _requested
    _run_ciqual = "ciqual" in _requested

    nevo_entries = store.list_nevo_entries() if _run_nevo else []
    ciqual_entries = store.list_ciqual_entries() if _run_ciqual else []
    nevo = NevoProvider.from_entries(nevo_entries) if nevo_entries else None
    ciqual = CiqualProvider.from_entries(ciqual_entries) if ciqual_entries else None

    # Phase 33I-AI — gated by AI_NUTRITION_MATCHING_ENABLED, OPENAI_API_KEY,
    # and ALTERA_AI_PROVIDER. None whenever any prerequisite is missing
    # → deterministic-only flow, no LLM calls.
    ai_provider = get_nutrition_ai_provider()

    # Reverse index lets us look up a reference row by (source, code)
    # after the AI picks one. The shortlist sent to the LLM is generated
    # from the same lists below, so any code AI returns is always
    # backed by a real row in this dict.
    nevo_by_code = {e.nevo_code: e for e in nevo_entries}
    ciqual_by_code = {e.source_food_code: e for e in ciqual_entries}

    now = datetime.now(UTC)
    counts: dict[str, int] = {
        "nevo_matched": 0,
        "nevo_with_split": 0,
        "ciqual_matched": 0,
        "nevo_ai_assisted_matched": 0,
        "nevo_ai_assisted_with_split": 0,
        "ciqual_ai_assisted_matched": 0,
        "ai_needs_review": 0,
        "no_match": 0,
        "skipped_has_retailer_value": 0,
        "skipped_no_pt_fields": 0,
    }
    product_results: list[ProductEnrichmentDetail] = []

    def _record(
        product_id: UUID,
        nutrient: str,
        value: Decimal,
        source: NutritionEnrichmentSource,
        confidence: Decimal,
        rationale: str,
        *,
        match_method: str = "deterministic",
        status: NutritionEnrichmentStatus = NutritionEnrichmentStatus.ENRICHED,
    ) -> NutritionEnrichmentRecord:
        return NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient=nutrient,
            original_value=None,
            enriched_value=value,
            unit="g_per_100g",
            source=source,
            confidence=confidence,
            status=status,
            rationale=rationale,
            created_at=now,
            created_by=auth.user_id,
            match_method=match_method,
        )

    def _apply_nevo_entry(
        product_id: UUID,
        entry,  # NevoEntry
        confidence: Decimal,
        rationale: str,
        *,
        match_method: str,
        with_split_counter: str,
    ) -> None:
        store.add_enrichment_record(
            _record(
                product_id,
                "protein_pct",
                entry.protein_g_per_100g,
                NutritionEnrichmentSource.NEVO,
                confidence,
                rationale,
                match_method=match_method,
            )
        )
        if (
            entry.plant_protein_g_per_100g is not None
            and entry.animal_protein_g_per_100g is not None
        ):
            store.add_enrichment_record(
                _record(
                    product_id,
                    "plant_protein_pct",
                    entry.plant_protein_g_per_100g,
                    NutritionEnrichmentSource.NEVO,
                    confidence,
                    f"{rationale}; PROTPL value",
                    match_method=match_method,
                )
            )
            store.add_enrichment_record(
                _record(
                    product_id,
                    "animal_protein_pct",
                    entry.animal_protein_g_per_100g,
                    NutritionEnrichmentSource.NEVO,
                    confidence,
                    f"{rationale}; PROTAN value",
                    match_method=match_method,
                )
            )
            counts[with_split_counter] += 1
        else:
            # Phase 34K — NEVO provided total protein but no plant/
            # animal split. Derive the split from the product's PT
            # classification when it is unambiguous:
            #   plant_based_core / plant_based_non_core  → 100% plant
            #   animal_core                               → 100% animal
            # Composite or unknown classifications are left to the
            # CIQUAL+AI fallback / manual review path so we never
            # silently invent a split for ambiguous products.
            classification = store.get_pt_classification(product_id)
            if classification is None:
                return
            pt_group = classification.pt_group.value
            plant_pct: Decimal | None = None
            animal_pct: Decimal | None = None
            if pt_group in ("plant_based_core", "plant_based_non_core"):
                plant_pct = entry.protein_g_per_100g
                animal_pct = Decimal("0")
            elif pt_group == "animal_core":
                plant_pct = Decimal("0")
                animal_pct = entry.protein_g_per_100g
            if plant_pct is None or animal_pct is None:
                return
            split_note = (
                f"{rationale}; classification_assumption split "
                f"({pt_group})"
            )
            store.add_enrichment_record(
                _record(
                    product_id,
                    "plant_protein_pct",
                    plant_pct,
                    NutritionEnrichmentSource.NEVO,
                    confidence,
                    split_note,
                    match_method=match_method,
                )
            )
            store.add_enrichment_record(
                _record(
                    product_id,
                    "animal_protein_pct",
                    animal_pct,
                    NutritionEnrichmentSource.NEVO,
                    confidence,
                    split_note,
                    match_method=match_method,
                )
            )
            counts[with_split_counter] += 1

    for product in store.list_products_for_project(project.id):
        if product.pt_fields is None:
            counts["skipped_no_pt_fields"] += 1
            product_results.append(
                ProductEnrichmentDetail(
                    product_id=str(product.id),
                    product_name=product.product_name,
                    outcome="skipped_no_pt_fields",
                )
            )
            continue
        if product.pt_fields.protein_pct is not None:
            counts["skipped_has_retailer_value"] += 1
            product_results.append(
                ProductEnrichmentDetail(
                    product_id=str(product.id),
                    product_name=product.product_name,
                    outcome="skipped_has_retailer_value",
                )
            )
            continue

        # NEVO first (deterministic).
        if nevo is not None:
            match = nevo.match(
                food_name=product.product_name, food_group=product.retailer_category
            )
            if (
                match is not None
                and match.entry.protein_g_per_100g is not None
                and match.match_type != "food_group_average"
            ):
                # Phase 34V — family-compatibility guard. Fuzzy token
                # matching produced ~40% precision in production
                # (Corn Flakes ↔ chicken schnitzel, Vinaigre ↔ pork
                # brawn, etc.). Reject matches where the retailer
                # product and the NEVO candidate live in incompatible
                # food families. Wrong nutrition is worse than
                # missing nutrition for the calculation.
                from altera_api.enrichment.family_guard import (
                    is_family_compatible,
                    nevo_candidate_family,
                    product_family,
                )

                p_family = product_family(product.product_name)
                c_family = nevo_candidate_family(
                    match.entry.food_group, match.entry.food_name_en
                )
                if not is_family_compatible(p_family, c_family):
                    # Record a FAILED enrichment with a clear rationale so
                    # the wizard's NEVO panel can explain WHY a candidate
                    # was rejected ("Sans correspondance" with reason).
                    store.add_enrichment_record(
                        _record(
                            product.id,
                            "protein_pct",
                            None,
                            NutritionEnrichmentSource.NEVO,
                            None,
                            (
                                f"NEVO candidate rejected: "
                                f"product family {p_family.value!r} "
                                f"incompatible with candidate family "
                                f"{c_family.value!r} "
                                f"(candidate {match.entry.food_name_en!r})"
                            ),
                            status=NutritionEnrichmentStatus.FAILED,
                            match_method="none",
                        )
                    )
                    counts["no_match"] += 1
                    product_results.append(
                        ProductEnrichmentDetail(
                            product_id=str(product.id),
                            product_name=product.product_name,
                            outcome="no_match",
                            source=None,
                            reference_name=None,
                            match_type="family_mismatch",
                            has_split=False,
                        )
                    )
                    continue
                rationale = (
                    f"NEVO {match.entry.source_version}: {match.match_type} "
                    f"match on {match.entry.food_name_en!r} "
                    f"(code {match.entry.nevo_code})"
                )
                has_split = (
                    match.entry.plant_protein_g_per_100g is not None
                    and match.entry.animal_protein_g_per_100g is not None
                )
                _apply_nevo_entry(
                    product.id,
                    match.entry,
                    match.confidence,
                    rationale,
                    match_method="deterministic",
                    with_split_counter="nevo_with_split",
                )
                counts["nevo_matched"] += 1
                product_results.append(
                    ProductEnrichmentDetail(
                        product_id=str(product.id),
                        product_name=product.product_name,
                        outcome="nevo_matched",
                        source="nevo",
                        reference_name=match.entry.food_name_en,
                        match_type=match.match_type,
                        has_split=has_split,
                    )
                )
                continue

        # CIQUAL fallback (deterministic, total protein only — no split).
        if ciqual is not None:
            c_match = ciqual.match(
                food_name=product.product_name, food_group=product.retailer_category
            )
            if (
                c_match is not None
                and c_match.entry.protein_g_per_100g is not None
                and c_match.match_type != "food_group_average"
            ):
                rationale = (
                    f"CIQUAL {c_match.entry.source_version}: {c_match.match_type} "
                    f"match on {c_match.entry.food_name_en!r} "
                    f"(code {c_match.entry.source_food_code})"
                )
                store.add_enrichment_record(
                    _record(
                        product.id,
                        "protein_pct",
                        c_match.entry.protein_g_per_100g,
                        NutritionEnrichmentSource.CIQUAL,
                        c_match.confidence,
                        rationale,
                    )
                )
                counts["ciqual_matched"] += 1
                product_results.append(
                    ProductEnrichmentDetail(
                        product_id=str(product.id),
                        product_name=product.product_name,
                        outcome="ciqual_matched",
                        source="ciqual",
                        reference_name=c_match.entry.food_name_en,
                        match_type=c_match.match_type,
                    )
                )
                continue

        # Phase 33I-AI fallback — only when AI is enabled AND we can
        # build a deterministic candidate shortlist (no shortlist → no
        # call, saves cost and grounds the LLM).
        if ai_provider is not None:
            candidates = candidates_for_product(
                product_name=product.product_name,
                retailer_category=product.retailer_category,
                nevo_entries=nevo_entries,
                ciqual_entries=ciqual_entries,
            )
            if candidates:
                product_card = build_product_card(
                    product_name=product.product_name,
                    brand=product.brand,
                    retailer_category=product.retailer_category,
                    retailer_subcategory=product.retailer_subcategory,
                    ingredients_text=product.ingredients_text,
                    labels=product.labels,
                    language=product.language,
                    country=product.country,
                )
                proposal = propose_match(
                    product_card=product_card,
                    candidates=candidates,
                    provider=ai_provider,
                )
                if proposal.decision == "match":
                    rationale = (
                        f"AI-assisted {proposal.source}: matched "
                        f"{proposal.reference_name!r} "
                        f"(code {proposal.reference_code}); "
                        f"ai_model={proposal.ai_model}; "
                        f"ai_confidence={proposal.confidence:.2f}; "
                        f"reason={proposal.reason}"
                    )
                    confidence_dec = Decimal(str(round(proposal.confidence, 4)))
                    if proposal.source == "nevo":
                        entry = nevo_by_code.get(proposal.reference_code)
                        if entry is None or entry.protein_g_per_100g is None:
                            counts["no_match"] += 1
                            product_results.append(
                                ProductEnrichmentDetail(
                                    product_id=str(product.id),
                                    product_name=product.product_name,
                                    outcome="no_match",
                                )
                            )
                            continue
                        has_split = (
                            entry.plant_protein_g_per_100g is not None
                            and entry.animal_protein_g_per_100g is not None
                        )
                        _apply_nevo_entry(
                            product.id,
                            entry,
                            confidence_dec,
                            rationale,
                            match_method="ai_assisted",
                            with_split_counter="nevo_ai_assisted_with_split",
                        )
                        counts["nevo_ai_assisted_matched"] += 1
                        product_results.append(
                            ProductEnrichmentDetail(
                                product_id=str(product.id),
                                product_name=product.product_name,
                                outcome="nevo_matched",
                                source="nevo",
                                reference_name=proposal.reference_name,
                                match_type="ai_assisted",
                                has_split=has_split,
                            )
                        )
                        continue
                    if proposal.source == "ciqual":
                        c_entry = ciqual_by_code.get(proposal.reference_code)
                        if c_entry is None or c_entry.protein_g_per_100g is None:
                            counts["no_match"] += 1
                            product_results.append(
                                ProductEnrichmentDetail(
                                    product_id=str(product.id),
                                    product_name=product.product_name,
                                    outcome="no_match",
                                )
                            )
                            continue
                        store.add_enrichment_record(
                            _record(
                                product.id,
                                "protein_pct",
                                c_entry.protein_g_per_100g,
                                NutritionEnrichmentSource.CIQUAL,
                                confidence_dec,
                                rationale,
                                match_method="ai_assisted",
                            )
                        )
                        counts["ciqual_ai_assisted_matched"] += 1
                        product_results.append(
                            ProductEnrichmentDetail(
                                product_id=str(product.id),
                                product_name=product.product_name,
                                outcome="ciqual_matched",
                                source="ciqual",
                                reference_name=proposal.reference_name,
                                match_type="ai_assisted",
                            )
                        )
                        continue
                elif proposal.decision == "needs_review":
                    # Persist a NEEDS_MANUAL_REVIEW record so the
                    # analyst can confirm — but do NOT feed the value
                    # into the calculation.
                    proposed_source = (
                        NutritionEnrichmentSource.NEVO
                        if proposal.source == "nevo"
                        else NutritionEnrichmentSource.CIQUAL
                    )
                    store.add_enrichment_record(
                        _record(
                            product.id,
                            "protein_pct",
                            None,  # value withheld until reviewer confirms
                            proposed_source,
                            Decimal(str(round(proposal.confidence, 4))),
                            (
                                f"AI proposed {proposal.source} code "
                                f"{proposal.reference_code} ({proposal.reference_name!r}) "
                                f"at confidence {proposal.confidence:.2f}; "
                                f"needs manual review. ai_model={proposal.ai_model}; "
                                f"reason={proposal.reason}"
                            ),
                            match_method="ai_assisted",
                            status=NutritionEnrichmentStatus.NEEDS_MANUAL_REVIEW,
                        )
                    )
                    counts["ai_needs_review"] += 1
                    product_results.append(
                        ProductEnrichmentDetail(
                            product_id=str(product.id),
                            product_name=product.product_name,
                            outcome="ai_needs_review",
                            source=proposal.source,
                            reference_name=proposal.reference_name,
                            match_type="ai_needs_review",
                        )
                    )
                    continue

        counts["no_match"] += 1
        product_results.append(
            ProductEnrichmentDetail(
                product_id=str(product.id),
                product_name=product.product_name,
                outcome="no_match",
            )
        )
        # Phase 34M — record a FAILED enrichment record on no-match
        # products too. This gives the workflow aggregator a way to
        # tell "NEVO has been attempted on this product" apart from
        # "NEVO has never run", so Step 5 can flip to complete after
        # the user's first run even when nothing matched.
        from altera_api.domain.enrichment import (
            NutritionEnrichmentRecord as _NER,
        )
        from altera_api.domain.enrichment import (
            NutritionEnrichmentSource as _NES,
        )
        from altera_api.domain.enrichment import (
            NutritionEnrichmentStatus as _NSt,
        )
        store.add_enrichment_record(
            _NER(
                product_id=product.id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=None,
                unit="g_per_100g",
                source=_NES.NEVO,
                confidence=None,
                status=_NSt.FAILED,
                rationale=(
                    "NEVO: no matching entry found "
                    "(deterministic + fuzzy + AI all returned no candidate)"
                ),
                created_at=now,
                created_by=auth.user_id,
                match_method="none",
            )
        )

    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.ENRICHMENT_APPLIED,
            target_table="nutrition_enrichment_records",
            target_id=project.id,
            metadata={
                "summary": counts,
                "scope": "apply_references",
                "ai_enabled": ai_provider is not None,
                "ai_model": ai_provider.model if ai_provider is not None else None,
            },
            created_at=datetime.now(UTC),
        )
    )
    nevo_total = len(nevo_entries)
    ciqual_total = len(ciqual_entries)
    total_matched = (
        counts["nevo_matched"]
        + counts["ciqual_matched"]
        + counts["nevo_ai_assisted_matched"]
        + counts["ciqual_ai_assisted_matched"]
    )
    attempted = total_matched + counts["no_match"] + counts["ai_needs_review"]
    warning: str | None = None
    if _run_nevo and nevo_total == 0:
        warning = (
            "Aucun produit n’a été enrichi par NEVO : la table de référence NEVO "
            "est vide sur ce serveur. Vérifier que la table nevo_reference est "
            "peuplée (script scripts/import_nevo.py) et que la connexion "
            "Supabase est correctement configurée."
        )
    elif attempted > 0 and total_matched == 0:
        warning = (
            "Aucun produit n’a été enrichi : aucun nom de produit n’a trouvé de "
            "correspondance dans NEVO ni CIQUAL. Vérifier les noms (langue, "
            "fautes, format) ou activer le matching IA "
            "(AI_NUTRITION_MATCHING_ENABLED)."
        )
    return ApplyReferencesResponse(
        **counts,
        ai_enabled=ai_provider is not None,
        ai_model=ai_provider.model if ai_provider is not None else None,
        # Phase 34T — cap per-product details so a 10K-row enrichment
        # response stays under a few hundred KB. The aggregate counts
        # remain in the response top-level so the wizard's diagnostics
        # panel never loses fidelity for large CSVs.
        product_results=product_results[:APPLY_REFERENCES_DETAIL_LIMIT],
        product_results_total=len(product_results),
        nevo_total_references=nevo_total,
        ciqual_total_references=ciqual_total,
        warning=warning,
    )


# ---------------------------------------------------------------------------
# WWF Step 2 ingredient upload (Phase 24A)
# ---------------------------------------------------------------------------


class WWFIngredientResponse(BaseModel):
    product_id: UUID
    food_group: str
    fg1_subgroup: str | None
    fg2_subgroup: str | None
    fg3_subgroup: str | None
    fg5_grain_kind: str | None
    ingredient_weight_kg_per_item: str


class WWFIngredientRowErrorResponse(BaseModel):
    ingredient_index: int
    field: str
    message: str


class WWFIngredientProductResultResponse(BaseModel):
    external_product_id: str
    product_id: UUID | None
    is_own_brand: bool | None
    is_composite: bool | None
    ingredient_count: int
    valid_ingredient_count: int
    total_attributed_weight_kg: str
    product_weight_kg: str | None
    residual_weight_kg: str | None
    errors: list[WWFIngredientRowErrorResponse]
    warnings: list[str]


class WWFStep2UploadResponse(BaseModel):
    total_products_in_file: int
    valid_product_count: int
    error_count: int
    warning_count: int
    unknown_product_count: int
    branded_composite_count: int
    stored: bool
    replaced: bool
    product_results: list[WWFIngredientProductResultResponse]


@api_router.post(
    "/projects/{project_id}/wwf-ingredients/upload",
    response_model=WWFStep2UploadResponse,
)
async def upload_wwf_step2_ingredients(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    file: Annotated[UploadFile, File(description="WWF Step 2 JSON companion file")],
) -> WWFStep2UploadResponse:
    """Upload and validate a WWF Step 2 ingredient file.

    Accepts a JSON file keyed by ``external_product_id``. Valid own-brand
    composite ingredients are stored immediately; validation errors are
    returned in the response. Branded composites receive a warning and are
    not stored.

    A successful upload **replaces** any previously stored Step 2 ingredients
    for the project. If validation fails, old records are preserved.

    Limits: 50 MB file, 200,000 total ingredient rows.

    Requires WWF to be enabled on the project. Accessible to project
    members (GMS clients can upload for their own project).
    """
    import json as _json

    from altera_api.domain.common import Methodology
    from altera_api.ingestion.validators import MAX_UPLOAD_BYTES
    from altera_api.ingestion.wwf_step2 import (
        MAX_STEP2_INGREDIENT_ROWS,
        validate_wwf_step2_json,
    )

    if Methodology.WWF not in project.methodologies_enabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="WWF methodology is not enabled on this project",
        )

    if not file.filename:
        raise HTTPException(status_code=400, detail="file is required")

    payload = await file.read()

    # File-size guard
    if len(payload) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"ingredient file exceeds {mb} MB limit ({len(payload):,} bytes)",
        )

    try:
        raw = _json.loads(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="file must be valid JSON") from exc

    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=400,
            detail="JSON must be an object keyed by external_product_id",
        )

    # Row-count guard (count across all product entries)
    total_rows = sum(
        len(v["ingredients"])
        for v in raw.values()
        if isinstance(v, dict) and isinstance(v.get("ingredients"), list)
    )
    if total_rows > MAX_STEP2_INGREDIENT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ingredient file exceeds {MAX_STEP2_INGREDIENT_ROWS:,} row limit",
        )

    products = store.list_products_for_project(project.id)
    products_by_external_id = {p.external_product_id: p for p in products}
    classifications = {
        p.id: c
        for p in products
        if (c := store.get_wwf_classification(p.id)) is not None
    }

    result = validate_wwf_step2_json(
        raw,
        products_by_external_id=products_by_external_id,
        classifications=classifications,
    )

    stored = False
    replaced = False
    if result.is_valid:
        # Re-upload semantics: clear old records before storing new ones.
        existing = store.get_wwf_ingredients_by_project(project.id)
        replaced = bool(existing)
        store.clear_wwf_ingredients_for_project(project.id)
        for product_id, ingredients in result.all_valid_ingredients:
            store.upsert_wwf_ingredients_for_product(product_id, ingredients)
        stored = True

    return WWFStep2UploadResponse(
        total_products_in_file=result.total_products_in_file,
        valid_product_count=result.valid_product_count,
        error_count=result.error_count,
        warning_count=result.warning_count,
        unknown_product_count=result.unknown_product_count,
        branded_composite_count=result.branded_composite_count,
        stored=stored,
        replaced=replaced,
        product_results=[
            WWFIngredientProductResultResponse(
                external_product_id=pr.external_product_id,
                product_id=pr.product_id,
                is_own_brand=pr.is_own_brand,
                is_composite=pr.is_composite,
                ingredient_count=pr.ingredient_count,
                valid_ingredient_count=pr.valid_ingredient_count,
                total_attributed_weight_kg=str(pr.total_attributed_weight_kg),
                product_weight_kg=(
                    str(pr.product_weight_kg) if pr.product_weight_kg is not None else None
                ),
                residual_weight_kg=(
                    str(pr.residual_weight_kg) if pr.residual_weight_kg is not None else None
                ),
                errors=[
                    WWFIngredientRowErrorResponse(
                        ingredient_index=e.ingredient_index,
                        field=e.field,
                        message=e.message,
                    )
                    for e in pr.errors
                ],
                warnings=list(pr.warnings),
            )
            for pr in result.product_results
        ],
    )


@api_router.get(
    "/projects/{project_id}/products/{product_id}/wwf-ingredients",
    response_model=list[WWFIngredientResponse],
)
def list_wwf_ingredients_route(
    product_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[WWFIngredientResponse]:
    """List stored WWF Step 2 ingredients for a product."""
    product = store.get_product(product_id)
    if product is None or product.project_id != project.id:
        raise HTTPException(status_code=404, detail="product not found")

    ingredients = store.get_wwf_ingredients_for_product(product_id)
    return [
        WWFIngredientResponse(
            product_id=ing.parent_product_id,
            food_group=ing.food_group.value,
            fg1_subgroup=ing.fg1_subgroup.value if ing.fg1_subgroup else None,
            fg2_subgroup=ing.fg2_subgroup.value if ing.fg2_subgroup else None,
            fg3_subgroup=ing.fg3_subgroup.value if ing.fg3_subgroup else None,
            fg5_grain_kind=ing.fg5_grain_kind.value if ing.fg5_grain_kind else None,
            ingredient_weight_kg_per_item=str(ing.ingredient_weight_kg_per_item),
        )
        for ing in ingredients
    ]


# ---------------------------------------------------------------------------
# Run comparisons (Phase 27A)
# ---------------------------------------------------------------------------


class PTGroupComparisonResponse(BaseModel):
    pt_group: str
    baseline_protein_kg: str
    comparison_protein_kg: str
    delta_protein_kg: str


class PTComparisonSummaryResponse(BaseModel):
    baseline_reporting_period: str
    comparison_reporting_period: str
    baseline_methodology_version: str
    comparison_methodology_version: str
    baseline_taxonomy_version: str
    comparison_taxonomy_version: str
    baseline_rules_version: str
    comparison_rules_version: str
    baseline_plant_protein_kg: str
    baseline_animal_protein_kg: str
    baseline_total_protein_kg: str
    baseline_plant_share_pct: str | None
    baseline_animal_share_pct: str | None
    comparison_plant_protein_kg: str
    comparison_animal_protein_kg: str
    comparison_total_protein_kg: str
    comparison_plant_share_pct: str | None
    comparison_animal_share_pct: str | None
    delta_plant_protein_kg: str
    delta_animal_protein_kg: str
    delta_total_protein_kg: str
    delta_plant_share_pct: str | None
    delta_animal_share_pct: str | None
    direction: str
    per_group: list[PTGroupComparisonResponse]


class WWFFoodGroupComparisonResponse(BaseModel):
    food_group: str
    baseline_weight_kg: str
    comparison_weight_kg: str
    delta_weight_kg: str
    baseline_share_pct: str
    comparison_share_pct: str
    delta_share_pct: str
    phd_reference_share_pct: str | None


class WWFComparisonSummaryResponse(BaseModel):
    baseline_reporting_period: str
    comparison_reporting_period: str
    baseline_methodology_version: str
    comparison_methodology_version: str
    baseline_taxonomy_version: str
    comparison_taxonomy_version: str
    baseline_rules_version: str
    comparison_rules_version: str
    baseline_total_weight_kg: str
    comparison_total_weight_kg: str
    delta_total_weight_kg: str
    baseline_plant_weight_kg: str
    comparison_plant_weight_kg: str
    delta_plant_weight_kg: str
    baseline_animal_weight_kg: str
    comparison_animal_weight_kg: str
    delta_animal_weight_kg: str
    direction: str
    per_food_group: list[WWFFoodGroupComparisonResponse]


class RunComparisonResponse(BaseModel):
    baseline_run_id: UUID
    comparison_run_id: UUID
    project_id: UUID
    methodology: str
    pt_comparison: PTComparisonSummaryResponse | None
    wwf_comparison: WWFComparisonSummaryResponse | None
    warnings: list[str]
    created_at: str


def _comparison_response(result: object) -> RunComparisonResponse:
    from altera_api.domain.comparison import RunComparisonResult

    r: RunComparisonResult = result  # type: ignore[assignment]

    pt_resp = None
    if r.pt_comparison is not None:
        p = r.pt_comparison
        pt_resp = PTComparisonSummaryResponse(
            baseline_reporting_period=p.baseline_reporting_period,
            comparison_reporting_period=p.comparison_reporting_period,
            baseline_methodology_version=p.baseline_methodology_version,
            comparison_methodology_version=p.comparison_methodology_version,
            baseline_taxonomy_version=p.baseline_taxonomy_version,
            comparison_taxonomy_version=p.comparison_taxonomy_version,
            baseline_rules_version=p.baseline_rules_version,
            comparison_rules_version=p.comparison_rules_version,
            baseline_plant_protein_kg=str(p.baseline_plant_protein_kg),
            baseline_animal_protein_kg=str(p.baseline_animal_protein_kg),
            baseline_total_protein_kg=str(p.baseline_total_protein_kg),
            baseline_plant_share_pct=str(p.baseline_plant_share_pct) if p.baseline_plant_share_pct is not None else None,
            baseline_animal_share_pct=str(p.baseline_animal_share_pct) if p.baseline_animal_share_pct is not None else None,
            comparison_plant_protein_kg=str(p.comparison_plant_protein_kg),
            comparison_animal_protein_kg=str(p.comparison_animal_protein_kg),
            comparison_total_protein_kg=str(p.comparison_total_protein_kg),
            comparison_plant_share_pct=str(p.comparison_plant_share_pct) if p.comparison_plant_share_pct is not None else None,
            comparison_animal_share_pct=str(p.comparison_animal_share_pct) if p.comparison_animal_share_pct is not None else None,
            delta_plant_protein_kg=str(p.delta_plant_protein_kg),
            delta_animal_protein_kg=str(p.delta_animal_protein_kg),
            delta_total_protein_kg=str(p.delta_total_protein_kg),
            delta_plant_share_pct=str(p.delta_plant_share_pct) if p.delta_plant_share_pct is not None else None,
            delta_animal_share_pct=str(p.delta_animal_share_pct) if p.delta_animal_share_pct is not None else None,
            direction=p.direction,
            per_group=[
                PTGroupComparisonResponse(
                    pt_group=g.pt_group,
                    baseline_protein_kg=str(g.baseline_protein_kg),
                    comparison_protein_kg=str(g.comparison_protein_kg),
                    delta_protein_kg=str(g.delta_protein_kg),
                )
                for g in p.per_group
            ],
        )

    wwf_resp = None
    if r.wwf_comparison is not None:
        w = r.wwf_comparison
        wwf_resp = WWFComparisonSummaryResponse(
            baseline_reporting_period=w.baseline_reporting_period,
            comparison_reporting_period=w.comparison_reporting_period,
            baseline_methodology_version=w.baseline_methodology_version,
            comparison_methodology_version=w.comparison_methodology_version,
            baseline_taxonomy_version=w.baseline_taxonomy_version,
            comparison_taxonomy_version=w.comparison_taxonomy_version,
            baseline_rules_version=w.baseline_rules_version,
            comparison_rules_version=w.comparison_rules_version,
            baseline_total_weight_kg=str(w.baseline_total_weight_kg),
            comparison_total_weight_kg=str(w.comparison_total_weight_kg),
            delta_total_weight_kg=str(w.delta_total_weight_kg),
            baseline_plant_weight_kg=str(w.baseline_plant_weight_kg),
            comparison_plant_weight_kg=str(w.comparison_plant_weight_kg),
            delta_plant_weight_kg=str(w.delta_plant_weight_kg),
            baseline_animal_weight_kg=str(w.baseline_animal_weight_kg),
            comparison_animal_weight_kg=str(w.comparison_animal_weight_kg),
            delta_animal_weight_kg=str(w.delta_animal_weight_kg),
            direction=w.direction,
            per_food_group=[
                WWFFoodGroupComparisonResponse(
                    food_group=fg.food_group,
                    baseline_weight_kg=str(fg.baseline_weight_kg),
                    comparison_weight_kg=str(fg.comparison_weight_kg),
                    delta_weight_kg=str(fg.delta_weight_kg),
                    baseline_share_pct=str(fg.baseline_share_pct),
                    comparison_share_pct=str(fg.comparison_share_pct),
                    delta_share_pct=str(fg.delta_share_pct),
                    phd_reference_share_pct=str(fg.phd_reference_share_pct)
                    if fg.phd_reference_share_pct is not None
                    else None,
                )
                for fg in w.per_food_group
            ],
        )

    return RunComparisonResponse(
        baseline_run_id=r.baseline_run_id,
        comparison_run_id=r.comparison_run_id,
        project_id=r.project_id,
        methodology=r.methodology,
        pt_comparison=pt_resp,
        wwf_comparison=wwf_resp,
        warnings=r.warnings,
        created_at=r.created_at.isoformat(),
    )


@api_router.get(
    "/projects/{project_id}/comparisons",
    response_model=RunComparisonResponse,
)
def get_run_comparison_route(
    baseline_run_id: Annotated[UUID, Query(...)],
    comparison_run_id: Annotated[UUID, Query(...)],
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> RunComparisonResponse:
    """Compare two runs of the same methodology for a project.

    Altera users can compare any two runs.
    Client users require an approved or delivered export for each run.
    Cross-organisation access is blocked by get_project (404 for clients).
    """
    if baseline_run_id == comparison_run_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="baseline_run_id and comparison_run_id must differ.",
        )

    base_run = store.get_run(baseline_run_id)
    if base_run is None or base_run.project_id != project.id:
        raise HTTPException(status_code=404, detail="baseline run not found")

    comp_run = store.get_run(comparison_run_id)
    if comp_run is None or comp_run.project_id != project.id:
        raise HTTPException(status_code=404, detail="comparison run not found")

    if base_run.methodology != comp_run.methodology:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Runs have different methodologies: "
                f"{base_run.methodology.value!r} (baseline) vs "
                f"{comp_run.methodology.value!r} (comparison). "
                "Compare runs of the same methodology only."
            ),
        )

    # Client users: both runs must have an approved or delivered export.
    if not auth.is_altera_internal:
        for run_obj, label in [(base_run, "baseline"), (comp_run, "comparison")]:
            exports = store.get_exports_for_run(run_obj.id)
            if not any(e.approval_status in _CLIENT_VISIBLE_STATUSES for e in exports):
                raise_forbidden(
                    f"No approved export available for the {label} run. "
                    "Run comparisons are only available once both reports "
                    "have been approved or delivered."
                )

    from altera_api.comparisons.engine import build_run_comparison

    try:
        result = build_run_comparison(base_run, comp_run)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not compute comparison: {exc}",
        ) from exc

    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=auth.user_id,
            action=AuditEventType.COMPARISON_REQUESTED,
            target_table="runs",
            target_id=baseline_run_id,
            metadata={
                "baseline_run_id": str(baseline_run_id),
                "comparison_run_id": str(comparison_run_id),
            },
            created_at=datetime.now(UTC),
        )
    )

    return _comparison_response(result)


# ---------------------------------------------------------------------------
# Phase 35-stale — admin endpoints for diagnosing + healing stale
# heavy jobs. Altera-internal staff only. The heavy-job guard
# already self-heals opportunistically on every blocked create, but
# these endpoints give support an explicit "list + reset" pair when
# a customer reports a stuck wizard.
# ---------------------------------------------------------------------------


class _HeavyJobActiveItem(BaseModel):
    job_id: UUID
    kind: str  # "classification" | "ingestion"
    project_id: UUID
    upload_id: UUID
    status: str
    total: int
    processed: int
    age_seconds: float
    updated_at: str | None


class _HeavyJobActiveResponse(BaseModel):
    classification: list[_HeavyJobActiveItem]
    ingestion: list[_HeavyJobActiveItem]
    stale_after_minutes: int


@api_router.get(
    "/admin/heavy-jobs/active",
    response_model=_HeavyJobActiveResponse,
)
def admin_heavy_jobs_active(
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> _HeavyJobActiveResponse:
    """List queued/running heavy jobs. Altera-internal only."""
    if not auth.is_altera_internal:
        raise_forbidden("admin endpoint")
    now = datetime.now(UTC)

    def _age(updated_at) -> float:  # type: ignore[no-untyped-def]
        if updated_at is None:
            return 0.0
        return (now - updated_at).total_seconds()

    try:
        class_jobs = store.list_active_heavy_classification_jobs(
            min_total_products=_HEAVY_CLASSIFICATION_THRESHOLD
        )
    except Exception:
        class_jobs = []
    try:
        ingest_jobs = store.list_active_heavy_ingestion_jobs(
            min_total_rows=_HEAVY_INGESTION_THRESHOLD
        )
    except Exception:
        ingest_jobs = []

    return _HeavyJobActiveResponse(
        classification=[
            _HeavyJobActiveItem(
                job_id=j.id,
                kind="classification",
                project_id=j.project_id,
                upload_id=j.upload_id,
                status=j.status.value,
                total=j.total_products,
                processed=j.processed_products,
                age_seconds=_age(j.updated_at),
                updated_at=j.updated_at.isoformat() if j.updated_at else None,
            )
            for j in class_jobs
        ],
        ingestion=[
            _HeavyJobActiveItem(
                job_id=j.id,
                kind="ingestion",
                project_id=j.project_id,
                upload_id=j.upload_id,
                status=j.status.value,
                total=j.total_rows,
                processed=j.processed_rows,
                age_seconds=_age(j.updated_at),
                updated_at=j.updated_at.isoformat() if j.updated_at else None,
            )
            for j in ingest_jobs
        ],
        stale_after_minutes=_HEAVY_JOB_STALE_MINUTES,
    )


class _HeavyJobCancelStaleResponse(BaseModel):
    cancelled_classification: int
    cancelled_ingestion: int
    stale_after_minutes: int


@api_router.post(
    "/admin/heavy-jobs/cancel-stale",
    response_model=_HeavyJobCancelStaleResponse,
)
def admin_heavy_jobs_cancel_stale(
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    stale_after_minutes: int | None = None,
) -> _HeavyJobCancelStaleResponse:
    """Force-terminalise stale heavy jobs. Altera-internal only.

    ``stale_after_minutes`` query param overrides the default cutoff
    (``ALTERA_HEAVY_JOB_STALE_MINUTES``). Useful when an operator
    wants to clean up jobs that are 5 minutes old without waiting
    for the full 30-minute heuristic.
    """
    if not auth.is_altera_internal:
        raise_forbidden("admin endpoint")
    cutoff = (
        stale_after_minutes
        if stale_after_minutes is not None
        else _HEAVY_JOB_STALE_MINUTES
    )
    if cutoff < 1:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_stale_minutes",
                "message": "stale_after_minutes must be >= 1",
            },
        )
    try:
        n_class = store.cancel_stale_classification_jobs(
            stale_after_minutes=cutoff
        )
    except Exception:
        n_class = 0
    try:
        n_ingest = store.cancel_stale_ingestion_jobs(
            stale_after_minutes=cutoff
        )
    except Exception:
        n_ingest = 0
    import logging

    logging.getLogger("altera_api.heavy_job_guard").info(
        "admin.cancel_stale actor=%s cutoff_min=%d "
        "cancelled_classification=%d cancelled_ingestion=%d",
        auth.user_id,
        cutoff,
        n_class,
        n_ingest,
    )
    return _HeavyJobCancelStaleResponse(
        cancelled_classification=n_class,
        cancelled_ingestion=n_ingest,
        stale_after_minutes=cutoff,
    )
