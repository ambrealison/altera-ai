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
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.classification_job import ClassificationJob
from altera_api.domain.common import Methodology, OrganisationType, Role
from altera_api.domain.enrichment import NutritionEnrichmentRecord
from altera_api.domain.ingestion_job import IngestionJob
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.nevo import NevoEntry
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
    use_enriched_nutrition: bool = False


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
class PersistedRecommendation:
    """A recommendation that has been saved to the store for lifecycle management."""

    id: UUID
    organisation_id: UUID
    project_id: UUID
    run_id: UUID
    methodology: str
    action_type: str
    category: str
    title: str
    description: str
    rationale: str
    expected_direction: str
    priority: str
    confidence: str
    evidence: list[str]
    caveats: list[str]
    status: str  # draft | proposed | accepted | dismissed | archived
    client_facing: bool
    created_at: datetime
    updated_at: datetime
    created_by: UUID | None = None
    updated_by: UUID | None = None


@dataclass
class ScenarioRecord:
    """Persisted scenario header (metadata only; operations stored separately)."""

    id: UUID
    organisation_id: UUID
    project_id: UUID
    base_run_id: UUID
    name: str
    description: str
    status: str  # draft | active | archived
    methodology: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime


@dataclass
class ScenarioOperationRecord:
    """A single persisted scenario operation."""

    id: UUID
    scenario_id: UUID
    operation_type: str
    parameters: dict  # free-form; validated by projection engine
    rationale: str
    order: int
    created_at: datetime


