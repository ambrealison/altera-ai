"""PostgresRepository — StoreProtocol implementation backed by Supabase.

Two clients are kept:

``_svc`` — service-role client.  Used for identity bootstrap operations
(``get_user``, ``upsert_user``, ``get_organisation``) and audit writes
that must bypass RLS.

``_rls`` — per-request JWT client.  Created from the anon key + user JWT
so Postgres RLS policies apply to every data operation.  Falls back to
``_svc`` when no JWT is available (dev mode, integration tests).
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

import supabase

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
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import ProteinTrackerProductClassification
from altera_api.domain.review import ManualReviewDecision, ManualReviewItem
from altera_api.domain.upload import Upload
from altera_api.domain.validation import ValidationReport
from altera_api.domain.wwf import WWFCompositeIngredient, WWFProductClassification
from altera_api.persistence.mappers import (
    enrichment_record_from_row,
    enrichment_record_to_row,
    export_record_from_row,
    export_record_to_row,
    job_from_row,
    job_to_row,
    manual_review_from_row,
    manual_review_to_row,
    organisation_from_row,
    persisted_recommendation_from_row,
    persisted_recommendation_to_row,
    product_from_row,
    product_to_row,
    project_from_row,
    project_to_row,
    pt_classification_from_row,
    pt_classification_to_row,
    review_decision_to_row,
    run_record_from_row,
    run_record_to_row,
    scenario_from_row,
    scenario_operation_from_row,
    scenario_operation_to_row,
    scenario_result_from_row,
    scenario_result_to_row,
    scenario_to_row,
    upload_record_from_rows,
    upload_to_row,
    user_profile_from_rows,
    wwf_classification_from_row,
    wwf_classification_to_row,
    wwf_ingredient_from_row,
)


class PostgresRepository:
    """StoreProtocol backed by Supabase Postgres via supabase-py."""

    def __init__(
        self,
        service_client: supabase.Client,
        rls_client: supabase.Client | None = None,
    ) -> None:
        self._svc = service_client
        # Data operations use the JWT-scoped client when available so
        # Postgres RLS policies apply; fall back to service role otherwise.
        self._rls = rls_client if rls_client is not None else service_client

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def default_org_id(self) -> UUID:
        raise RuntimeError(
            "default_org_id is unavailable in Postgres mode. "
            "Set ALTERA_DEV_ORGANISATION_ID explicitly."
        )

    @property
    def default_user_id(self) -> UUID:
        raise RuntimeError(
            "default_user_id is unavailable in Postgres mode. Set ALTERA_DEV_USER_ID explicitly."
        )

    def get_user(self, user_id: UUID) -> UserProfile | None:
        r = (
            self._svc.table("user_profiles")
            .select("*")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        profile_row = r.data[0]

        m = (
            self._svc.table("memberships")
            .select("organisation_id, role")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        if not m.data:
            return None
        return user_profile_from_rows(profile_row, m.data[0])

    def upsert_user(self, profile: UserProfile) -> None:
        self._svc.table("user_profiles").upsert(
            {
                "user_id": str(profile.user_id),
                "email": profile.email,
                "display_name": profile.display_name,
            },
            on_conflict="user_id",
        ).execute()
        self._svc.table("memberships").upsert(
            {
                "user_id": str(profile.user_id),
                "organisation_id": str(profile.organisation_id),
                "role": profile.role.value,
            },
            on_conflict="user_id,organisation_id",
        ).execute()

    def list_members(self, org_id: UUID) -> list[UserProfile]:
        m = (
            self._svc.table("memberships")
            .select("user_id, role, created_at")
            .eq("organisation_id", str(org_id))
            .execute()
        )
        if not m.data:
            return []
        user_ids = [row["user_id"] for row in m.data]
        p = (
            self._svc.table("user_profiles")
            .select("*")
            .in_("user_id", user_ids)
            .execute()
        )
        profile_map = {row["user_id"]: row for row in (p.data or [])}
        result = []
        for membership in m.data:
            uid = membership["user_id"]
            profile_row = profile_map.get(uid)
            if profile_row is None:
                continue
            result.append(
                user_profile_from_rows(
                    profile_row,
                    {"organisation_id": str(org_id), "role": membership["role"]},
                )
            )
        return result

    def remove_member(self, user_id: UUID, org_id: UUID) -> None:
        (
            self._svc.table("memberships")
            .delete()
            .eq("user_id", str(user_id))
            .eq("organisation_id", str(org_id))
            .execute()
        )

    def get_organisation(self, org_id: UUID) -> Organisation | None:
        r = self._svc.table("organisations").select("*").eq("id", str(org_id)).limit(1).execute()
        if not r.data:
            return None
        return organisation_from_row(r.data[0])

    def create_organisation(
        self,
        *,
        name: str,
        slug: str,
        organisation_type: OrganisationType = OrganisationType.GMS_CLIENT,
    ) -> Organisation:
        from datetime import UTC, datetime
        from uuid import uuid4

        org_id = uuid4()
        now = datetime.now(UTC)
        self._svc.table("organisations").insert(
            {
                "id": str(org_id),
                "name": name,
                "slug": slug,
                "organisation_type": organisation_type.value,
                "created_at": now.isoformat(),
            }
        ).execute()
        return Organisation(
            id=org_id,
            name=name,
            slug=slug,
            organisation_type=organisation_type,
            created_at=now,
        )

    def list_organisations(self) -> list[Organisation]:
        r = self._svc.table("organisations").select("*").order("created_at").execute()
        return [organisation_from_row(row) for row in (r.data or [])]

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
        from datetime import UTC, datetime
        from uuid import uuid4

        from altera_api.domain.project import ProjectStatus, PTValidationStatus

        project = Project(
            id=uuid4(),
            organisation_id=organisation_id or self.default_org_id,
            name=name,
            methodologies_enabled=methodologies_enabled,
            reporting_period_label=reporting_period_label,
            pt_validation_status=PTValidationStatus.NONE,
            project_status=ProjectStatus.CREATED,
            created_by=created_by or UUID(int=0),
            created_at=datetime.now(UTC),
        )
        self._rls.table("projects").insert(project_to_row(project)).execute()
        return project

    def list_projects(self) -> list[Project]:
        r = self._rls.table("projects").select("*").execute()
        return [project_from_row(row) for row in (r.data or [])]

    def get_project(self, project_id: UUID) -> Project | None:
        r = self._rls.table("projects").select("*").eq("id", str(project_id)).limit(1).execute()
        if not r.data:
            return None
        return project_from_row(r.data[0])

    # ------------------------------------------------------------------
    # Uploads + products
    # ------------------------------------------------------------------

    def add_upload(self, upload: Upload, product_ids: list[UUID]) -> None:
        # product_ids are already written via add_product; store the upload row.
        self._rls.table("uploads").insert(upload_to_row(upload)).execute()

    def get_upload(self, upload_id: UUID) -> UploadRecord | None:
        r = self._rls.table("uploads").select("*").eq("id", str(upload_id)).limit(1).execute()
        if not r.data:
            return None
        pr = self._rls.table("products").select("id").eq("upload_id", str(upload_id)).execute()
        return upload_record_from_rows(r.data[0], pr.data or [])

    def list_uploads_for_project(self, project_id: UUID) -> list[UploadRecord]:
        r = self._rls.table("uploads").select("*").eq("project_id", str(project_id)).execute()
        rows = r.data or []
        if not rows:
            return []
        upload_ids = [row["id"] for row in rows]
        pr = (
            self._rls.table("products")
            .select("id, upload_id")
            .in_("upload_id", upload_ids)
            .execute()
        )
        products_by_upload: dict[str, list[dict]] = defaultdict(list)
        for p in pr.data or []:
            products_by_upload[p["upload_id"]].append(p)
        return [upload_record_from_rows(row, products_by_upload.get(row["id"], [])) for row in rows]

    def add_product(self, product: NormalizedProduct) -> None:
        self._rls.table("products").insert(product_to_row(product)).execute()

    def list_products_for_project(self, project_id: UUID) -> list[NormalizedProduct]:
        proj = self.get_project(project_id)
        if proj is None:
            return []
        r = self._rls.table("products").select("*").eq("project_id", str(project_id)).execute()
        return [
            product_from_row(row, methodologies_enabled=proj.methodologies_enabled)
            for row in (r.data or [])
        ]

    def get_product(self, product_id: UUID) -> NormalizedProduct | None:
        r = self._rls.table("products").select("*").eq("id", str(product_id)).limit(1).execute()
        if not r.data:
            return None
        row = r.data[0]
        proj = self.get_project(UUID(row["project_id"]))
        if proj is None:
            return None
        return product_from_row(row, methodologies_enabled=proj.methodologies_enabled)

    # ------------------------------------------------------------------
    # Classifications
    # ------------------------------------------------------------------

    def _get_org_id_for_product(self, product_id: UUID) -> UUID:
        r = (
            self._rls.table("products")
            .select("organisation_id")
            .eq("id", str(product_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            raise LookupError(f"product {product_id} not found")
        return UUID(r.data[0]["organisation_id"])

    def upsert_pt_classification(self, classification: ProteinTrackerProductClassification) -> None:
        org_id = self._get_org_id_for_product(classification.product_id)
        self._rls.table("classifications").upsert(
            pt_classification_to_row(classification, organisation_id=org_id),
            on_conflict="product_id,methodology",
        ).execute()

    def get_pt_classification(self, product_id: UUID) -> ProteinTrackerProductClassification | None:
        r = (
            self._rls.table("classifications")
            .select("*")
            .eq("product_id", str(product_id))
            .eq("methodology", "protein_tracker")
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return pt_classification_from_row(r.data[0])

    def upsert_wwf_classification(self, classification: WWFProductClassification) -> None:
        org_id = self._get_org_id_for_product(classification.product_id)
        self._rls.table("classifications").upsert(
            wwf_classification_to_row(classification, organisation_id=org_id),
            on_conflict="product_id,methodology",
        ).execute()

    def get_wwf_classification(self, product_id: UUID) -> WWFProductClassification | None:
        r = (
            self._rls.table("classifications")
            .select("*")
            .eq("product_id", str(product_id))
            .eq("methodology", "wwf")
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return wwf_classification_from_row(r.data[0])

    # ------------------------------------------------------------------
    # WWF composite ingredients
    # ------------------------------------------------------------------

    def get_wwf_ingredients_by_project(
        self, project_id: UUID
    ) -> dict[UUID, list[WWFCompositeIngredient]]:
        pr = self._rls.table("products").select("id").eq("project_id", str(project_id)).execute()
        product_ids = [r["id"] for r in (pr.data or [])]
        if not product_ids:
            return {}
        ir = (
            self._rls.table("product_composite_ingredients")
            .select("*")
            .in_("product_id", product_ids)
            .execute()
        )
        result: dict[UUID, list[WWFCompositeIngredient]] = defaultdict(list)
        for row in ir.data or []:
            ing = wwf_ingredient_from_row(row)
            result[ing.parent_product_id].append(ing)
        return dict(result)

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    def upsert_review_item(self, item: ManualReviewItem) -> None:
        org_id = self._get_org_id_for_product(item.product_id)
        self._rls.table("manual_reviews").upsert(
            manual_review_to_row(item, organisation_id=org_id),
            on_conflict="product_id,methodology",
        ).execute()

    def remove_review_item(self, product_id: UUID, methodology: Methodology) -> None:
        self._rls.table("manual_reviews").delete().eq("product_id", str(product_id)).eq(
            "methodology", methodology.value
        ).execute()

    def get_review_item(
        self, product_id: UUID, methodology: Methodology
    ) -> ManualReviewItem | None:
        r = (
            self._rls.table("manual_reviews")
            .select("*")
            .eq("product_id", str(product_id))
            .eq("methodology", methodology.value)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return manual_review_from_row(r.data[0])

    def list_review_items_for_project(
        self, project_id: UUID, *, methodology: Methodology | None = None
    ) -> list[ManualReviewItem]:
        pr = self._rls.table("products").select("id").eq("project_id", str(project_id)).execute()
        product_ids = [r["id"] for r in (pr.data or [])]
        if not product_ids:
            return []
        q = self._rls.table("manual_reviews").select("*").in_("product_id", product_ids)
        if methodology is not None:
            q = q.eq("methodology", methodology.value)
        r = q.execute()
        return [manual_review_from_row(row) for row in (r.data or [])]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def add_run(self, record: RunRecord) -> None:
        self._rls.table("calculation_runs").insert(run_record_to_row(record)).execute()

    def get_run(self, run_id: UUID) -> RunRecord | None:
        r = self._rls.table("calculation_runs").select("*").eq("id", str(run_id)).limit(1).execute()
        if not r.data:
            return None
        return run_record_from_row(r.data[0])

    def list_runs_for_project(self, project_id: UUID) -> list[RunRecord]:
        r = (
            self._rls.table("calculation_runs")
            .select("*")
            .eq("project_id", str(project_id))
            .execute()
        )
        return [run_record_from_row(row) for row in (r.data or [])]

    # ------------------------------------------------------------------
    # Export records
    # ------------------------------------------------------------------

    def add_export_record(self, record: ExportRecord) -> None:
        try:
            self._rls.table("report_exports").insert(export_record_to_row(record)).execute()
        except Exception:
            pass

    def get_export_record(self, export_id: UUID) -> ExportRecord | None:
        try:
            r = (
                self._rls.table("report_exports")
                .select("*")
                .eq("id", str(export_id))
                .limit(1)
                .execute()
            )
            return export_record_from_row(r.data[0]) if r.data else None
        except Exception:
            return None

    def get_exports_for_run(self, run_id: UUID) -> list[ExportRecord]:
        try:
            r = self._rls.table("report_exports").select("*").eq("run_id", str(run_id)).execute()
            return [export_record_from_row(row) for row in (r.data or [])]
        except Exception:
            return []

    def update_export_approval(
        self,
        export_id: UUID,
        *,
        approval_status: str,
        by_user_id: UUID,
        rejection_reason: str | None = None,
    ) -> ExportRecord | None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        update: dict = {"approval_status": approval_status}
        if approval_status == "approved":
            update["approved_by"] = str(by_user_id)
            update["approved_at"] = now
        elif approval_status == "rejected":
            update["rejected_by"] = str(by_user_id)
            update["rejected_at"] = now
            if rejection_reason is not None:
                update["rejection_reason"] = rejection_reason
        try:
            r = self._svc.table("report_exports").update(update).eq("id", str(export_id)).execute()
            return export_record_from_row(r.data[0]) if r.data else None
        except Exception:
            return None

    def mark_export_under_review(self, export_id: UUID, *, by_user_id: UUID) -> ExportRecord | None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        try:
            r = (
                self._svc.table("report_exports")
                .update(
                    {
                        "approval_status": "under_review",
                        "under_review_by": str(by_user_id),
                        "under_review_at": now,
                    }
                )
                .eq("id", str(export_id))
                .execute()
            )
            return export_record_from_row(r.data[0]) if r.data else None
        except Exception:
            return None

    def deliver_export(self, export_id: UUID, *, by_user_id: UUID) -> ExportRecord | None:
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        try:
            r = (
                self._svc.table("report_exports")
                .update(
                    {
                        "approval_status": "delivered",
                        "delivered_by": str(by_user_id),
                        "delivered_at": now,
                    }
                )
                .eq("id", str(export_id))
                .execute()
            )
            return export_record_from_row(r.data[0]) if r.data else None
        except Exception:
            return None

    def record_client_download(self, export_id: UUID) -> ExportRecord | None:
        from datetime import UTC, datetime

        try:
            current = self.get_export_record(export_id)
            if current is None:
                return None
            now = datetime.now(UTC).isoformat()
            update: dict = {
                "client_download_count": current.client_download_count + 1,
            }
            if current.client_downloaded_at is None:
                update["client_downloaded_at"] = now
            r = self._svc.table("report_exports").update(update).eq("id", str(export_id)).execute()
            return export_record_from_row(r.data[0]) if r.data else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def append_audit(self, event: AuditEvent) -> None:
        # Audit events are fire-and-forget; failures are non-fatal.
        try:
            self._svc.table("audit_events").insert(
                {
                    "id": str(event.id),
                    "organisation_id": str(event.organisation_id),
                    "actor_user_id": str(event.actor_user_id),
                    "action": event.action,
                    "resource_type": event.resource_type,
                    "resource_id": str(event.resource_id) if event.resource_id else None,
                    "payload": event.payload,
                    "occurred_at": event.occurred_at.isoformat(),
                }
            ).execute()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Upload lifecycle (Phase 15)
    # ------------------------------------------------------------------

    def update_upload(self, upload: Upload, *, product_ids: list[UUID] | None = None) -> None:
        row = upload_to_row(upload)
        row.pop("id", None)
        self._rls.table("uploads").update(row).eq("id", str(upload.id)).execute()

    def set_upload_validation_report(
        self,
        upload_id: UUID,
        report: ValidationReport,
        *,
        duplicate_of: UUID | None = None,
    ) -> None:
        update: dict = {"validation_report": report.model_dump(mode="json")}
        if duplicate_of is not None:
            update["duplicate_of_upload_id"] = str(duplicate_of)
        self._rls.table("uploads").update(update).eq("id", str(upload_id)).execute()

    def get_upload_validation_report(self, upload_id: UUID) -> ValidationReport | None:
        r = (
            self._rls.table("uploads")
            .select("validation_report")
            .eq("id", str(upload_id))
            .limit(1)
            .execute()
        )
        if not r.data or r.data[0].get("validation_report") is None:
            return None
        try:
            return ValidationReport.model_validate(r.data[0]["validation_report"])
        except Exception:
            return None

    def find_upload_by_checksum(self, project_id: UUID, checksum: str) -> Upload | None:
        r = (
            self._rls.table("uploads")
            .select("*")
            .eq("project_id", str(project_id))
            .eq("checksum_sha256", checksum)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return upload_record_from_rows(r.data[0], []).upload

    # ------------------------------------------------------------------
    # WWF ingredients — missing write/clear methods (Phase 24A)
    # ------------------------------------------------------------------

    def upsert_wwf_ingredients_for_product(
        self, product_id: UUID, ingredients: list[WWFCompositeIngredient]
    ) -> None:
        self._rls.table("product_composite_ingredients").delete().eq(
            "product_id", str(product_id)
        ).execute()
        if ingredients:
            rows = []
            for ing in ingredients:
                subgroup: str | None = None
                for sg in (ing.fg1_subgroup, ing.fg2_subgroup):
                    if sg is not None:
                        subgroup = sg.value
                        break
                rows.append(
                    {
                        "id": str(ing.id),
                        "product_id": str(product_id),
                        "food_group": ing.food_group.value,
                        "subgroup": subgroup,
                        "ingredient_weight_kg_per_item": float(
                            ing.ingredient_weight_kg_per_item
                        ),
                    }
                )
            self._rls.table("product_composite_ingredients").insert(rows).execute()

    def clear_wwf_ingredients_for_project(self, project_id: UUID) -> None:
        pr = self._rls.table("products").select("id").eq("project_id", str(project_id)).execute()
        product_ids = [r["id"] for r in (pr.data or [])]
        if product_ids:
            self._rls.table("product_composite_ingredients").delete().in_(
                "product_id", product_ids
            ).execute()

    def get_wwf_ingredients_for_product(
        self, product_id: UUID
    ) -> list[WWFCompositeIngredient]:
        r = (
            self._rls.table("product_composite_ingredients")
            .select("*")
            .eq("product_id", str(product_id))
            .execute()
        )
        return [wwf_ingredient_from_row(row) for row in (r.data or [])]

    # ------------------------------------------------------------------
    # Review decisions (Phase 19C)
    # ------------------------------------------------------------------

    def add_review_decision(self, decision: ManualReviewDecision) -> None:
        try:
            self._rls.table("review_decisions").insert(
                review_decision_to_row(decision)
            ).execute()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Jobs (Phase 16)
    # ------------------------------------------------------------------

    def add_job(self, job: Job) -> None:
        self._rls.table("jobs").insert(job_to_row(job)).execute()

    def update_job(self, job: Job) -> None:
        row = job_to_row(job)
        row.pop("job_id", None)
        self._rls.table("jobs").update(row).eq("job_id", str(job.job_id)).execute()

    def get_job(self, job_id: UUID) -> Job | None:
        r = (
            self._rls.table("jobs")
            .select("*")
            .eq("job_id", str(job_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return job_from_row(r.data[0])

    def list_jobs_for_project(self, project_id: UUID) -> list[Job]:
        r = (
            self._rls.table("jobs")
            .select("*")
            .eq("project_id", str(project_id))
            .execute()
        )
        return [job_from_row(row) for row in (r.data or [])]

    def find_active_job(self, *, job_type: JobType, idempotency_key: str) -> Job | None:
        r = (
            self._rls.table("jobs")
            .select("*")
            .eq("job_type", job_type.value)
            .eq("idempotency_key", idempotency_key)
            .in_("status", [JobStatus.QUEUED.value, JobStatus.RUNNING.value])
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return job_from_row(r.data[0])

    # ------------------------------------------------------------------
    # Nutrition enrichment (Phase 23A)
    # ------------------------------------------------------------------

    def add_enrichment_record(self, record: NutritionEnrichmentRecord) -> None:
        self._rls.table("nutrition_enrichment_records").insert(
            enrichment_record_to_row(record)
        ).execute()

    def get_enrichment_records_for_product(
        self, product_id: UUID
    ) -> list[NutritionEnrichmentRecord]:
        r = (
            self._rls.table("nutrition_enrichment_records")
            .select("*")
            .eq("product_id", str(product_id))
            .execute()
        )
        return [enrichment_record_from_row(row) for row in (r.data or [])]

    def list_enrichment_records_for_project(
        self, project_id: UUID
    ) -> list[NutritionEnrichmentRecord]:
        pr = self._rls.table("products").select("id").eq("project_id", str(project_id)).execute()
        product_ids = [r["id"] for r in (pr.data or [])]
        if not product_ids:
            return []
        r = (
            self._rls.table("nutrition_enrichment_records")
            .select("*")
            .in_("product_id", product_ids)
            .execute()
        )
        return [enrichment_record_from_row(row) for row in (r.data or [])]

    # ------------------------------------------------------------------
    # Recommendations (Phase 25B)
    # ------------------------------------------------------------------

    def upsert_recommendations_for_run(
        self, records: list[PersistedRecommendation]
    ) -> None:
        if not records:
            return
        rows = [persisted_recommendation_to_row(r) for r in records]
        self._rls.table("recommendations").upsert(
            rows, on_conflict="run_id,action_type"
        ).execute()

    def list_recommendations_for_run(
        self, run_id: UUID
    ) -> list[PersistedRecommendation]:
        r = (
            self._rls.table("recommendations")
            .select("*")
            .eq("run_id", str(run_id))
            .execute()
        )
        return [persisted_recommendation_from_row(row) for row in (r.data or [])]

    def list_recommendations_for_project(
        self, project_id: UUID
    ) -> list[PersistedRecommendation]:
        r = (
            self._rls.table("recommendations")
            .select("*")
            .eq("project_id", str(project_id))
            .execute()
        )
        return [persisted_recommendation_from_row(row) for row in (r.data or [])]

    def get_recommendation(
        self, recommendation_id: UUID
    ) -> PersistedRecommendation | None:
        r = (
            self._rls.table("recommendations")
            .select("*")
            .eq("id", str(recommendation_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return persisted_recommendation_from_row(r.data[0])

    def update_recommendation_status(
        self,
        recommendation_id: UUID,
        *,
        status: str,
        by_user_id: UUID,
    ) -> PersistedRecommendation | None:
        from datetime import UTC, datetime

        r = (
            self._svc.table("recommendations")
            .update(
                {
                    "status": status,
                    "updated_by": str(by_user_id),
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", str(recommendation_id))
            .execute()
        )
        if not r.data:
            return None
        return persisted_recommendation_from_row(r.data[0])

    # ------------------------------------------------------------------
    # Scenarios (Phase 26A)
    # ------------------------------------------------------------------

    def add_scenario(self, record: ScenarioRecord) -> None:
        self._rls.table("scenarios").insert(scenario_to_row(record)).execute()

    def get_scenario(self, scenario_id: UUID) -> ScenarioRecord | None:
        r = (
            self._rls.table("scenarios")
            .select("*")
            .eq("id", str(scenario_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return scenario_from_row(r.data[0])

    def list_scenarios_for_project(self, project_id: UUID) -> list[ScenarioRecord]:
        r = (
            self._rls.table("scenarios")
            .select("*")
            .eq("project_id", str(project_id))
            .execute()
        )
        return [scenario_from_row(row) for row in (r.data or [])]

    def update_scenario_status(
        self, scenario_id: UUID, *, status: str
    ) -> ScenarioRecord | None:
        from datetime import UTC, datetime

        r = (
            self._rls.table("scenarios")
            .update(
                {
                    "status": status,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            .eq("id", str(scenario_id))
            .execute()
        )
        if not r.data:
            return None
        return scenario_from_row(r.data[0])

    def add_scenario_operation(self, record: ScenarioOperationRecord) -> None:
        self._rls.table("scenario_operations").insert(
            scenario_operation_to_row(record)
        ).execute()

    def list_scenario_operations(
        self, scenario_id: UUID
    ) -> list[ScenarioOperationRecord]:
        r = (
            self._rls.table("scenario_operations")
            .select("*")
            .eq("scenario_id", str(scenario_id))
            .order("order")
            .execute()
        )
        return [scenario_operation_from_row(row) for row in (r.data or [])]

    def save_scenario_result(self, record: ScenarioResultRecord) -> None:
        self._rls.table("scenario_results").upsert(
            scenario_result_to_row(record), on_conflict="scenario_id"
        ).execute()

    def get_scenario_result(self, scenario_id: UUID) -> ScenarioResultRecord | None:
        r = (
            self._rls.table("scenario_results")
            .select("*")
            .eq("scenario_id", str(scenario_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return scenario_result_from_row(r.data[0])
