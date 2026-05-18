"""In-memory state container.

A single ``InMemoryStore`` instance holds all projects, uploads,
products, classifications, review queue items, runs, and audit events
for the lifetime of the FastAPI process. Tests use a fresh store per
test via dependency override.

The store is concurrency-naive — Phase 12 is single-user, single-tenant.
Supabase + RLS lands in Phase 13 and replaces this whole module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from altera_api.domain.audit import AuditEvent
from altera_api.domain.common import Methodology, Role
from altera_api.domain.enrichment import NutritionEnrichmentRecord
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.project import Project, PTValidationStatus
from altera_api.domain.protein_tracker import ProteinTrackerProductClassification
from altera_api.domain.review import ManualReviewDecision, ManualReviewItem
from altera_api.domain.upload import Upload
from altera_api.domain.validation import ValidationReport
from altera_api.domain.wwf import (
    WWFCompositeIngredient,
    WWFProductClassification,
)


@dataclass
class RunRecord:
    """Persisted summary + rows for one calculation run."""

    id: UUID
    project_id: UUID
    methodology: Methodology
    started_at: datetime
    finished_at: datetime
    triggered_by: UUID
    # Stored as model_dump payloads so we don't keep large Pydantic objects
    # alive longer than necessary — exports re-serialise from these.
    rows_payload: list[dict] = field(default_factory=list)
    summary_payload: dict = field(default_factory=dict)
    rows_count: int = 0
    organisation_id: UUID | None = None


@dataclass
class ExportRecord:
    """Persisted record of a generated export artefact."""

    id: UUID
    run_id: UUID
    organisation_id: UUID
    format: str  # "csv", "json", "md"
    status: str  # "success" | "failed"
    storage_path: str
    filename: str
    size_bytes: int
    # Phase 20 — full delivery lifecycle
    approval_status: str = "draft"  # draft | under_review | approved | rejected | delivered
    sha256: str | None = None
    requested_by: UUID | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    under_review_by: UUID | None = None
    under_review_at: datetime | None = None
    delivered_by: UUID | None = None
    delivered_at: datetime | None = None
    client_downloaded_at: datetime | None = None
    client_download_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


@dataclass
class UploadRecord:
    """Per-upload bookkeeping that the domain ``Upload`` model doesn't carry."""

    upload: Upload
    product_ids: list[UUID] = field(default_factory=list)
    # Phase 15: persisted validation output and duplicate tracking
    validation_report: ValidationReport | None = None
    duplicate_of: UUID | None = None