@dataclass
class ScenarioResultRecord:
    """Persisted projection output for a scenario run."""

    scenario_id: UUID
    base_run_id: UUID
    methodology: str
    result_payload: dict  # model_dump() of ScenarioResult
    created_at: datetime


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
        # Phase 34R — async, chunked AI classification jobs.
        self.classification_jobs: dict[UUID, ClassificationJob] = {}
        # Phase 34X — chunked CSV ingestion jobs.
        self.ingestion_jobs: dict[UUID, IngestionJob] = {}
        # Phase 23A: enrichment records keyed by product_id
        self.enrichment_records: dict[UUID, list[NutritionEnrichmentRecord]] = {}
        # Phase 33H: in-memory nutrition reference tables (seeded in tests;
        # production reads come from PostgresRepository.list_nevo_entries /
        # list_ciqual_entries against the Supabase tables).
        self.nevo_entries: list[NevoEntry] = []
        self.ciqual_entries: list[CiqualEntry] = []
        # Phase 25B: persisted recommendations keyed by recommendation id
        self.recommendations: dict[UUID, PersistedRecommendation] = {}
        # Phase 26A: scenarios, operations, results
        self.scenarios: dict[UUID, ScenarioRecord] = {}
        self.scenario_operations: dict[UUID, ScenarioOperationRecord] = {}
        self.scenario_results: dict[UUID, ScenarioResultRecord] = {}
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

    def delete_upload(self, upload_id: UUID) -> None:
        """Remove an upload and every record that references it.

        Cleans up products, their PT/WWF classifications, manual review
        items, enrichment records, and the upload itself. Calculation
        runs are NOT touched — completed runs remain in history even if
        their source upload is deleted.
        """
        with self._lock:
            rec = self.uploads.pop(upload_id, None)
            if rec is None:
                return
            product_ids = set(rec.product_ids)
            for product_id in product_ids:
                self.products.pop(product_id, None)
                self.pt_classifications.pop(product_id, None)
                self.wwf_classifications.pop(product_id, None)
                self.enrichment_records.pop(product_id, None)
            # Review queue is keyed by (product_id, methodology) hash — purge
            # by scanning since we don't have a reverse index.
            doomed_keys = [
                key for key, item in self.review_queue.items()
                if item.product_id in product_ids
            ]
            for key in doomed_keys:
                self.review_queue.pop(key, None)

    def add_product(self, product: NormalizedProduct) -> None:
        with self._lock:
            self.products[product.id] = product

    def add_products_bulk(self, products: list[NormalizedProduct]) -> None:
        """Phase 34W — single lock acquisition for N inserts.

        Trivial perf win for the in-memory store but the API parity
        matters: ingestion calls ``add_products_bulk`` regardless of
        backing store, and Postgres benefits dramatically from the
        same call shape.
        """
        with self._lock:
            for product in products:
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

    def delete_run(self, run_id: UUID) -> None:
        """Phase 34L — cleanup hook used by the zero-row partial-run
        guard. Best-effort: removes the run from the in-memory map.
        """
        with self._lock:
            self.runs.pop(run_id, None)

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
    def list_audit_events_for_project(
        self, project_id: UUID
    ) -> list[AuditEvent]:
        """Phase 34M — events whose target_id equals the project id."""
        return [
            ev for ev in self.audit_events if ev.target_id == project_id
        ]

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

    def get_pt_classifications_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, ProteinTrackerProductClassification]:
        # Phase 34W — O(1) dict-lookup per id; the Postgres impl uses
        # a single SELECT WHERE product_id IN (…) instead of N HTTP
        # round-trips.
        out: dict[UUID, ProteinTrackerProductClassification] = {}
        for pid in product_ids:
            cls = self.pt_classifications.get(pid)
            if cls is not None:
                out[pid] = cls
        return out

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

    def upsert_wwf_ingredients_for_product(
        self, product_id: UUID, ingredients: list[WWFCompositeIngredient]
    ) -> None:
        with self._lock:
            self.wwf_ingredients[product_id] = list(ingredients)

    def clear_wwf_ingredients_for_project(self, project_id: UUID) -> None:
        with self._lock:
            to_clear = [
                pid
                for pid, p in self.products.items()
                if p.project_id == project_id
            ]
            for pid in to_clear:
                self.wwf_ingredients.pop(pid, None)

    def get_wwf_ingredients_for_product(
        self, product_id: UUID
    ) -> list[WWFCompositeIngredient]:
        return list(self.wwf_ingredients.get(product_id, []))

    def get_user(self, user_id: UUID) -> UserProfile | None:
        return self.users.get(user_id)

    def upsert_user(self, profile: UserProfile) -> None:
        with self._lock:
            self.users[profile.user_id] = profile

    def list_members(self, org_id: UUID) -> list[UserProfile]:
        return [p for p in self.users.values() if p.organisation_id == org_id]

    def remove_member(self, user_id: UUID, org_id: UUID) -> None:
        with self._lock:
            profile = self.users.get(user_id)
            if profile is not None and profile.organisation_id == org_id:
                del self.users[user_id]

    def get_organisation(self, org_id: UUID) -> Organisation | None:
        return self.organisations.get(org_id)

    def create_organisation(
        self,
        *,
        name: str,
        slug: str,
        organisation_type: OrganisationType = OrganisationType.GMS_CLIENT,
    ) -> Organisation:
        now = datetime.now(UTC)
        org = Organisation(
            id=uuid4(),
            name=name,
            slug=slug,
            organisation_type=organisation_type,
            created_at=now,
        )
        with self._lock:
            self.organisations[org.id] = org
        return org

    def list_organisations(self) -> list[Organisation]:
        return list(self.organisations.values())

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
    # Classification jobs (Phase 34R) — async, chunked AI classification
    # ------------------------------------------------------------------
    def add_classification_job(self, job: ClassificationJob) -> None:
        from datetime import UTC, datetime

        with self._lock:
            stamped = (
                job
                if job.updated_at is not None
                else job.with_progress(updated_at=datetime.now(UTC))
            )
            self.classification_jobs[stamped.id] = stamped

    def update_classification_job(self, job: ClassificationJob) -> None:
        from datetime import UTC, datetime

        with self._lock:
            self.classification_jobs[job.id] = job.with_progress(
                updated_at=datetime.now(UTC)
            )

    def get_classification_job(
        self, job_id: UUID
    ) -> ClassificationJob | None:
        return self.classification_jobs.get(job_id)

    def list_classification_jobs_for_project(
        self, project_id: UUID
    ) -> list[ClassificationJob]:
        return [
            j
            for j in self.classification_jobs.values()
            if j.project_id == project_id
        ]

    def list_classification_jobs_for_upload(
        self, upload_id: UUID
    ) -> list[ClassificationJob]:
        return [
            j
            for j in self.classification_jobs.values()
            if j.upload_id == upload_id
        ]

    # ------------------------------------------------------------------
    # Ingestion jobs (Phase 34X) — chunked CSV ingestion
    # ------------------------------------------------------------------
    def add_ingestion_job(self, job: IngestionJob) -> None:
        from datetime import UTC, datetime

        with self._lock:
            stamped = (
                job
                if job.updated_at is not None
                else job.with_progress(updated_at=datetime.now(UTC))
            )
            self.ingestion_jobs[stamped.id] = stamped

    def update_ingestion_job(self, job: IngestionJob) -> None:
        from datetime import UTC, datetime

        with self._lock:
            self.ingestion_jobs[job.id] = job.with_progress(
                updated_at=datetime.now(UTC)
            )

    def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None:
        return self.ingestion_jobs.get(job_id)

    def list_ingestion_jobs_for_upload(
        self, upload_id: UUID
    ) -> list[IngestionJob]:
        return [
            j for j in self.ingestion_jobs.values() if j.upload_id == upload_id
        ]

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

    # ------------------------------------------------------------------
    # Nutrition reference tables (Phase 33H)
    # ------------------------------------------------------------------
    def list_nevo_entries(self) -> list[NevoEntry]:
        return list(self.nevo_entries)

    def list_ciqual_entries(self) -> list[CiqualEntry]:
        return list(self.ciqual_entries)

    def seed_nevo_entries(self, entries: list[NevoEntry]) -> None:
        """Test/bootstrap helper; production reads from the DB instead."""
        with self._lock:
            self.nevo_entries = list(entries)

    def seed_ciqual_entries(self, entries: list[CiqualEntry]) -> None:
        """Test/bootstrap helper; production reads from the DB instead."""
        with self._lock:
            self.ciqual_entries = list(entries)

    # ------------------------------------------------------------------
    # Recommendations (Phase 25B)
    # ------------------------------------------------------------------

    def upsert_recommendations_for_run(
        self,
        records: list[PersistedRecommendation],
    ) -> None:
        """Upsert a list of recommendations for a run.

        Existing records with the same (run_id, action_type) are updated in
        place but their status is preserved if it has already been promoted
        beyond 'draft'.  New records are inserted with status 'draft'.
        """
        _TERMINAL_STATUSES = {"proposed", "accepted", "dismissed", "archived"}
        with self._lock:
            if not records:
                return
            target_run_id = records[0].run_id
            existing_by_key: dict[tuple[UUID, str], PersistedRecommendation] = {
                (r.run_id, r.action_type): r
                for r in self.recommendations.values()
                if r.run_id == target_run_id
            }
            for rec in records:
                key = (rec.run_id, rec.action_type)
                existing = existing_by_key.get(key)
                if existing is not None:
                    # Preserve status if already promoted; update content fields.
                    import dataclasses
                    status_to_keep = (
                        existing.status if existing.status in _TERMINAL_STATUSES else rec.status
                    )
                    updated = dataclasses.replace(
                        existing,
                        title=rec.title,
                        description=rec.description,
                        rationale=rec.rationale,
                        expected_direction=rec.expected_direction,
                        priority=rec.priority,
                        confidence=rec.confidence,
                        evidence=rec.evidence,
                        caveats=rec.caveats,
                        client_facing=rec.client_facing,
                        status=status_to_keep,
                        updated_at=datetime.now(UTC),
                        updated_by=rec.updated_by,
                    )
                    self.recommendations[existing.id] = updated
                else:
                    self.recommendations[rec.id] = rec

    def list_recommendations_for_run(self, run_id: UUID) -> list[PersistedRecommendation]:
        return [r for r in self.recommendations.values() if r.run_id == run_id]

    def list_recommendations_for_project(
        self, project_id: UUID
    ) -> list[PersistedRecommendation]:
        return [r for r in self.recommendations.values() if r.project_id == project_id]

    def get_recommendation(self, recommendation_id: UUID) -> PersistedRecommendation | None:
        return self.recommendations.get(recommendation_id)

    def update_recommendation_status(
        self,
        recommendation_id: UUID,
        *,
        status: str,
        by_user_id: UUID,
    ) -> PersistedRecommendation | None:
        import dataclasses

        with self._lock:
            rec = self.recommendations.get(recommendation_id)
            if rec is None:
                return None
            updated = dataclasses.replace(
                rec,
                status=status,
                updated_at=datetime.now(UTC),
                updated_by=by_user_id,
            )
            self.recommendations[recommendation_id] = updated
            return updated

    # ------------------------------------------------------------------
    # Scenarios (Phase 26A)
    # ------------------------------------------------------------------

    def add_scenario(self, record: ScenarioRecord) -> None:
        with self._lock:
            self.scenarios[record.id] = record

    def get_scenario(self, scenario_id: UUID) -> ScenarioRecord | None:
        return self.scenarios.get(scenario_id)

    def list_scenarios_for_project(self, project_id: UUID) -> list[ScenarioRecord]:
        return [s for s in self.scenarios.values() if s.project_id == project_id]

    def update_scenario_status(self, scenario_id: UUID, *, status: str) -> ScenarioRecord | None:
        import dataclasses

        with self._lock:
            rec = self.scenarios.get(scenario_id)
            if rec is None:
                return None
            updated = dataclasses.replace(rec, status=status, updated_at=datetime.now(UTC))
            self.scenarios[scenario_id] = updated
            return updated

    def add_scenario_operation(self, record: ScenarioOperationRecord) -> None:
        with self._lock:
            self.scenario_operations[record.id] = record

    def list_scenario_operations(self, scenario_id: UUID) -> list[ScenarioOperationRecord]:
        return [
            op for op in self.scenario_operations.values() if op.scenario_id == scenario_id
        ]

    def save_scenario_result(self, record: ScenarioResultRecord) -> None:
        with self._lock:
            self.scenario_results[record.scenario_id] = record

    def get_scenario_result(self, scenario_id: UUID) -> ScenarioResultRecord | None:
        return self.scenario_results.get(scenario_id)
