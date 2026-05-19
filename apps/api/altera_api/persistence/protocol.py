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
from altera_api.domain.common import Methodology, OrganisationType
from altera_api.domain.enrichment import NutritionEnrichmentRecord
from altera_api.domain.job import Job, JobType
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

    def add_product(self, product: NormalizedProduct) -> None: ...
    def list_products_for_project(self, project_id: UUID) -> list[NormalizedProduct]: ...
    def get_product(self, product_id: UUID) -> NormalizedProduct | None: ...

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

    # ------------------------------------------------------------------
    # Jobs (Phase 16)
    # ------------------------------------------------------------------
    def add_job(self, job: Job) -> None: ...
    def update_job(self, job: Job) -> None: ...
    def get_job(self, job_id: UUID) -> Job | None: ...
    def list_jobs_for_project(self, project_id: UUID) -> list[Job]: ...
    def find_active_job(self, *, job_type: JobType, idempotency_key: str) -> Job | None: ...

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
