"""HTTP routes — projects, uploads, classify, review, runs, exports.

All routes are mounted under ``/api/v1``. Request and response bodies
are Pydantic models defined inline; they're intentionally narrower than
the full domain models so the wire contract is stable even as the
domain evolves.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, Field

from altera_api.api.dependencies import current_user_id, get_data_store, get_project
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


def _project_response(store: StoreProtocol, project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        organisation_id=project.organisation_id,
        name=project.name,
        methodologies_enabled=sorted(m.value for m in project.methodologies_enabled),
        reporting_period_label=project.reporting_period_label,
        pt_validation_status=project.pt_validation_status.value,
        upload_count=len(store.list_uploads_for_project(project.id)),
        review_queue_count=len(store.list_review_items_for_project(project.id)),
        run_count=len(store.list_runs_for_project(project.id)),
    )


@api_router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreateRequest,
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_data_store),
) -> ProjectResponse:
    if not auth.can_write_data:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="creating projects requires analyst, admin, or owner",
        )
    project = store.create_project(
        name=body.name,
        methodologies_enabled=frozenset(body.methodologies_enabled),
        reporting_period_label=body.reporting_period_label,
        organisation_id=auth.organisation_id,
        created_by=auth.user_id,
    )
    return _project_response(store, project)


@api_router.get("/projects", response_model=list[ProjectResponse])
def list_projects_route(
    auth: AuthContext = Depends(authed_user),
    store: StoreProtocol = Depends(get_data_store),
) -> list[ProjectResponse]:
    projects = store.list_projects()
    if not auth.is_altera_internal:
        projects = [p for p in projects if p.organisation_id == auth.organisation_id]
    return [_project_response(store, p) for p in projects]


@api_router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project_route(
    project: Project = Depends(get_project),
    store: StoreProtocol = Depends(get_data_store),
) -> ProjectResponse:
    return _project_response(store, project)


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
    errors: list[ValidationEntryResponse]
    warnings: list[ValidationEntryResponse]
    # Phase 15 metadata
    file_size_bytes: int | None = None
    checksum_sha256: str | None = None
    duplicate_of: UUID | None = None
    validation_started_at: str | None = None
    validation_completed_at: str | None = None
    ingestion_started_at: str | None = None
    ingestion_completed_at: str | None = None


def _upload_response(summary: IngestSummary) -> UploadResponse:
    u = summary.upload
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
            for e in summary.report.errors
        ],
        warnings=[
            ValidationEntryResponse(
                row_number=w.row_number, field=w.field, code=w.code, message=w.message
            )
            for w in summary.report.warnings
        ],
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
) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="file is required")
    payload = await file.read()
    pre_errors = validate_upload(file.filename, payload, content_type=file.content_type)
    if pre_errors:
        raise HTTPException(status_code=400, detail="; ".join(pre_errors))
    summary = ingest_upload(
        store,
        project=project,
        file_bytes=payload,
        original_filename=file.filename,
        uploaded_by=user_id,
        content_type=file.content_type,
    )
    return _upload_response(summary)


@api_router.get(
    "/projects/{project_id}/uploads",
    response_model=list[UploadResponse],
)
def list_uploads_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> list[UploadResponse]:
    return [
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
    )
    return _upload_response(summary)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
class ClassifyRequest(BaseModel):
    methodology: Methodology


class ClassifyResponse(BaseModel):
    methodology: str
    matched: int
    pass_through: int
    rule_collision: int
    queued_for_review: int


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
    try:
        summary = classify_upload(
            store, project=project, upload_id=upload_id, methodology=body.methodology
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ClassifyResponse(
        methodology=summary.methodology.value,
        matched=summary.matched,
        pass_through=summary.pass_through,
        rule_collision=summary.rule_collision,
        queued_for_review=summary.queued_for_review,
    )


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


class DecisionRequest(BaseModel):
    decision: Literal["accepted", "changed", "deferred"]
    to_category: str | None = None
    reason: str | None = None


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
    response_model=list[ReviewItemResponse],
)
def list_review_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
    methodology: Methodology | None = None,
    status: ManualReviewStatus | None = None,
    reason: ManualReviewQueueReason | None = None,
    priority_level: ManualReviewPriority | None = None,
    upload_id: UUID | None = None,
    product_search: str | None = None,
    sort: Literal["oldest", "newest", "priority"] = "oldest",
) -> list[ReviewItemResponse]:
    """List review items for a project with optional filtering and sorting.

    Filters: methodology, status, reason, priority_level, upload_id,
    product_search (name or external_product_id substring, case-insensitive).

    Sort: oldest (default) | newest | priority (critical first).
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
    return [_review_response(v) for v in views]


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera staff can submit review decisions",
        )
    user_id = auth.user_id
    try:
        view = submit_decision(
            store,
            product_id=product_id,
            methodology=methodology,
            decision=body.decision,
            reviewer_user_id=user_id,
            to_category=body.to_category,
            reason=body.reason,
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="only Altera staff can claim review items"
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera staff can release review items",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera staff can refresh review locks",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera staff can assign review items",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera staff can submit review decisions",
        )
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
    use_enriched_nutrition: bool = False


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
    if body.use_enriched_nutrition and not auth.is_altera_internal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="use_enriched_nutrition may only be enabled by Altera internal users",
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
    return RunResponse(
        id=record.id,
        project_id=record.project_id,
        methodology=record.methodology.value,
        rows_count=record.rows_count,
        started_at=record.started_at.isoformat(),
        finished_at=record.finished_at.isoformat() if record.finished_at else None,
        summary=record.summary_payload,
    )


