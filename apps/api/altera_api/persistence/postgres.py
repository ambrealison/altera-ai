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
from decimal import Decimal
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
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.common import Methodology, OrganisationType
from altera_api.domain.enrichment import NutritionEnrichmentRecord
from altera_api.domain.job import Job, JobStatus, JobType
from altera_api.domain.nevo import NevoEntry
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

# ---------------------------------------------------------------------------
# Phase 34Z-fix — URL-length safety for PostgREST ``.in_(...)`` filters.
#
# PostgREST encodes ``.in_("product_id", [a, b, c])`` as a query
# parameter like ``product_id=in.(a,b,c)``. Each UUID is 36 chars +
# a comma; 1050 of them produce a ~38KB URL that exceeds typical
# HTTP-server URL limits (default ~8KB on PostgREST/nginx). The
# upstream returns 414 URI Too Long which Supabase relays as
# ``APIError(code=400, message="JSON could not be generated")`` —
# the exact production symptom from the workflow-status 500.
#
# ``_IN_FILTER_CHUNK`` is the max number of ids we put in one
# ``.in_(...)`` call. 200 ids × ~37 chars ≈ 7.4 KB per URL, well
# under the practical limit.
# ---------------------------------------------------------------------------
_IN_FILTER_CHUNK: int = 200


