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

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
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
from altera_api.api.state import ExportRecord
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
    user_id: Annotated[UUID, Depends(current_user_id)],
) -> RunResponse:
    try:
        record = run_calculation(
            store, project=project, methodology=body.methodology, triggered_by=user_id
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

    return build_report_document(store, record, project, export)


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
    job, _created = _create_and_dispatch(
        job_type=JobType.RUN_CALCULATION,
        project=project,
        store=store,
        worker=worker,
        auth=auth,
        payload={"methodology": body.methodology.value},
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
