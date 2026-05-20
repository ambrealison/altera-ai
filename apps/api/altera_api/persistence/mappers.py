"""Row-to-domain and domain-to-row conversions for the Postgres repository.

Each mapper pair converts between the raw dicts returned by supabase-py
and the typed domain / state models.  Callers are responsible for
providing any context that cannot be derived from the row alone
(e.g. ``methodologies_enabled`` for products).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from altera_api.api.state import (
    ExportRecord,
    PersistedRecommendation,
    RunRecord,
    ScenarioOperationRecord,
    ScenarioRecord,
    ScenarioResultRecord,
    UploadRecord,
)
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    ClientRole,
    Methodology,
    OrganisationType,
    Role,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.project import Project, ProjectStatus, PTValidationStatus
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.report_exports import ReviewOwnerType
from altera_api.domain.review import (
    ManualReviewDecision,
    ManualReviewDecisionType,
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.validation import ValidationReport
from altera_api.domain.wwf import (
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)


def _parse_role(value: str) -> Role | ClientRole | AlteraRole:
    for cls in (Role, ClientRole, AlteraRole):
        try:
            return cls(value)
        except ValueError:
            pass
    raise ValueError(f"unknown role: {value!r}")


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Organisation
# ---------------------------------------------------------------------------


def organisation_from_row(row: dict) -> Organisation:
    return Organisation(
        id=UUID(row["id"]),
        name=row["name"],
        slug=row["slug"],
        organisation_type=OrganisationType(row.get("organisation_type", "gms_client")),
        created_at=_parse_dt(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------


def user_profile_from_rows(profile_row: dict, membership_row: dict) -> UserProfile:
    return UserProfile(
        user_id=UUID(profile_row["user_id"]),
        organisation_id=UUID(membership_row["organisation_id"]),
        email=profile_row["email"],
        display_name=profile_row["display_name"],
        role=_parse_role(membership_row["role"]),
        created_at=_parse_dt(profile_row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def project_from_row(row: dict) -> Project:
    return Project(
        id=UUID(row["id"]),
        organisation_id=UUID(row["organisation_id"]),
        name=row["name"],
        methodologies_enabled=frozenset(Methodology(m) for m in row["methodologies_enabled"]),
        reporting_period_label=row["reporting_period_label"],
        pt_validation_status=PTValidationStatus(row.get("pt_validation_status", "none")),
        project_status=ProjectStatus(row.get("project_status", "created")),
        created_by=UUID(row["created_by"]) if row.get("created_by") else UUID(int=0),
        created_at=_parse_dt(row["created_at"]),
    )


def project_to_row(project: Project) -> dict:
    return {
        "id": str(project.id),
        "organisation_id": str(project.organisation_id),
        "name": project.name,
        "methodologies_enabled": sorted(m.value for m in project.methodologies_enabled),
        "reporting_period_label": project.reporting_period_label,
        "pt_validation_status": project.pt_validation_status.value,
        "project_status": project.project_status.value,
        "created_by": str(project.created_by),
        "created_at": project.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload_from_row(row: dict) -> Upload:
    return Upload(
        id=UUID(row["id"]),
        organisation_id=UUID(row["organisation_id"]),
        project_id=UUID(row["project_id"]),
        storage_path=row["storage_path"],
        original_filename=row["original_filename"],
        status=UploadStatus(row["status"]),
        row_count=row.get("row_count"),
        dropped_columns=tuple(row.get("dropped_columns") or []),
        uploaded_by=UUID(row["uploaded_by"]) if row.get("uploaded_by") else UUID(int=0),
        created_at=_parse_dt(row["created_at"]),
        # Phase 15 fields
        content_type=row.get("content_type"),
        file_size_bytes=row.get("file_size_bytes"),
        checksum_sha256=row.get("checksum_sha256"),
        validation_started_at=(
            _parse_dt(row["validation_started_at"])
            if row.get("validation_started_at")
            else None
        ),
        validation_completed_at=(
            _parse_dt(row["validation_completed_at"])
            if row.get("validation_completed_at")
            else None
        ),
        ingestion_started_at=(
            _parse_dt(row["ingestion_started_at"])
            if row.get("ingestion_started_at")
            else None
        ),
        ingestion_completed_at=(
            _parse_dt(row["ingestion_completed_at"])
            if row.get("ingestion_completed_at")
            else None
        ),
    )


def upload_to_row(upload: Upload) -> dict:
    return {
        "id": str(upload.id),
        "organisation_id": str(upload.organisation_id),
        "project_id": str(upload.project_id),
        "storage_path": upload.storage_path,
        "original_filename": upload.original_filename,
        "status": upload.status.value,
        "row_count": upload.row_count,
        "dropped_columns": list(upload.dropped_columns),
        "uploaded_by": str(upload.uploaded_by),
        "created_at": upload.created_at.isoformat(),
        # Phase 15 fields
        "content_type": upload.content_type,
        "file_size_bytes": upload.file_size_bytes,
        "checksum_sha256": upload.checksum_sha256,
        "validation_started_at": (
            upload.validation_started_at.isoformat()
            if upload.validation_started_at
            else None
        ),
        "validation_completed_at": (
            upload.validation_completed_at.isoformat()
            if upload.validation_completed_at
            else None
        ),
        "ingestion_started_at": (
            upload.ingestion_started_at.isoformat()
            if upload.ingestion_started_at
            else None
        ),
        "ingestion_completed_at": (
            upload.ingestion_completed_at.isoformat()
            if upload.ingestion_completed_at
            else None
        ),
    }


def upload_record_from_rows(upload_row: dict, product_id_rows: list[dict]) -> UploadRecord:
    validation_report: ValidationReport | None = None
    vr_payload = upload_row.get("validation_report")
    if vr_payload:
        try:
            validation_report = ValidationReport.model_validate(vr_payload)
        except Exception:
            pass
    return UploadRecord(
        upload=upload_from_row(upload_row),
        product_ids=[UUID(r["id"]) for r in product_id_rows],
        validation_report=validation_report,
        duplicate_of=(
            UUID(upload_row["duplicate_of_upload_id"])
            if upload_row.get("duplicate_of_upload_id")
            else None
        ),
    )


# ---------------------------------------------------------------------------
# NormalizedProduct
# ---------------------------------------------------------------------------


def product_from_row(
    row: dict, *, methodologies_enabled: frozenset[Methodology]
) -> NormalizedProduct:
    pt_fields: PTProductFields | None = None
    if (
        Methodology.PROTEIN_TRACKER in methodologies_enabled
        and row.get("items_purchased") is not None
    ):
        pt_fields = PTProductFields(
            items_purchased=Decimal(str(row["items_purchased"])),
            protein_pct=(
                Decimal(str(row["protein_pct"])) if row.get("protein_pct") is not None else None
            ),
            protein_source=ProteinSource(row.get("protein_source") or "reference_db"),
            plant_protein_pct=(
                Decimal(str(row["plant_protein_pct"]))
                if row.get("plant_protein_pct") is not None
                else None
            ),
            animal_protein_pct=(
                Decimal(str(row["animal_protein_pct"]))
                if row.get("animal_protein_pct") is not None
                else None
            ),
        )

    wwf_fields: WWFProductFields | None = None
    if Methodology.WWF in methodologies_enabled and row.get("items_sold") is not None:
        wwf_fields = WWFProductFields(
            items_sold=Decimal(str(row["items_sold"])),
            retail_channel=RetailChannel(row["retail_channel"]),
            is_own_brand=bool(row.get("is_own_brand")),
        )

    return NormalizedProduct(
        id=UUID(row["id"]),
        upload_id=UUID(row["upload_id"]),
        project_id=UUID(row["project_id"]),
        organisation_id=UUID(row["organisation_id"]),
        row_number=row["row_number"],
        external_product_id=row["external_product_id"],
        product_name=row["product_name"],
        brand=row.get("brand"),
        is_own_brand=row.get("is_own_brand"),
        retailer_category=row.get("retailer_category"),
        retailer_subcategory=row.get("retailer_subcategory"),
        ingredients_text=row.get("ingredients_text"),
        labels=tuple(row.get("labels") or []),
        language=row.get("language"),
        country=row.get("country"),
        weight_per_item_kg=Decimal(str(row["weight_per_item_kg"])),
        methodologies_enabled=methodologies_enabled,
        pt_fields=pt_fields,
        wwf_fields=wwf_fields,
        created_at=_parse_dt(row["created_at"]),
    )


def product_to_row(product: NormalizedProduct) -> dict:
    row: dict = {
        "id": str(product.id),
        "upload_id": str(product.upload_id),
        "project_id": str(product.project_id),
        "organisation_id": str(product.organisation_id),
        "row_number": product.row_number,
        "external_product_id": product.external_product_id,
        "product_name": product.product_name,
        "brand": product.brand,
        "is_own_brand": product.is_own_brand,
        "retailer_category": product.retailer_category,
        "retailer_subcategory": product.retailer_subcategory,
        "ingredients_text": product.ingredients_text,
        "labels": list(product.labels),
        "language": product.language,
        "country": product.country,
        "weight_per_item_kg": float(product.weight_per_item_kg),
        "created_at": product.created_at.isoformat(),
    }
    if product.pt_fields:
        f = product.pt_fields
        row.update(
            {
                "items_purchased": float(f.items_purchased),
                "protein_pct": float(f.protein_pct) if f.protein_pct is not None else None,
                "protein_source": f.protein_source.value,
                "plant_protein_pct": (
                    float(f.plant_protein_pct) if f.plant_protein_pct is not None else None
                ),
                "animal_protein_pct": (
                    float(f.animal_protein_pct) if f.animal_protein_pct is not None else None
                ),
            }
        )
    if product.wwf_fields:
        f = product.wwf_fields
        row.update(
            {
                "items_sold": float(f.items_sold),
                "retail_channel": f.retail_channel.value,
            }
        )
    return row


# ---------------------------------------------------------------------------
# Classifications — Protein Tracker
# ---------------------------------------------------------------------------


def pt_classification_from_row(row: dict) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=UUID(row["product_id"]),
        pt_group=ProteinTrackerGroup(row["category"]),
        source=ClassificationSource(row["source"]),
        confidence=Decimal(str(row["confidence"])),
        rule_id=row.get("rule_id"),
        ai_prompt_version=row.get("ai_prompt_version"),
        ai_model=row.get("ai_model"),
        reviewer_user_id=UUID(row["reviewer_user_id"]) if row.get("reviewer_user_id") else None,
        review_reason=row.get("review_reason"),
        updated_at=_parse_dt(row["updated_at"]),
    )


def pt_classification_to_row(
    c: ProteinTrackerProductClassification, *, organisation_id: UUID
) -> dict:
    return {
        "product_id": str(c.product_id),
        "methodology": "protein_tracker",
        "organisation_id": str(organisation_id),
        "category": c.pt_group.value,
        "source": c.source.value,
        "confidence": float(c.confidence),
        "rule_id": c.rule_id,
        "ai_prompt_version": c.ai_prompt_version,
        "ai_model": c.ai_model,
        "reviewer_user_id": str(c.reviewer_user_id) if c.reviewer_user_id else None,
        "review_reason": c.review_reason,
        "updated_at": c.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Classifications — WWF
# ---------------------------------------------------------------------------


def wwf_classification_from_row(row: dict) -> WWFProductClassification:
    fg = WWFFoodGroup(row["category"])
    subgroup_str: str | None = row.get("wwf_subgroup")
    composite_str: str | None = row.get("wwf_composite_step1_bucket")

    subgroup_kwargs: dict = {}
    if subgroup_str:
        match fg:
            case WWFFoodGroup.FG1:
                subgroup_kwargs["fg1_subgroup"] = WWFFG1Subgroup(subgroup_str)
            case WWFFoodGroup.FG2:
                subgroup_kwargs["fg2_subgroup"] = WWFFG2Subgroup(subgroup_str)
            case WWFFoodGroup.FG3:
                subgroup_kwargs["fg3_subgroup"] = WWFFG3Subgroup(subgroup_str)
            case WWFFoodGroup.FG5:
                subgroup_kwargs["fg5_grain_kind"] = WWFFG5GrainKind(subgroup_str)
            case WWFFoodGroup.FG7:
                subgroup_kwargs["fg7_snack_kind"] = WWFFG7SnackKind(subgroup_str)

    return WWFProductClassification(
        product_id=UUID(row["product_id"]),
        wwf_food_group=fg,
        wwf_is_composite=row.get("wwf_is_composite") or False,
        composite_step1_bucket=(WWFCompositeStep1Bucket(composite_str) if composite_str else None),
        source=ClassificationSource(row["source"]),
        confidence=Decimal(str(row["confidence"])),
        rule_id=row.get("rule_id"),
        ai_prompt_version=row.get("ai_prompt_version"),
        ai_model=row.get("ai_model"),
        reviewer_user_id=UUID(row["reviewer_user_id"]) if row.get("reviewer_user_id") else None,
        review_reason=row.get("review_reason"),
        updated_at=_parse_dt(row["updated_at"]),
        **subgroup_kwargs,
    )


def wwf_classification_to_row(c: WWFProductClassification, *, organisation_id: UUID) -> dict:
    subgroup: str | None = None
    for sg in (c.fg1_subgroup, c.fg2_subgroup, c.fg3_subgroup, c.fg5_grain_kind, c.fg7_snack_kind):
        if sg is not None:
            subgroup = sg.value
            break

    return {
        "product_id": str(c.product_id),
        "methodology": "wwf",
        "organisation_id": str(organisation_id),
        "category": c.wwf_food_group.value,
        "wwf_is_composite": c.wwf_is_composite,
        "wwf_subgroup": subgroup,
        "wwf_composite_step1_bucket": (
            c.composite_step1_bucket.value if c.composite_step1_bucket else None
        ),
        "source": c.source.value,
        "confidence": float(c.confidence),
        "rule_id": c.rule_id,
        "ai_prompt_version": c.ai_prompt_version,
        "ai_model": c.ai_model,
        "reviewer_user_id": str(c.reviewer_user_id) if c.reviewer_user_id else None,
        "review_reason": c.review_reason,
        "updated_at": c.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Manual review
# ---------------------------------------------------------------------------


def manual_review_from_row(row: dict) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=UUID(row["product_id"]),
        methodology=Methodology(row["methodology"]),
        status=ManualReviewStatus(row["status"]),
        reason=ManualReviewQueueReason(row["reason"]),
        owner_type=ReviewOwnerType(row.get("owner_type", "altera_internal")),
        soft_lock_user_id=(
            UUID(row["soft_lock_user_id"]) if row.get("soft_lock_user_id") else None
        ),
        soft_lock_expires_at=(
            _parse_dt(row["soft_lock_expires_at"]) if row.get("soft_lock_expires_at") else None
        ),
        queued_at=_parse_dt(row["queued_at"]),
    )


def manual_review_to_row(item: ManualReviewItem, *, organisation_id: UUID) -> dict:
    return {
        "product_id": str(item.product_id),
        "methodology": item.methodology.value,
        "organisation_id": str(organisation_id),
        "status": item.status.value,
        "reason": item.reason.value,
        "owner_type": item.owner_type.value,
        "soft_lock_user_id": (str(item.soft_lock_user_id) if item.soft_lock_user_id else None),
        "soft_lock_expires_at": (
            item.soft_lock_expires_at.isoformat() if item.soft_lock_expires_at else None
        ),
        "queued_at": item.queued_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Calculation run
# ---------------------------------------------------------------------------


def run_record_from_row(row: dict) -> RunRecord:
    rows_payload: list[dict] = row.get("rows_payload") or []
    return RunRecord(
        id=UUID(row["id"]),
        project_id=UUID(row["project_id"]),
        organisation_id=UUID(row["organisation_id"]),
        methodology=Methodology(row["methodology"]),
        started_at=_parse_dt(row["started_at"]) if row.get("started_at") else datetime.now(UTC),
        finished_at=(
            _parse_dt(row["finished_at"]) if row.get("finished_at") else datetime.now(UTC)
        ),
        triggered_by=(UUID(row["triggered_by"]) if row.get("triggered_by") else UUID(int=0)),
        rows_payload=rows_payload,
        summary_payload=row.get("summary_payload") or {},
        rows_count=len(rows_payload),
    )


def _json_safe(value):
    """Recursively convert a value into JSON-serialisable primitives.

    pydantic's default ``model_dump()`` keeps ``Decimal``/``UUID``/
    ``datetime`` as Python objects. supabase-py then calls ``json.dumps``
    on the insert payload and raises ``TypeError`` — the root cause of
    the Phase 33F run-persistence 500. Encoding once at the persistence
    boundary keeps in-memory semantics untouched while guaranteeing the
    JSONB columns receive primitives.
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def run_record_to_row(record: RunRecord) -> dict:
    summary = record.summary_payload
    return {
        "id": str(record.id),
        "project_id": str(record.project_id),
        "organisation_id": str(record.organisation_id) if record.organisation_id else None,
        "methodology": record.methodology.value,
        "methodology_version": summary.get("methodology_version", "unknown"),
        "methodology_source_edition": summary.get("methodology_source_edition", "unknown"),
        "taxonomy_version": summary.get("taxonomy_version", "unknown"),
        "rules_version": summary.get("rules_version", "unknown"),
        "reporting_period_label": summary.get("reporting_period_label", ""),
        "status": "success",
        "started_at": record.started_at.isoformat(),
        "finished_at": record.finished_at.isoformat(),
        "triggered_by": str(record.triggered_by) if record.triggered_by != UUID(int=0) else None,
        "summary_payload": _json_safe(record.summary_payload),
        "rows_payload": _json_safe(record.rows_payload),
    }