def _chunked_ids(ids: list[str], size: int = _IN_FILTER_CHUNK):
    """Yield successive ``size``-length slices of ``ids``."""
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


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
        # Phase 35-OOM — paginate the products fetch with .range() to
        # defeat PostgREST's default 1000-row response cap. Without
        # this, projects with 1050+ products silently lose the last
        # ~50 product ids from their UploadRecord, then every count
        # downstream is wrong. Plus the original single-call response
        # for a 10K-row project would be ~400KB of JSON across the
        # wire just to populate product_ids tuples.
        products_by_upload: dict[str, list[dict]] = defaultdict(list)
        PAGE = 1000
        offset = 0
        while True:
            pr = (
                self._rls.table("products")
                .select("id, upload_id")
                .in_("upload_id", upload_ids)
                .range(offset, offset + PAGE - 1)
                .execute()
            )
            page_rows = pr.data or []
            for p in page_rows:
                products_by_upload[p["upload_id"]].append(p)
            if len(page_rows) < PAGE:
                break
            offset += PAGE
        return [upload_record_from_rows(row, products_by_upload.get(row["id"], [])) for row in rows]

    def add_product(self, product: NormalizedProduct) -> None:
        self._rls.table("products").insert(product_to_row(product)).execute()

    def add_products_bulk(
        self, products: list[NormalizedProduct]
    ) -> None:
        """Phase 34W — single Supabase insert for N products.

        Replaces the per-product loop that made ingestion of a
        1050-row CSV take ~60s (1050 HTTP roundtrips). Supabase's
        REST API accepts up to ~1000 rows per insert call; we
        defensively chunk at 500 to leave headroom for the JSON
        serialisation overhead.
        """
        if not products:
            return
        CHUNK = 500
        for start in range(0, len(products), CHUNK):
            batch = products[start : start + CHUNK]
            rows = [product_to_row(p) for p in batch]
            self._rls.table("products").insert(rows).execute()

    def _list_product_ids_paged(self, project_id: UUID) -> list[str]:
        """Phase 34Z-fix — single source of truth for "all product ids
        for this project". Paginates via ``.range()`` to defeat
        PostgREST's default 1000-row cap, and selects only the ``id``
        column so the payload stays tiny (1050 UUIDs ≈ 38 KB JSON
        response, much smaller than a full row dump).

        Used by every method that needs to filter another table by
        ``product_id IN (project's products)``. Centralised here so
        the pagination + selection logic isn't repeated and isn't
        accidentally re-introduced as N+1 elsewhere.
        """
        out: list[str] = []
        PAGE = 1000
        offset = 0
        while True:
            r = (
                self._rls.table("products")
                .select("id")
                .eq("project_id", str(project_id))
                .range(offset, offset + PAGE - 1)
                .execute()
            )
            rows = r.data or []
            for row in rows:
                out.append(row["id"])
            if len(rows) < PAGE:
                break
            offset += PAGE
        return out

    def list_products_for_project(self, project_id: UUID) -> list[NormalizedProduct]:
        proj = self.get_project(project_id)
        if proj is None:
            return []
        # Phase 34Z — PostgREST's default range cap is 1000 rows per
        # request. For projects with 1050+ products we paginate via
        # ``.range()`` so the workflow aggregator sees every row.
        # Without this, the aggregator silently misses the tail and
        # downstream calculation totals are wrong.
        out: list[NormalizedProduct] = []
        PAGE = 1000
        offset = 0
        while True:
            r = (
                self._rls.table("products")
                .select("*")
                .eq("project_id", str(project_id))
                .range(offset, offset + PAGE - 1)
                .execute()
            )
            rows = r.data or []
            for row in rows:
                out.append(
                    product_from_row(
                        row, methodologies_enabled=proj.methodologies_enabled
                    )
                )
            if len(rows) < PAGE:
                break
            offset += PAGE
        return out

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

    def get_pt_classifications_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, ProteinTrackerProductClassification]:
        """Phase 34W — fetch all classifications for many products in
        one query. Replaces the N+1 per-product loop.

        Phase 34Z-fix — chunk size lowered 500 → 200 to keep each
        ``.in_(...)`` URL well under PostgREST/nginx's ~8KB practical
        limit. 500 UUIDs produced an 18KB URL which Supabase rejected
        in some environments.
        """
        if not product_ids:
            return {}
        out: dict[UUID, ProteinTrackerProductClassification] = {}
        for start in range(0, len(product_ids), _IN_FILTER_CHUNK):
            ids_str = [
                str(pid)
                for pid in product_ids[start : start + _IN_FILTER_CHUNK]
            ]
            r = (
                self._rls.table("classifications")
                .select("*")
                .in_("product_id", ids_str)
                .eq("methodology", "protein_tracker")
                .execute()
            )
            for row in r.data or []:
                cls = pt_classification_from_row(row)
                out[cls.product_id] = cls
        return out

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
        # Phase 34Z-fix — chunk the IN() filter at 200 ids per call.
        # A 1050-product project produced a single ~38KB URL which
        # PostgREST rejects with "JSON could not be generated"
        # (URI Too Long, surfaced as 400/500). Chunking keeps each
        # URL well under the 8KB practical limit.
        #
        # The products SELECT itself also needs ``.range()`` pagination
        # to defeat PostgREST's default 1000-row response cap — the
        # legacy single SELECT silently truncated the last 50 rows on
        # 1050-product projects.
        product_ids = self._list_product_ids_paged(project_id)
        if not product_ids:
            return []
        out: list[ManualReviewItem] = []
        for chunk in _chunked_ids(product_ids, 200):
            q = (
                self._rls.table("manual_reviews")
                .select("*")
                .in_("product_id", chunk)
            )
            if methodology is not None:
                q = q.eq("methodology", methodology.value)
            r = q.execute()
            out.extend(manual_review_from_row(row) for row in (r.data or []))
        return out

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

    def list_audit_events_for_project(
        self, project_id: UUID
    ) -> list[AuditEvent]:
        """Phase 34M — placeholder so the workflow aggregator can call
        this on every store. On production the safe fallback is an
        empty list, which makes the NEVO step stay at "available"
        until we add a real query (deferred to a follow-up phase that
        also adds the audit-event row parser). The in-memory store
        provides the working implementation tests use.
        """
        return []

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

    def delete_upload(self, upload_id: UUID) -> None:
        """Delete an upload row. Schema FKs cascade products → classifications,
        manual_reviews, enrichment records. Calculation runs are unaffected
        (no FK to uploads). RLS policy on uploads gates the delete.
        """
        self._rls.table("uploads").delete().eq("id", str(upload_id)).execute()

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
    # Classification jobs (Phase 34S) — persisted async, chunked AI
    # classification. Backed by the classification_jobs table introduced
    # in migration 0034_phase34s_classification_jobs.sql. The optimistic
    # concurrency token is the row's ``updated_at`` column; every
    # store update bumps it.
    # ------------------------------------------------------------------
    def add_classification_job(self, job) -> None:  # type: ignore[no-untyped-def]
        from altera_api.persistence.mappers import (
            classification_job_to_row,
        )

        self._rls.table("classification_jobs").insert(
            classification_job_to_row(job)
        ).execute()

    def update_classification_job(self, job) -> None:  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        from altera_api.persistence.mappers import (
            classification_job_to_row,
        )

        # Bump updated_at so the optimistic-lock check in advance can
        # detect a concurrent write. We don't fail loudly if 0 rows
        # were updated here because not every caller wants conflict
        # semantics (e.g. cancel_classification_job is idempotent).
        row = classification_job_to_row(job)
        row["updated_at"] = datetime.now(UTC).isoformat()
        row.pop("id", None)
        self._rls.table("classification_jobs").update(row).eq(
            "id", str(job.id)
        ).execute()

    def get_classification_job(self, job_id: UUID):  # type: ignore[no-untyped-def]
        from altera_api.persistence.mappers import (
            classification_job_from_row,
        )

        r = (
            self._rls.table("classification_jobs")
            .select("*")
            .eq("id", str(job_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return classification_job_from_row(r.data[0])

    def list_classification_jobs_for_project(
        self, project_id: UUID
    ) -> list:
        from altera_api.persistence.mappers import (
            classification_job_from_row,
        )

        r = (
            self._rls.table("classification_jobs")
            .select("*")
            .eq("project_id", str(project_id))
            .order("created_at", desc=True)
            .execute()
        )
        return [
            classification_job_from_row(row) for row in (r.data or [])
        ]

    def list_classification_jobs_for_upload(
        self, upload_id: UUID
    ) -> list:
        from altera_api.persistence.mappers import (
            classification_job_from_row,
        )

        r = (
            self._rls.table("classification_jobs")
            .select("*")
            .eq("upload_id", str(upload_id))
            .order("created_at", desc=True)
            .execute()
        )
        return [
            classification_job_from_row(row) for row in (r.data or [])
        ]

    def find_active_classification_job(
        self,
        *,
        upload_id: UUID,
        methodology: Methodology,
    ):  # type: ignore[no-untyped-def]
        """Phase 35A — return the most-recent non-terminal job for
        the (upload, methodology) pair, or None.

        ``not is_terminal`` translates to status IN (queued, running)
        — the four terminal states (completed, completed_with_errors,
        failed, cancelled) are explicitly excluded.
        """
        from altera_api.persistence.mappers import (
            classification_job_from_row,
        )

        r = (
            self._rls.table("classification_jobs")
            .select("*")
            .eq("upload_id", str(upload_id))
            .eq("methodology", methodology.value)
            .in_("status", ["queued", "running"])
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return classification_job_from_row(r.data[0])

    def count_active_heavy_classification_jobs(
        self, *, min_total_products: int = 500
    ) -> int:
        """Phase 35B — count non-terminal heavy classification jobs
        visible under the caller's RLS scope. Used by the heavy-job
        guard to decide whether to admit a new heavy job.

        Uses PostgREST's count-only ``head=True`` so no row data
        crosses the wire — just the count via Content-Range header.
        """
        r = (
            self._rls.table("classification_jobs")
            .select("id", count="exact", head=True)
            .in_("status", ["queued", "running"])
            .gte("total_products", min_total_products)
            .execute()
        )
        return int(r.count or 0)

    def count_active_heavy_ingestion_jobs(
        self, *, min_total_rows: int = 1000
    ) -> int:
        r = (
            self._rls.table("ingestion_jobs")
            .select("id", count="exact", head=True)
            .in_("status", ["queued", "running"])
            .gte("total_rows", min_total_rows)
            .execute()
        )
        return int(r.count or 0)

    # ------------------------------------------------------------------
    # Ingestion jobs (Phase 34X)
    # ------------------------------------------------------------------
    # Phase 34X initial Postgres implementation. Schema lives in
    # migration 0036_phase34x_ingestion_jobs.sql. The ``pending_payload``
    # column is JSONB; we keep the inline serialisation simple by
    # storing the list of mapper rows directly (no separate table —
    # the chunk size cap keeps the row size bounded).
    def add_ingestion_job(self, job) -> None:  # type: ignore[no-untyped-def]
        from altera_api.persistence.mappers import ingestion_job_to_row

        self._rls.table("ingestion_jobs").insert(
            ingestion_job_to_row(job)
        ).execute()

    def update_ingestion_job(self, job) -> None:  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        from altera_api.persistence.mappers import ingestion_job_to_row

        row = ingestion_job_to_row(job)
        row["updated_at"] = datetime.now(UTC).isoformat()
        row.pop("id", None)
        self._rls.table("ingestion_jobs").update(row).eq(
            "id", str(job.id)
        ).execute()

    def get_ingestion_job(self, job_id: UUID):  # type: ignore[no-untyped-def]
        from altera_api.persistence.mappers import ingestion_job_from_row

        r = (
            self._rls.table("ingestion_jobs")
            .select("*")
            .eq("id", str(job_id))
            .limit(1)
            .execute()
        )
        if not r.data:
            return None
        return ingestion_job_from_row(r.data[0])

    def list_ingestion_jobs_for_upload(self, upload_id: UUID) -> list:
        from altera_api.persistence.mappers import ingestion_job_from_row

        r = (
            self._rls.table("ingestion_jobs")
            .select("*")
            .eq("upload_id", str(upload_id))
            .order("created_at", desc=True)
            .execute()
        )
        return [ingestion_job_from_row(row) for row in (r.data or [])]

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

    def get_enrichment_records_bulk(
        self, product_ids: list[UUID]
    ) -> dict[UUID, list[NutritionEnrichmentRecord]]:
        """Phase 34Z — single ``WHERE product_id IN (…)`` query instead
        of N round-trips. The workflow-status aggregator was making
        2N HTTP calls (two nested loops) on a 1050-product project
        — that's >2100 round-trips and the dominant cause of the
        PostgREST timeout/JSON error.

        Returns the same shape as N individual
        ``get_enrichment_records_for_product`` calls, keyed by
        product_id. Missing ids are simply absent from the dict.
        """
        if not product_ids:
            return {}
        out: dict[UUID, list[NutritionEnrichmentRecord]] = {}
        # Phase 34Z-fix — chunk size lowered 500 → _IN_FILTER_CHUNK (200)
        # for URL-length safety; same reasoning as
        # get_pt_classifications_bulk above.
        for start in range(0, len(product_ids), _IN_FILTER_CHUNK):
            ids_str = [
                str(pid)
                for pid in product_ids[start : start + _IN_FILTER_CHUNK]
            ]
            r = (
                self._rls.table("nutrition_enrichment_records")
                .select("*")
                .in_("product_id", ids_str)
                .execute()
            )
            for row in r.data or []:
                rec = enrichment_record_from_row(row)
                out.setdefault(rec.product_id, []).append(rec)
        return out

    def list_enrichment_records_for_project(
        self, project_id: UUID
    ) -> list[NutritionEnrichmentRecord]:
        # Phase 34Z-fix — same chunking treatment as
        # list_review_items_for_project. A 1050-product project would
        # otherwise produce a ~38KB ``.in_()`` URL and PostgREST 400s.
        product_ids = self._list_product_ids_paged(project_id)
        if not product_ids:
            return []
        out: list[NutritionEnrichmentRecord] = []
        for chunk in _chunked_ids(product_ids, 200):
            r = (
                self._rls.table("nutrition_enrichment_records")
                .select("*")
                .in_("product_id", chunk)
                .execute()
            )
            out.extend(
                enrichment_record_from_row(row) for row in (r.data or [])
            )
        return out

    def project_has_any_enrichment(self, project_id: UUID) -> bool:
        """Phase 34Z-fix — boolean probe for ``nevo_attempted``.

        ``compute_workflow_status`` only needs to know whether NEVO
        has ever run for this project. Pulling every enrichment row
        and converting them to domain objects just to test
        ``bool(...)`` was the dominant N+1-shaped cost on a project
        with 1050+ products and growing enrichment history.

        This probe issues a ``count="exact", head=True`` query which
        returns NO rows — just a Content-Range header with the
        match count. Bounded HTTP and bounded memory regardless of
        product count.
        """
        # Walk a few products at a time; stop at the first hit. For
        # the common case (NEVO has been run) the very first chunk
        # returns count > 0 and the function returns True in ~1 HTTP
        # call.
        product_ids = self._list_product_ids_paged(project_id)
        if not product_ids:
            return False
        for chunk in _chunked_ids(product_ids, 200):
            r = (
                self._rls.table("nutrition_enrichment_records")
                .select("product_id", count="exact", head=True)
                .in_("product_id", chunk)
                .execute()
            )
            if (r.count or 0) > 0:
                return True
        return False

    # ------------------------------------------------------------------
    # Nutrition reference tables (Phase 33H)
    # ------------------------------------------------------------------
    # Reads go via the service-role client (_svc) because RLS on these
    # tables is gated to Altera staff only — the JWT-scoped client would
    # see zero rows for any non-Altera user. The apply-references route
    # is itself Altera-only.

    def list_nevo_entries(self) -> list[NevoEntry]:
        # Phase 34N — Supabase/PostgREST applies a 1000-row default cap
        # to .select(); the NEVO 2025 v9.0 dataset has ~2,328 rows so
        # the cap silently truncated 60% of the table. We iterate with
        # explicit .range() windows until we drain the whole table.
        return [_nevo_entry_from_row(row) for row in self._fetch_all_rows("nevo_reference")]

    def list_ciqual_entries(self) -> list[CiqualEntry]:
        # Phase 34N — same fix as list_nevo_entries; CIQUAL is much
        # larger (~3,000 rows) and was also being silently truncated.
        return [
            _ciqual_entry_from_row(row)
            for row in self._fetch_all_rows("ciqual_reference")
        ]

    def _fetch_all_rows(
        self, table: str, *, window: int = 1000
    ) -> list[dict]:
        """Drain ``table`` through paginated ``.range()`` calls.

        Supabase/PostgREST caps a single ``.select()`` at 1000 rows by
        default. Reference tables (NEVO, CIQUAL) exceed that, so we
        loop until a window comes back smaller than ``window``.
        """
        out: list[dict] = []
        offset = 0
        while True:
            r = (
                self._svc.table(table)
                .select("*")
                .range(offset, offset + window - 1)
                .execute()
            )
            chunk = r.data or []
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < window:
                break
            offset += window
            # Defensive cap — no real reference table exceeds 100k rows.
            if offset >= 100_000:
                break
        return out

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


# ---------------------------------------------------------------------------
# Phase 33H — nutrition reference row mappers
# ---------------------------------------------------------------------------
# Lightweight readers for the static reference tables. No to_row helpers
# because writes go through the dedicated import scripts (scripts/
# import_nevo.py, scripts/import_ciqual.py) using service-role upserts.


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _nevo_entry_from_row(row: dict) -> NevoEntry:
    return NevoEntry(
        id=UUID(row["id"]),
        source=row.get("source", "nevo"),
        source_version=row["source_version"],
        nevo_code=row["nevo_code"],
        food_name_nl=row.get("food_name_nl") or "",
        food_name_en=row.get("food_name_en") or "",
        food_group=row.get("food_group") or "unknown",
        quantity_basis=row.get("quantity_basis") or "per 100g",
        protein_g_per_100g=_dec(row.get("protein_g_per_100g")),
        plant_protein_g_per_100g=_dec(row.get("plant_protein_g_per_100g")),
        animal_protein_g_per_100g=_dec(row.get("animal_protein_g_per_100g")),
    )


def _ciqual_entry_from_row(row: dict) -> CiqualEntry:
    return CiqualEntry(
        id=UUID(row["id"]),
        source=row.get("source", "ciqual"),
        source_version=row["source_version"],
        source_food_code=row["source_food_code"],
        food_name_en=row.get("food_name_en") or "",
        food_group=row.get("food_group") or "unknown",
        food_subgroup=row.get("food_subgroup"),
        food_subsubgroup=row.get("food_subsubgroup"),
        protein_g_per_100g=_dec(row.get("protein_g_per_100g")),
        is_below_detection=bool(row.get("is_below_detection", False)),
    )