class InMemoryStore:
    """A thread-locked dict-of-dicts state container.

    All public methods are mutating; the lock serialises mutations so
    concurrent uvicorn workers don't race on the same upload. Reads are
    not locked (callers operate on immutable Pydantic models).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.organisations: dict[UUID, Organisation] = {}
        self.users: dict[UUID, UserProfile] = {}
        self.projects: dict[UUID, Project] = {}
        self.uploads: dict[UUID, UploadRecord] = {}
        self.products: dict[UUID, NormalizedProduct] = {}
        self.pt_classifications: dict[UUID, ProteinTrackerProductClassification] = {}
        self.wwf_classifications: dict[UUID, WWFProductClassification] = {}
        self.review_queue: dict[UUID, ManualReviewItem] = {}
        self.review_decisions: dict[UUID, ManualReviewDecision] = {}
        self.runs: dict[UUID, RunRecord] = {}
        self.export_records: dict[UUID, ExportRecord] = {}
        self.audit_events: list[AuditEvent] = []
        self.wwf_ingredients: dict[UUID, list[WWFCompositeIngredient]] = {}
        self.jobs: dict[UUID, Job] = {}
        # Phase 23A: enrichment records keyed by product_id
        self.enrichment_records: dict[UUID, list[NutritionEnrichmentRecord]] = {}
        # Bootstrap a default org + user so Phase 12 doesn't need auth.
        self._bootstrap_default_tenant()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------
    def _bootstrap_default_tenant(self) -> None:
        now = datetime.now(UTC)
        org_id = UUID("00000000-0000-0000-0000-0000000000a0")
        user_id = UUID("00000000-0000-0000-0000-0000000000a1")
        self.organisations[org_id] = Organisation(
            id=org_id,
            name="Demo Organisation",
            slug="demo",
            created_at=now,
        )
        self.users[user_id] = UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email="demo@altera-ai.local",
            display_name="Demo User",
            role=Role.OWNER,
            created_at=now,
        )

    @property
    def default_org_id(self) -> UUID:
        return next(iter(self.organisations))

    @property
    def default_user_id(self) -> UUID:
        return next(iter(self.users))

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    def create_project(
        self,
        *,
        name: str,
        methodologies_enabled: frozenset[Methodology],
        reporting_period_label: str,
        organisation_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> Project:
        with self._lock:
            project = Project(
                id=uuid4(),
                organisation_id=organisation_id or self.default_org_id,
                name=name,
                methodologies_enabled=methodologies_enabled,
                reporting_period_label=reporting_period_label,
                pt_validation_status=PTValidationStatus.NONE,
                created_by=created_by or self.default_user_id,
                created_at=datetime.now(UTC),
            )
            self.projects[project.id] = project
            return project

    def list_projects(self) -> list[Project]:
        return list(self.projects.values())

    def get_project(self, project_id: UUID) -> Project | None:
        return self.projects.get(project_id)

    # ------------------------------------------------------------------
    # Uploads + products
    # ------------------------------------------------------------------
    def add_upload(self, upload: Upload, product_ids: list[UUID]) -> None:
        with self._lock:
            self.uploads[upload.id] = UploadRecord(upload=upload, product_ids=product_ids)

    def update_upload(self, upload: Upload, *, product_ids: list[UUID] | None = None) -> None:
        """Replace the Upload in an existing UploadRecord.

        If *product_ids* is provided it replaces the existing list;
        otherwise the existing list is preserved. If no record exists yet
        a new one is created.
        """
        with self._lock:
            rec = self.uploads.get(upload.id)
            if rec is not None:
                new_pids = product_ids if product_ids is not None else rec.product_ids
                self.uploads[upload.id] = UploadRecord(
                    upload=upload,
                    product_ids=new_pids,
                    validation_report=rec.validation_report,
                    duplicate_of=rec.duplicate_of,
                )
            else:
                self.uploads[upload.id] = UploadRecord(upload=upload, product_ids=product_ids or [])

    def set_upload_validation_report(
        self,
        upload_id: UUID,
        report: ValidationReport,
        *,
        duplicate_of: UUID | None = None,
    ) -> None:
        with self._lock:
            rec = self.uploads.get(upload_id)
            if rec is not None:
                self.uploads[upload_id] = UploadRecord(
                    upload=rec.upload,
                    product_ids=rec.product_ids,
                    validation_report=report,
                    duplicate_of=duplicate_of if duplicate_of is not None else rec.duplicate_of,
                )

    def get_upload_validation_report(self, upload_id: UUID) -> ValidationReport | None:
        rec = self.uploads.get(upload_id)
        return rec.validation_report if rec is not None else None

    def find_upload_by_checksum(self, project_id: UUID, checksum: str) -> Upload | None:
        """Return the most-recent upload in *project_id* with the given SHA-256."""
        matches = [
            rec.upload
            for rec in self.uploads.values()
            if rec.upload.project_id == project_id and rec.upload.checksum_sha256 == checksum
        ]
        if not matches:
            return None
        return max(matches, key=lambda u: u.created_at)

    def get_upload(self, upload_id: UUID) -> UploadRecord | None:
        return self.uploads.get(upload_id)

    def list_uploads_for_project(self, project_id: UUID) -> list[UploadRecord]:
        return [u for u in self.uploads.values() if u.upload.project_id == project_id]

    def add_product(self, product: NormalizedProduct) -> None:
        with self._lock:
            self.products[product.id] = product

    def list_products_for_project(self, project_id: UUID) -> list[NormalizedProduct]:
        return [p for p in self.products.values() if p.project_id == project_id]

    # ------------------------------------------------------------------
    # Classifications
    # ------------------------------------------------------------------
    def upsert_pt_classification(self, classification: ProteinTrackerProductClassification) -> None:
        with self._lock:
            self.pt_classifications[classification.product_id] = classification

    def upsert_wwf_classification(self, classification: WWFProductClassification) -> None:
        with self._lock:
            self.wwf_classifications[classification.product_id] = classification

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------
    def upsert_review_item(self, item: ManualReviewItem) -> None:
        with self._lock:
            key = self._review_key(item.product_id, item.methodology)
            self.review_queue[key] = item

    def add_review_decision(self, decision: ManualReviewDecision) -> None:
        with self._lock:
            self.review_decisions[decision.id] = decision

    def remove_review_item(self, product_id: UUID, methodology: Methodology) -> None:
        with self._lock:
            self.review_queue.pop(self._review_key(product_id, methodology), None)

    def get_review_item(
        self, product_id: UUID, methodology: Methodology
    ) -> ManualReviewItem | None:
        return self.review_queue.get(self._review_key(product_id, methodology))

    def list_review_items_for_project(
        self, project_id: UUID, *, methodology: Methodology | None = None
    ) -> list[ManualReviewItem]:
        out: list[ManualReviewItem] = []
        for item in self.review_queue.values():
            product = self.products.get(item.product_id)
            if product is None or product.project_id != project_id:
                continue
            if methodology is not None and item.methodology is not methodology:
                continue
            out.append(item)
        return out

    @staticmethod
    def _review_key(product_id: UUID, methodology: Methodology) -> UUID:
        # We store one entry per (product, methodology); the UUID key
        # combines them deterministically so we don't need a composite map.
        # Use the product_id namespaced by methodology byte.
        suffix = b"\x01" if methodology is Methodology.PROTEIN_TRACKER else b"\x02"
        return UUID(bytes=product_id.bytes[:-1] + suffix)

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def add_run(self, record: RunRecord) -> None:
        with self._lock:
            self.runs[record.id] = record

    def get_run(self, run_id: UUID) -> RunRecord | None:
        return self.runs.get(run_id)

    def list_runs_for_project(self, project_id: UUID) -> list[RunRecord]:
        return [r for r in self.runs.values() if r.project_id == project_id]

    # ------------------------------------------------------------------
    # Export records
    # ------------------------------------------------------------------
    def add_export_record(self, record: ExportRecord) -> None:
        with self._lock:
            self.export_records[record.id] = record

    def get_export_record(self, export_id: UUID) -> ExportRecord | None:
        return self.export_records.get(export_id)

    def get_exports_for_run(self, run_id: UUID) -> list[ExportRecord]:
        return [r for r in self.export_records.values() if r.run_id == run_id]

    def update_export_approval(
        self,
        export_id: UUID,
        *,
        approval_status: str,
        by_user_id: UUID,
        rejection_reason: str | None = None,
    ) -> ExportRecord | None:
        with self._lock:
            record = self.export_records.get(export_id)
            if record is None:
                return None
            now = datetime.now(UTC)
            updated = ExportRecord(
                id=record.id,
                run_id=record.run_id,
                organisation_id=record.organisation_id,
                format=record.format,
                status=record.status,
                storage_path=record.storage_path,
                filename=record.filename,
                size_bytes=record.size_bytes,
                approval_status=approval_status,
                sha256=record.sha256,
                requested_by=record.requested_by,
                approved_by=by_user_id if approval_status == "approved" else record.approved_by,
                approved_at=now if approval_status == "approved" else record.approved_at,
                rejected_by=by_user_id if approval_status == "rejected" else record.rejected_by,
                rejected_at=now if approval_status == "rejected" else record.rejected_at,
                rejection_reason=rejection_reason
                if approval_status == "rejected"
                else record.rejection_reason,
                created_at=record.created_at,
                finished_at=record.finished_at,
            )
            self.export_records[export_id] = updated
            return updated

    def mark_export_under_review(self, export_id: UUID, *, by_user_id: UUID) -> ExportRecord | None:
        import dataclasses

        with self._lock:
            record = self.export_records.get(export_id)
            if record is None:
                return None
            updated = dataclasses.replace(
                record,
                approval_status="under_review",
                under_review_by=by_user_id,
                under_review_at=datetime.now(UTC),
            )
            self.export_records[export_id] = updated
            return updated

    def deliver_export(self, export_id: UUID, *, by_user_id: UUID) -> ExportRecord | None:
        import dataclasses

        with self._lock:
            record = self.export_records.get(export_id)
            if record is None:
                return None
            updated = dataclasses.replace(
                record,
                approval_status="delivered",
                delivered_by=by_user_id,
                delivered_at=datetime.now(UTC),
            )
            self.export_records[export_id] = updated
            return updated

    def record_client_download(self, export_id: UUID) -> ExportRecord | None:
        import dataclasses

        with self._lock:
            record = self.export_records.get(export_id)
            if record is None:
                return None
            updated = dataclasses.replace(
                record,
                client_download_count=record.client_download_count + 1,
                client_downloaded_at=record.client_downloaded_at or datetime.now(UTC),
            )
            self.export_records[export_id] = updated
            return updated

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    # ------------------------------------------------------------------
    # Accessor helpers (used by Protocol-based code paths)
    # ------------------------------------------------------------------
    def get_product(self, product_id: UUID) -> NormalizedProduct | None:
        return self.products.get(product_id)

    def get_pt_classification(self, product_id: UUID) -> ProteinTrackerProductClassification | None:
        return self.pt_classifications.get(product_id)

    def get_wwf_classification(self, product_id: UUID) -> WWFProductClassification | None:
        return self.wwf_classifications.get(product_id)

    def get_wwf_ingredients_by_project(
        self, project_id: UUID
    ) -> dict[UUID, list[WWFCompositeIngredient]]:
        """Return the WWF ingredient map filtered to products of *project_id*."""
        return {
            product_id: ingredients
            for product_id, ingredients in self.wwf_ingredients.items()
            if product_id in self.products and self.products[product_id].project_id == project_id
        }

    def get_user(self, user_id: UUID) -> UserProfile | None:
        return self.users.get(user_id)

    def upsert_user(self, profile: UserProfile) -> None:
        with self._lock:
            self.users[profile.user_id] = profile

    def get_organisation(self, org_id: UUID) -> Organisation | None:
        return self.organisations.get(org_id)

    # ------------------------------------------------------------------
    # Jobs (Phase 16)
    # ------------------------------------------------------------------
    def add_job(self, job: Job) -> None:
        with self._lock:
            self.jobs[job.job_id] = job

    def update_job(self, job: Job) -> None:
        with self._lock:
            self.jobs[job.job_id] = job

    def get_job(self, job_id: UUID) -> Job | None:
        return self.jobs.get(job_id)

    def list_jobs_for_project(self, project_id: UUID) -> list[Job]:
        return [j for j in self.jobs.values() if j.project_id == project_id]

    def find_active_job(self, *, job_type: JobType, idempotency_key: str) -> Job | None:
        """Return a queued/running job with the given type + idempotency key."""
        for job in self.jobs.values():
            if (
                job.job_type is job_type
                and job.idempotency_key == idempotency_key
                and job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            ):
                return job
        return None

    # ------------------------------------------------------------------
    # Nutrition enrichment (Phase 23A)
    # ------------------------------------------------------------------

    def add_enrichment_record(self, record: NutritionEnrichmentRecord) -> None:
        with self._lock:
            recs = self.enrichment_records.get(record.product_id)
            if recs is None:
                self.enrichment_records[record.product_id] = [record]
            else:
                recs.append(record)

    def get_enrichment_records_for_product(
        self, product_id: UUID
    ) -> list[NutritionEnrichmentRecord]:
        return list(self.enrichment_records.get(product_id, []))

    def list_enrichment_records_for_project(
        self, project_id: UUID
    ) -> list[NutritionEnrichmentRecord]:
        result: list[NutritionEnrichmentRecord] = []
        for pid, recs in self.enrichment_records.items():
            product = self.products.get(pid)
            if product is not None and product.project_id == project_id:
                result.extend(recs)
        return result