# ---------------------------------------------------------------------------
# Export records
# ---------------------------------------------------------------------------


def export_record_to_row(record: ExportRecord) -> dict:
    return {
        "id": str(record.id),
        "run_id": str(record.run_id),
        "organisation_id": str(record.organisation_id),
        "format": record.format,
        "status": record.status,
        "storage_path": record.storage_path,
        "filename": record.filename,
        "size_bytes": record.size_bytes,
        "approval_status": record.approval_status,
        "requested_by": str(record.requested_by) if record.requested_by else None,
        "approved_by": str(record.approved_by) if record.approved_by else None,
        "approved_at": record.approved_at.isoformat() if record.approved_at else None,
        "rejected_by": str(record.rejected_by) if record.rejected_by else None,
        "rejected_at": record.rejected_at.isoformat() if record.rejected_at else None,
        "rejection_reason": record.rejection_reason,
        "under_review_by": str(record.under_review_by) if record.under_review_by else None,
        "under_review_at": record.under_review_at.isoformat() if record.under_review_at else None,
        "delivered_by": str(record.delivered_by) if record.delivered_by else None,
        "delivered_at": record.delivered_at.isoformat() if record.delivered_at else None,
        "client_downloaded_at": record.client_downloaded_at.isoformat()
        if record.client_downloaded_at
        else None,
        "client_download_count": record.client_download_count,
        "created_at": record.created_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


def export_record_from_row(row: dict) -> ExportRecord:
    return ExportRecord(
        id=UUID(row["id"]),
        run_id=UUID(row["run_id"]),
        organisation_id=UUID(row["organisation_id"]),
        format=row["format"],
        status=row["status"],
        storage_path=row.get("storage_path") or "",
        filename=row.get("filename") or "",
        size_bytes=row.get("size_bytes") or 0,
        approval_status=row.get("approval_status") or "draft",
        requested_by=UUID(row["requested_by"]) if row.get("requested_by") else None,
        approved_by=UUID(row["approved_by"]) if row.get("approved_by") else None,
        approved_at=_parse_dt(row["approved_at"]) if row.get("approved_at") else None,
        rejected_by=UUID(row["rejected_by"]) if row.get("rejected_by") else None,
        rejected_at=_parse_dt(row["rejected_at"]) if row.get("rejected_at") else None,
        rejection_reason=row.get("rejection_reason"),
        under_review_by=UUID(row["under_review_by"]) if row.get("under_review_by") else None,
        under_review_at=_parse_dt(row["under_review_at"]) if row.get("under_review_at") else None,
        delivered_by=UUID(row["delivered_by"]) if row.get("delivered_by") else None,
        delivered_at=_parse_dt(row["delivered_at"]) if row.get("delivered_at") else None,
        client_downloaded_at=_parse_dt(row["client_downloaded_at"])
        if row.get("client_downloaded_at")
        else None,
        client_download_count=row.get("client_download_count") or 0,
        created_at=_parse_dt(row["created_at"]),
        finished_at=_parse_dt(row["finished_at"]) if row.get("finished_at") else None,
    )


# ---------------------------------------------------------------------------
# WWF composite ingredients
# ---------------------------------------------------------------------------


def wwf_ingredient_from_row(row: dict) -> WWFCompositeIngredient:
    fg = WWFFoodGroup(row["food_group"])
    subgroup_str: str | None = row.get("subgroup")

    subgroup_kwargs: dict = {}
    if subgroup_str:
        if fg == WWFFoodGroup.FG1:
            subgroup_kwargs["fg1_subgroup"] = WWFFG1Subgroup(subgroup_str)
        elif fg == WWFFoodGroup.FG2:
            subgroup_kwargs["fg2_subgroup"] = WWFFG2Subgroup(subgroup_str)

    return WWFCompositeIngredient(
        id=UUID(row["id"]),
        parent_product_id=UUID(row["product_id"]),
        food_group=fg,
        ingredient_weight_kg_per_item=Decimal(str(row["ingredient_weight_kg_per_item"])),
        **subgroup_kwargs,
    )


# ---------------------------------------------------------------------------
# Jobs (Phase 16)
# ---------------------------------------------------------------------------


def job_from_row(row: dict) -> Job:
    return Job(
        job_id=UUID(row["job_id"]),
        organisation_id=UUID(row["organisation_id"]),
        project_id=UUID(row["project_id"]),
        upload_id=UUID(row["upload_id"]) if row.get("upload_id") else None,
        run_id=UUID(row["run_id"]) if row.get("run_id") else None,
        job_type=JobType(row["job_type"]),
        status=JobStatus(row["status"]),
        progress_pct=row.get("progress_pct"),
        created_by=UUID(row["created_by"]),
        created_at=_parse_dt(row["created_at"]),
        started_at=_parse_dt(row["started_at"]) if row.get("started_at") else None,
        completed_at=_parse_dt(row["completed_at"]) if row.get("completed_at") else None,
        failed_at=_parse_dt(row["failed_at"]) if row.get("failed_at") else None,
        error_message=row.get("error_message"),
        retry_count=row.get("retry_count") or 0,
        idempotency_key=row.get("idempotency_key"),
        payload=row.get("payload") or {},
    )


def job_to_row(job: Job) -> dict:
    return {
        "job_id": str(job.job_id),
        "organisation_id": str(job.organisation_id),
        "project_id": str(job.project_id),
        "upload_id": str(job.upload_id) if job.upload_id else None,
        "run_id": str(job.run_id) if job.run_id else None,
        "job_type": job.job_type.value,
        "status": job.status.value,
        "progress_pct": job.progress_pct,
        "created_by": str(job.created_by),
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "failed_at": job.failed_at.isoformat() if job.failed_at else None,
        "error_message": job.error_message,
        "retry_count": job.retry_count,
        "idempotency_key": job.idempotency_key,
        "payload": job.payload,
    }


# ---------------------------------------------------------------------------
# Review decisions (Phase 19C)
# ---------------------------------------------------------------------------


def review_decision_to_row(decision: ManualReviewDecision) -> dict:
    return {
        "id": str(decision.id),
        "product_id": str(decision.product_id),
        "methodology": decision.methodology.value,
        "decision": decision.decision.value,
        "reviewer_user_id": str(decision.reviewer_user_id),
        "from_category": decision.from_category,
        "to_category": decision.to_category,
        "reason": decision.reason,
        "created_at": decision.created_at.isoformat(),
    }


def review_decision_from_row(row: dict) -> ManualReviewDecision:
    return ManualReviewDecision(
        id=UUID(row["id"]),
        product_id=UUID(row["product_id"]),
        methodology=Methodology(row["methodology"]),
        decision=ManualReviewDecisionType(row["decision"]),
        reviewer_user_id=UUID(row["reviewer_user_id"]),
        from_category=row.get("from_category"),
        to_category=row.get("to_category"),
        reason=row.get("reason"),
        created_at=_parse_dt(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Nutrition enrichment records (Phase 23A)
# ---------------------------------------------------------------------------


def enrichment_record_from_row(row: dict) -> NutritionEnrichmentRecord:
    return NutritionEnrichmentRecord(
        product_id=UUID(row["product_id"]),
        nutrient=row["nutrient"],
        original_value=(
            Decimal(str(row["original_value"]))
            if row.get("original_value") is not None
            else None
        ),
        enriched_value=(
            Decimal(str(row["enriched_value"]))
            if row.get("enriched_value") is not None
            else None
        ),
        unit=row["unit"],
        source=NutritionEnrichmentSource(row["source"]),
        confidence=(
            Decimal(str(row["confidence"]))
            if row.get("confidence") is not None
            else None
        ),
        status=NutritionEnrichmentStatus(row["status"]),
        rationale=row.get("rationale") or "",
        created_at=_parse_dt(row["created_at"]),
        created_by=UUID(row["created_by"]) if row.get("created_by") else None,
    )


def enrichment_record_to_row(record: NutritionEnrichmentRecord) -> dict:
    return {
        "product_id": str(record.product_id),
        "nutrient": record.nutrient,
        "original_value": (
            float(record.original_value) if record.original_value is not None else None
        ),
        "enriched_value": (
            float(record.enriched_value) if record.enriched_value is not None else None
        ),
        "unit": record.unit,
        "source": record.source.value,
        "confidence": float(record.confidence) if record.confidence is not None else None,
        "status": record.status.value,
        "rationale": record.rationale,
        "created_at": record.created_at.isoformat(),
        "created_by": str(record.created_by) if record.created_by else None,
    }


# ---------------------------------------------------------------------------
# Recommendations (Phase 25B)
# ---------------------------------------------------------------------------


def persisted_recommendation_from_row(row: dict) -> PersistedRecommendation:
    return PersistedRecommendation(
        id=UUID(row["id"]),
        organisation_id=UUID(row["organisation_id"]),
        project_id=UUID(row["project_id"]),
        run_id=UUID(row["run_id"]),
        methodology=row["methodology"],
        action_type=row["action_type"],
        category=row["category"],
        title=row["title"],
        description=row["description"],
        rationale=row["rationale"],
        expected_direction=row["expected_direction"],
        priority=row["priority"],
        confidence=row["confidence"],
        evidence=list(row.get("evidence") or []),
        caveats=list(row.get("caveats") or []),
        status=row["status"],
        client_facing=bool(row["client_facing"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        created_by=UUID(row["created_by"]) if row.get("created_by") else None,
        updated_by=UUID(row["updated_by"]) if row.get("updated_by") else None,
    )


def persisted_recommendation_to_row(rec: PersistedRecommendation) -> dict:
    return {
        "id": str(rec.id),
        "organisation_id": str(rec.organisation_id),
        "project_id": str(rec.project_id),
        "run_id": str(rec.run_id),
        "methodology": rec.methodology,
        "action_type": rec.action_type,
        "category": rec.category,
        "title": rec.title,
        "description": rec.description,
        "rationale": rec.rationale,
        "expected_direction": rec.expected_direction,
        "priority": rec.priority,
        "confidence": rec.confidence,
        "evidence": list(rec.evidence),
        "caveats": list(rec.caveats),
        "status": rec.status,
        "client_facing": rec.client_facing,
        "created_at": rec.created_at.isoformat(),
        "updated_at": rec.updated_at.isoformat(),
        "created_by": str(rec.created_by) if rec.created_by else None,
        "updated_by": str(rec.updated_by) if rec.updated_by else None,
    }


# ---------------------------------------------------------------------------
# Scenarios (Phase 26A)
# ---------------------------------------------------------------------------


def scenario_from_row(row: dict) -> ScenarioRecord:
    return ScenarioRecord(
        id=UUID(row["id"]),
        organisation_id=UUID(row["organisation_id"]),
        project_id=UUID(row["project_id"]),
        base_run_id=UUID(row["base_run_id"]),
        name=row["name"],
        description=row.get("description") or "",
        status=row["status"],
        methodology=row["methodology"],
        created_by=UUID(row["created_by"]) if row.get("created_by") else UUID(int=0),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def scenario_to_row(record: ScenarioRecord) -> dict:
    return {
        "id": str(record.id),
        "organisation_id": str(record.organisation_id),
        "project_id": str(record.project_id),
        "base_run_id": str(record.base_run_id),
        "name": record.name,
        "description": record.description,
        "status": record.status,
        "methodology": record.methodology,
        "created_by": (
            str(record.created_by) if record.created_by != UUID(int=0) else None
        ),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def scenario_operation_from_row(row: dict) -> ScenarioOperationRecord:
    return ScenarioOperationRecord(
        id=UUID(row["id"]),
        scenario_id=UUID(row["scenario_id"]),
        operation_type=row["operation_type"],
        parameters=row.get("parameters") or {},
        rationale=row.get("rationale") or "",
        order=row.get("order") or 0,
        created_at=_parse_dt(row["created_at"]),
    )


def scenario_operation_to_row(record: ScenarioOperationRecord) -> dict:
    return {
        "id": str(record.id),
        "scenario_id": str(record.scenario_id),
        "operation_type": record.operation_type,
        "parameters": record.parameters,
        "rationale": record.rationale,
        "order": record.order,
        "created_at": record.created_at.isoformat(),
    }


def scenario_result_from_row(row: dict) -> ScenarioResultRecord:
    return ScenarioResultRecord(
        scenario_id=UUID(row["scenario_id"]),
        base_run_id=UUID(row["base_run_id"]),
        methodology=row["methodology"],
        result_payload=row.get("result_payload") or {},
        created_at=_parse_dt(row["created_at"]),
    )


def scenario_result_to_row(record: ScenarioResultRecord) -> dict:
    return {
        "scenario_id": str(record.scenario_id),
        "base_run_id": str(record.base_run_id),
        "methodology": record.methodology,
        "result_payload": record.result_payload,
        "created_at": record.created_at.isoformat(),
    }
