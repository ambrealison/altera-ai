"""StoreProtocol — the interface every repository implementation satisfies.

Structural (duck-typed) Protocol so both ``MemoryRepository`` and
``PostgresRepository`` can be used wherever a ``StoreProtocol`` is
expected without inheriting from a common base class.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
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
from altera_api.domain.audit import AuditEvent
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.classification_job import ClassificationJob
from altera_api.domain.common import Methodology, OrganisationType
from altera_api.domain.enrichment import NutritionEnrichmentRecord
from altera_api.domain.ingestion_job import IngestionJob
from altera_api.domain.job import Job, JobType
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import ProteinTrackerProductClassification
from altera_api.domain.review import ManualReviewDecision, ManualReviewItem
from altera_api.domain.upload import Upload
from altera_api.domain.validation import ValidationReport
from altera_api.domain.wwf import WWFCompositeIngredient, WWFProductClassification


@runtime_checkable
class StoreProtocol(Protocol):
    """Every method that routes, the orchestrator, or the auth dependency
    calls on the backing store. Both ``MemoryRepository`` and
    ``PostgresRepository`` satisfy this structurally."""

    # ------------------------------------------------------------------
    # Identity (used by dev-auth fallback)
    # ------------------------------------------------------------------
    @property
    def default_org_id(self) -> UUID: ...
    @property
    def default_user_id(self) -> UUID: ...

    def get_user(self, user_id: UUID) -> UserProfile | None: ...
    def upsert_user(self, profile: UserProfile) -> None: ...
    def list_members(self, org_id: UUID) -> list[UserProfile]: ...
    def remove_member(self, user_id: UUID, org_id: UUID) -> None: ...
    def get_organisation(self, org_id: UUID) -> Organisation | None: ...
    def create_organisation(
        self,
        *,
        name: str,
        slug: str,
        organisation_type: OrganisationType = OrganisationType.GMS_CLIENT,
    ) -> Organisation: ...
    def list_organisations(self) -> list[Organisation]: ...

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
    ) -> Project: ...

    def list_projects(self) -> list[Project]: ...
    def get_project(self, project_id: UUID) -> Project | None: ...

    # ------------------------------------------------------------------
    # Uploads + products
    # ------------------------------------------------------------------
    def add_upload(self, upload: Upload, product_ids: list[UUID]) -> None: ...
    def update_upload(self, upload: Upload, *, product_ids: list[UUID] | None = None) -> None: ...
    def get_upload(self, upload_id: UUID) -> UploadRecord | None: ...
    def list_uploads_for_project(self, project_id: UUID) -> list[UploadRecord]: ...
    def set_upload_validation_report(
        self,
        upload_id: UUID,
        report: ValidationReport,
        *,
        duplicate_of: UUID | None = None,
    ) -> None: ...
    def get_upload_validation_report(self, upload_id: UUID) -> ValidationReport | None: ...
    def find_upload_by_checksum(self, project_id: UUID, checksum: str) -> Upload | None: ...
    def delete_upload(self, upload_id: UUID) -> None: ...

    def add_product(self, product: NormalizedProduct) -> None: ...
    def add_products_bulk(
        self, products: list[NormalizedProduct]
    ) -> None: ...
    def list_products_for_project(self, project_id: UUID) -> list[NormalizedProduct]: ...
    def get_product(self, product_id: UUID) -> NormalizedProduct | None: ...
    # Phase 34W — batch classification lookup. The project-detail
    # route used to make N HTTP round-trips (one per product) just to
    # count "unclassified" rows; with this method a single SELECT WHERE
    # product_id IN (…) suffices.
    def get_pt_classifications_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, ProteinTrackerProductClassification]: ...

    # Phase 35-perf — bulk product lookup. Replaces the per-product
    # ``get_product`` loop in the classification orchestrator's
    # ``_eligible_product_ids`` and per-batch product load, both of
    # which contributed N HTTP round-trips per 1000-row job
    # (~80s on Render).
    def list_products_by_ids(
        self, product_ids: list[UUID]
    ) -> list[NormalizedProduct]: ...

    # ------------------------------------------------------------------
    # Classifications
    # ------------------------------------------------------------------
    def upsert_pt_classification(
        self, classification: ProteinTrackerProductClassification
    ) -> None: ...
    def get_pt_classification(
        self, product_id: UUID
    ) -> ProteinTrackerProductClassification | None: ...

    def upsert_wwf_classification(self, classification: WWFProductClassification) -> None: ...
    def get_wwf_classification(self, product_id: UUID) -> WWFProductClassification | None: ...

    # Phase 35-perf — symmetric bulk lookup for WWF. Used by the
    # eligibility filter when a job targets methodology=WWF; without
    # it we'd N+1 ``get_wwf_classification`` per product.
    def get_wwf_classifications_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, WWFProductClassification]: ...

    # ------------------------------------------------------------------
    # WWF ingredients
    # ------------------------------------------------------------------
    def get_wwf_ingredients_by_project(
        self, project_id: UUID
    ) -> dict[UUID, list[WWFCompositeIngredient]]: ...

    def upsert_wwf_ingredients_for_product(
        self, product_id: UUID, ingredients: list[WWFCompositeIngredient]
    ) -> None: ...

    def clear_wwf_ingredients_for_project(self, project_id: UUID) -> None: ...

    def get_wwf_ingredients_for_product(
        self, product_id: UUID
    ) -> list[WWFCompositeIngredient]: ...

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------
    def upsert_review_item(self, item: ManualReviewItem) -> None: ...
    def remove_review_item(self, product_id: UUID, methodology: Methodology) -> None: ...
    def get_review_item(
        self, product_id: UUID, methodology: Methodology
    ) -> ManualReviewItem | None: ...
    def list_review_items_for_project(
        self, project_id: UUID, *, methodology: Methodology | None = None
    ) -> list[ManualReviewItem]: ...
    # Phase 36C — count-only probe for the projects-list endpoint.
    # ``_project_response`` only needs ``len(list_review_items_*)``;
    # loading every review row for that ``len()`` cost ~8 round-trips
    # per project. Implementations use ``count="exact", head=True``
    # so no rows cross the wire — just a Content-Range header.
    def count_review_items_for_product_ids(
        self,
        product_ids: list[UUID],
        *,
        methodology: Methodology | None = None,
    ) -> int: ...
    def add_review_decision(self, decision: ManualReviewDecision) -> None: ...

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------
    def add_run(self, record: RunRecord) -> None: ...
    def get_run(self, run_id: UUID) -> RunRecord | None: ...
    def list_runs_for_project(self, project_id: UUID) -> list[RunRecord]: ...

    # ------------------------------------------------------------------
    # Export records
    # ------------------------------------------------------------------
    def add_export_record(self, record: ExportRecord) -> None: ...
    def get_export_record(self, export_id: UUID) -> ExportRecord | None: ...
    def get_exports_for_run(self, run_id: UUID) -> list[ExportRecord]: ...
    def update_export_approval(
        self,
        export_id: UUID,
        *,
        approval_status: str,
        by_user_id: UUID,
        rejection_reason: str | None = None,
    ) -> ExportRecord | None: ...
    def mark_export_under_review(
        self, export_id: UUID, *, by_user_id: UUID
    ) -> ExportRecord | None: ...
    def deliver_export(self, export_id: UUID, *, by_user_id: UUID) -> ExportRecord | None: ...
    def record_client_download(self, export_id: UUID) -> ExportRecord | None: ...

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def append_audit(self, event: AuditEvent) -> None: ...
    def list_audit_events_for_project(
        self, project_id: UUID
    ) -> list[AuditEvent]:
        """Phase 34M — return audit events that target the project.

        Used to detect whether NEVO enrichment has been attempted so
        the workflow Step 5 can flip to ``complete`` after the user's
        first run (even if only a fraction of products matched).
        """
        ...

    # ------------------------------------------------------------------
    # Jobs (Phase 16)
    # ------------------------------------------------------------------
    def add_job(self, job: Job) -> None: ...
    def update_job(self, job: Job) -> None: ...
    def get_job(self, job_id: UUID) -> Job | None: ...
    def list_jobs_for_project(self, project_id: UUID) -> list[Job]: ...
    def find_active_job(self, *, job_type: JobType, idempotency_key: str) -> Job | None: ...

    # ------------------------------------------------------------------
    # Classification jobs (Phase 34R) — async, chunked AI classification
    # ------------------------------------------------------------------
    def add_classification_job(self, job: ClassificationJob) -> None: ...
    def update_classification_job(self, job: ClassificationJob) -> None: ...
    def get_classification_job(
        self, job_id: UUID
    ) -> ClassificationJob | None: ...
    def list_classification_jobs_for_project(
        self, project_id: UUID
    ) -> list[ClassificationJob]: ...
    def list_classification_jobs_for_upload(
        self, upload_id: UUID
    ) -> list[ClassificationJob]: ...
    # Phase 35A — resume support. Returns the most-recent
    # non-terminal classification job for the (upload_id,
    # methodology) pair, or None. The frontend uses this to detect
    # an interrupted job on wizard re-mount and offer a "Reprendre"
    # button instead of duplicating the work.
    def find_active_classification_job(
        self,
        *,
        upload_id: UUID,
        methodology: Methodology,
    ) -> ClassificationJob | None: ...
    # Phase 35B — heavy-job guard. Returns counts of non-terminal
    # heavy jobs visible to the caller's RLS scope. Used to block
    # the creation of a NEW heavy job when another is in flight.
    #
    # Phase 35-stale — ``max_age_minutes`` filters out jobs whose
    # ``updated_at`` is older than the cutoff. A worker that crashes
    # mid-job leaves a stale ``queued`` / ``running`` record that
    # otherwise blocks every subsequent creation until manual
    # cleanup. Default ``None`` means "no age filter" (counts all
    # non-terminal jobs).
    def count_active_heavy_classification_jobs(
        self,
        *,
        min_total_products: int = 500,
        max_age_minutes: int | None = None,
    ) -> int: ...
    def count_active_heavy_ingestion_jobs(
        self,
        *,
        min_total_rows: int = 1000,
        max_age_minutes: int | None = None,
    ) -> int: ...
    # Phase 35-stale — listings + bulk-heal for the admin endpoints.
    # The list methods return full domain objects so the admin UI
    # can show counts + ages; cancel_stale_* returns the number of
    # rows that were transitioned to a terminal state.
    def list_active_heavy_classification_jobs(
        self, *, min_total_products: int = 500
    ) -> list[ClassificationJob]: ...
    def list_active_heavy_ingestion_jobs(
        self, *, min_total_rows: int = 1000
    ) -> list[IngestionJob]: ...
    def cancel_stale_classification_jobs(
        self, *, stale_after_minutes: int = 30
    ) -> int: ...
    def cancel_stale_ingestion_jobs(
        self, *, stale_after_minutes: int = 30
    ) -> int: ...

    # ------------------------------------------------------------------
    # Ingestion jobs (Phase 34X) — chunked, resumable CSV ingestion
    # ------------------------------------------------------------------
    def add_ingestion_job(self, job: IngestionJob) -> None: ...
    def update_ingestion_job(self, job: IngestionJob) -> None: ...
    def get_ingestion_job(self, job_id: UUID) -> IngestionJob | None: ...
    def list_ingestion_jobs_for_upload(
        self, upload_id: UUID
    ) -> list[IngestionJob]: ...

    # ------------------------------------------------------------------
    # Nutrition enrichment (Phase 23A)
    # ------------------------------------------------------------------
    def add_enrichment_record(self, record: NutritionEnrichmentRecord) -> None: ...
    def get_enrichment_records_for_product(
        self, product_id: UUID
    ) -> list[NutritionEnrichmentRecord]: ...
    def list_enrichment_records_for_project(
        self, project_id: UUID
    ) -> list[NutritionEnrichmentRecord]: ...
    # Phase 34Z — bulk enrichment lookup for workflow-status.
    # Returns a dict keyed by product_id so the workflow aggregator
    # can replace per-product round-trips with a single
    # ``WHERE product_id IN (…)`` query.
    def get_enrichment_records_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, list[NutritionEnrichmentRecord]]: ...
    # Phase 34Z-fix — boolean probe for ``nevo_attempted``.
    # ``compute_workflow_status`` only needs "did NEVO ever run?",
    # which doesn't require loading every record. The Postgres impl
    # uses a count-only ``head=True`` query; the in-memory impl
    # checks dict size.
    def project_has_any_enrichment(self, project_id: UUID) -> bool: ...

    # ------------------------------------------------------------------
    # Nutrition reference tables (Phase 33H — NEVO and CIQUAL lookup)
    # ------------------------------------------------------------------
    def list_nevo_entries(self) -> list[NevoEntry]: ...
    # Phase 36C — cheap count probe used by calculation-preflight. The
    # endpoint only surfaces ``nevo_total_references`` (a single int);
    # loading 2300+ NEVO rows just to call ``len()`` was wasteful.
    def count_nevo_entries(self) -> int: ...
    def list_ciqual_entries(self) -> list[CiqualEntry]: ...

    # ------------------------------------------------------------------
    # Recommendations (Phase 25B)
    # ------------------------------------------------------------------
    def upsert_recommendations_for_run(
        self, records: list[PersistedRecommendation]
    ) -> None: ...
    def list_recommendations_for_run(
        self, run_id: UUID
    ) -> list[PersistedRecommendation]: ...
    def list_recommendations_for_project(
        self, project_id: UUID
    ) -> list[PersistedRecommendation]: ...
    def get_recommendation(
        self, recommendation_id: UUID
    ) -> PersistedRecommendation | None: ...
    def update_recommendation_status(
        self,
        recommendation_id: UUID,
        *,
        status: str,
        by_user_id: UUID,
    ) -> PersistedRecommendation | None: ...

    # ------------------------------------------------------------------
    # Scenarios (Phase 26A)
    # ------------------------------------------------------------------
    def add_scenario(self, record: ScenarioRecord) -> None: ...
    def get_scenario(self, scenario_id: UUID) -> ScenarioRecord | None: ...
    def list_scenarios_for_project(self, project_id: UUID) -> list[ScenarioRecord]: ...
    def update_scenario_status(self, scenario_id: UUID, *, status: str) -> ScenarioRecord | None: ...
    def add_scenario_operation(self, record: ScenarioOperationRecord) -> None: ...
    def list_scenario_operations(self, scenario_id: UUID) -> list[ScenarioOperationRecord]: ...
    def save_scenario_result(self, record: ScenarioResultRecord) -> None: ...
    def get_scenario_result(self, scenario_id: UUID) -> ScenarioResultRecord | None: ...