@api_router.get(
    "/projects/{project_id}/runs",
    response_model=list[RunResponse],
)
def list_runs_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
) -> list[RunResponse]:
    return [
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
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="no approved export available for this run",
            )
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
    response_model=list[ExportRecordResponse],
)
def list_exports_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[ExportRecordResponse]:
    exports = store.get_exports_for_run(run_id)
    # Clients only see approved/delivered exports; Altera sees all.
    if not auth.is_altera_internal:
        exports = [e for e in exports if e.approval_status in _CLIENT_VISIBLE_STATUSES]
    return [_to_export_response(e) for e in exports if e.run_id == run_id]


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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only Altera internal users can submit exports for review",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only altera_methodology_lead can approve exports",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only altera_methodology_lead can reject exports",
        )
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only altera_methodology_lead or altera_admin can deliver exports",
        )
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

    Altera users: always accessible regardless of approval status.
    Client users: 403 if no approved or delivered export exists.
    """
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    exports = store.get_exports_for_run(run_id)

    if auth.is_altera_internal:
        export = max(exports, key=lambda e: e.created_at) if exports else None
    else:
        visible = [e for e in exports if e.approval_status in _CLIENT_VISIBLE_STATUSES]
        if not visible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="report is not yet approved for client access",
            )
        export = max(visible, key=lambda e: e.created_at)

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
    response_model=list[RecommendationResponse],
)
def list_recommendations_route(
    run_id: UUID,
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[RecommendationResponse]:
    """List persisted recommendations for a run.

    Altera users see all statuses; clients see only proposed and accepted.
    """
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    recs = store.list_recommendations_for_run(run_id)

    if not auth.is_altera_internal:
        recs = [r for r in recs if r.status in _CLIENT_VISIBLE_REC_STATUSES]

    return [_rec_response(r) for r in recs]


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
    if not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="altera internal access required")

    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise HTTPException(status_code=404, detail="run not found")

    from altera_api.exports.coverage import build_coverage_section
    from altera_api.recommendations.engine import generate_recommendations as _gen

    coverage = build_coverage_section(store, record, project)

    from altera_api.domain.common import Methodology as _M

    if record.methodology is _M.PROTEIN_TRACKER:
        from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary

        s_pt = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
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
        from altera_api.domain.wwf import WWFCalculationSummary

        s_wwf = WWFCalculationSummary.model_validate(record.summary_payload)
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
        raise HTTPException(status_code=403, detail="access denied")

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
        raise HTTPException(status_code=403, detail="insufficient permissions to propose recommendations")
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
        raise HTTPException(status_code=403, detail="altera internal access required")
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
        raise HTTPException(status_code=403, detail="altera internal access required")
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
        raise HTTPException(status_code=403, detail="insufficient permissions to accept recommendations")
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
    if not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="altera internal access required")

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
    response_model=list[ScenarioResponse],
)
def list_scenarios_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    auth: Annotated[AuthContext, Depends(authed_user)],
) -> list[ScenarioResponse]:
    """List scenarios for a project.

    Altera sees all statuses. Clients see only active scenarios.
    """
    scenarios = store.list_scenarios_for_project(project.id)
    if not auth.is_altera_internal:
        scenarios = [s for s in scenarios if s.status == ScenarioStatus.ACTIVE.value]
    return [_scenario_response(store, s) for s in scenarios]


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
    if not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="altera internal access required")

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
    if not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="altera internal access required")

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    if scenario.organisation_id != auth.organisation_id and not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="access denied")

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
    if not auth.is_altera_internal:
        raise HTTPException(status_code=403, detail="altera internal access required")

    scenario = store.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="scenario not found")

    run = store.get_run(scenario.base_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="base run not found")

    from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary
    from altera_api.scenarios.pt_projection import project_pt_scenario

    try:
        base_summary = ProteinTrackerCalculationSummary.model_validate(run.summary_payload)
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
        raise HTTPException(status_code=403, detail="scenario not yet active")

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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="use_enriched_nutrition may only be enabled by Altera internal users",
        )
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


@api_router.get("/projects/{project_id}/jobs", response_model=list[JobResponse])
def list_jobs_route(
    project: Annotated[Project, Depends(get_project)],
    store: Annotated[StoreProtocol, Depends(get_data_store)],
    job_type: JobType | None = None,
) -> list[JobResponse]:
    jobs = store.list_jobs_for_project(project.id)
    if job_type is not None:
        jobs = [j for j in jobs if j.job_type is job_type]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [_job_response(j) for j in jobs]


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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
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

    if not auth.is_altera_internal:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

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

    if not auth.is_altera_internal:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

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
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"No approved export available for the {label} run. "
                        "Run comparisons are only available once both reports "
                        "have been approved or delivered."
                    ),
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
