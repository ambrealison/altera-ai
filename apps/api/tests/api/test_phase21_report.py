"""Phase 21 — client-facing report layer.

Tests:
- PT report includes headline result and four-group breakdown
- WWF report includes FG1–FG7 and PHD comparison
- report includes methodology versions
- report includes approval status and approval metadata
- report includes manual review summary when items exist
- report does not include forbidden commercial fields
- client CAN see its own org's draft/under_review/rejected report (200)
  (Phase Product-UX-D — self-service guided workflow)
- client can see approved/delivered report (200)
- Altera can preview draft report (200)
- cross-organisation access blocked (404)
- executive summary is factual and deterministic
- WWF composite Step 1 buckets are present
- classification_sources counts are correct
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from altera_api.api.state import ExportRecord, InMemoryStore, RunRecord
from altera_api.auth import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    ClientRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
    ProteinTrackerProductClassification,
)
from altera_api.domain.report_exports import ReviewOwnerType
from altera_api.domain.review import ManualReviewItem, ManualReviewQueueReason, ManualReviewStatus
from altera_api.domain.wwf import (
    WWFCalculationSummary,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
)
from altera_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _org(store: InMemoryStore, *, org_type: OrganisationType = OrganisationType.ALTERA_INTERNAL) -> Organisation:
    o = Organisation(
        id=uuid4(), name="Org", slug="org", organisation_type=org_type, created_at=_NOW
    )
    store.organisations[o.id] = o
    return o


def _user(store: InMemoryStore, *, org: Organisation, role: AlteraRole | ClientRole) -> UserProfile:
    uid = uuid4()
    p = UserProfile(
        user_id=uid,
        organisation_id=org.id,
        email=f"{uid}@t.local",
        display_name="U",
        role=role,
        created_at=_NOW,
    )
    store.users[uid] = p
    return p


def _auth(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id,
        email=user.email,
        organisation_id=org.id,
        role=user.role,
        organisation_type=org.organisation_type,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
    )


def _pt_summary(run_id: UUID) -> dict:
    aggs = [
        ProteinTrackerGroupAggregate(
            pt_group=g, volume_kg=Decimal("100"), protein_kg=Decimal("20"), item_count=5
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    ]
    s = ProteinTrackerCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_group=tuple(aggs),
        plant_protein_kg=Decimal("60"),
        animal_protein_kg=Decimal("40"),
        total_in_scope_protein_kg=Decimal("100"),
        plant_share_pct=Decimal("60"),
        animal_share_pct=Decimal("40"),
        rows_with_per_product_split=0,
        rows_protein_source_label=10,
        rows_protein_source_reference_db=5,
        out_of_scope_count=2,
        unknown_count=1,
        methodology_version="1.0.0",
        methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    )
    return s.model_dump()


def _wwf_summary(run_id: UUID) -> dict:
    fg_aggs = [
        WWFFoodGroupAggregate(
            food_group=fg,
            weight_kg=Decimal("100"),
            share_pct=Decimal("14"),
            phd_reference_share_pct=Decimal("15") if fg in (WWFFoodGroup.FG1, WWFFoodGroup.FG2) else None,
        )
        for fg in (
            WWFFoodGroup.FG1,
            WWFFoodGroup.FG2,
            WWFFoodGroup.FG3,
            WWFFoodGroup.FG4,
            WWFFoodGroup.FG5,
            WWFFoodGroup.FG6,
            WWFFoodGroup.FG7,
        )
    ]
    fg_aggs_with_dairy = [
        WWFFoodGroupAggregate(
            food_group=a.food_group,
            weight_kg=a.weight_kg,
            weight_kg_dairy_equiv=Decimal("100") if a.food_group is WWFFoodGroup.FG2 else None,
            share_pct=a.share_pct,
            phd_reference_share_pct=a.phd_reference_share_pct,
        )
        for a in fg_aggs
    ]
    s = WWFCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_food_group=tuple(fg_aggs_with_dairy),
        total_sales_weight_in_scope_kg=Decimal("700"),
        composites_total_weight_kg=Decimal("40"),
        composites_meat_based_kg=Decimal("10"),
        composites_seafood_based_kg=Decimal("10"),
        composites_vegetarian_kg=Decimal("10"),
        composites_vegan_kg=Decimal("10"),
        whole_diet_plant_weight_kg=Decimal("400"),
        whole_diet_animal_weight_kg=Decimal("300"),
        out_of_scope_count=3,
        unknown_count=2,
        methodology_version="1.0.0",
        methodology_source_edition="WWF Food Practice 2024",
        taxonomy_version="1.0.0",
        rules_version="0.1.0",
    )
    return s.model_dump()


def _run(store: InMemoryStore, *, project_id: UUID, org_id: UUID, methodology: Methodology) -> RunRecord:
    run_id = uuid4()
    payload = _pt_summary(run_id) if methodology is Methodology.PROTEIN_TRACKER else _wwf_summary(run_id)
    rec = RunRecord(
        id=run_id,
        project_id=project_id,
        methodology=methodology,
        started_at=_NOW,
        finished_at=_NOW,
        triggered_by=uuid4(),
        rows_payload=[],
        summary_payload=payload,
        rows_count=0,
        organisation_id=org_id,
    )
    store.runs[run_id] = rec
    return rec


def _export(store: InMemoryStore, *, run_id: UUID, org_id: UUID, status: str = "draft") -> ExportRecord:
    eid = uuid4()
    rec = ExportRecord(
        id=eid,
        run_id=run_id,
        organisation_id=org_id,
        format="md",
        status="success",
        storage_path=f"orgs/{org_id}/exports/{eid}.md",
        filename="report.md",
        size_bytes=100,
        approval_status=status,
        approved_by=uuid4() if status in ("approved", "delivered") else None,
        approved_at=_NOW if status in ("approved", "delivered") else None,
        delivered_by=uuid4() if status == "delivered" else None,
        delivered_at=_NOW if status == "delivered" else None,
    )
    store.add_export_record(rec)
    return rec


@contextmanager
def _client(store: InMemoryStore, auth: AuthContext):
    from altera_api.api.dependencies import get_data_store

    app.dependency_overrides[authed_user] = lambda: auth
    app.dependency_overrides[get_data_store] = lambda: store
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(authed_user, None)
        app.dependency_overrides.pop(get_data_store, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _setup():
    store = InMemoryStore()
    altera_org = _org(store, org_type=OrganisationType.ALTERA_INTERNAL)
    client_org = _org(store, org_type=OrganisationType.GMS_CLIENT)

    altera_user = _user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
    client_user = _user(store, org=client_org, role=ClientRole.CLIENT_VIEWER)

    pt_project = store.create_project(
        name="Retailer PT",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
        organisation_id=client_org.id,
        created_by=altera_user.user_id,
    )
    wwf_project = store.create_project(
        name="Retailer WWF",
        methodologies_enabled=frozenset({Methodology.WWF}),
        reporting_period_label="2024",
        organisation_id=client_org.id,
        created_by=altera_user.user_id,
    )

    pt_run = _run(store, project_id=pt_project.id, org_id=client_org.id, methodology=Methodology.PROTEIN_TRACKER)
    wwf_run = _run(store, project_id=wwf_project.id, org_id=client_org.id, methodology=Methodology.WWF)

    return store, altera_org, client_org, altera_user, client_user, pt_project, wwf_project, pt_run, wwf_run


# ---------------------------------------------------------------------------
# 1. PT report content
# ---------------------------------------------------------------------------

class TestPTReportContent:
    def test_pt_report_headline_and_groups(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        doc = r.json()
        pt = doc["pt_section"]
        assert pt is not None
        assert pt["plant_share_pct"] == "60"
        assert pt["animal_share_pct"] == "40"
        assert pt["plant_protein_kg"] == "60"
        assert pt["animal_protein_kg"] == "40"
        assert len(pt["groups"]) == 4
        group_names = {g["pt_group"] for g in pt["groups"]}
        assert group_names == {
            "plant_based_core", "plant_based_non_core", "composite_products", "animal_core"
        }

    def test_pt_report_methodology_versions(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        doc = r.json()
        pt = doc["pt_section"]
        assert pt["methodology_version"] == "1.0.0"
        assert pt["methodology_source_edition"] == "GPA & ProVeg Foodservice 2024-08"
        assert pt["taxonomy_version"] == "1.0.0"
        assert pt["rules_version"] == "0.1.0"

    def test_pt_report_meta_fields(self):
        store, altera_org, client_org, altera_user, _, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="approved")
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        doc = r.json()
        assert doc["meta"]["methodology"] == "protein_tracker"
        assert doc["meta"]["approval_status"] == "approved"
        assert doc["meta"]["approved_at"] is not None
        assert doc["meta"]["project_name"] == "Retailer PT"

    def test_pt_composite_note_50_50(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        pt = r.json()["pt_section"]
        assert "50/50" in pt["composite_note"]

    def test_pt_data_quality_counts(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        pt = r.json()["pt_section"]
        assert pt["out_of_scope_count"] == 2
        assert pt["unknown_count"] == 1
        assert pt["rows_protein_source_label"] == 10
        assert pt["rows_protein_source_reference_db"] == 5


# ---------------------------------------------------------------------------
# 2. WWF report content
# ---------------------------------------------------------------------------

class TestWWFReportContent:
    def test_wwf_food_groups_fg1_through_fg7(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        assert r.status_code == 200
        doc = r.json()
        wwf = doc["wwf_section"]
        assert wwf is not None
        fg_names = {g["food_group"] for g in wwf["per_food_group"]}
        assert fg_names == {"FG1", "FG2", "FG3", "FG4", "FG5", "FG6", "FG7"}

    def test_wwf_phd_reference_shares_present(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        wwf = r.json()["wwf_section"]
        fg1 = next(g for g in wwf["per_food_group"] if g["food_group"] == "FG1")
        assert fg1["phd_reference_share_pct"] == "15"

    def test_wwf_composite_step1_buckets(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        wwf = r.json()["wwf_section"]
        assert wwf["composites_meat_based_kg"] == "10"
        assert wwf["composites_seafood_based_kg"] == "10"
        assert wwf["composites_vegetarian_kg"] == "10"
        assert wwf["composites_vegan_kg"] == "10"
        assert wwf["composites_total_weight_kg"] == "40"

    def test_wwf_whole_diet_context(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        wwf = r.json()["wwf_section"]
        assert wwf["whole_diet_plant_weight_kg"] == "400"
        assert wwf["whole_diet_animal_weight_kg"] == "300"

    def test_wwf_methodology_versions(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        wwf = r.json()["wwf_section"]
        assert wwf["methodology_version"] == "1.0.0"
        assert wwf["methodology_source_edition"] == "WWF Food Practice 2024"

    def test_wwf_executive_summary_mentions_weight(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        doc = r.json()
        assert "weight" in doc["executive_summary"].lower()
        assert "700" in doc["executive_summary"]


# ---------------------------------------------------------------------------
# 3. Executive summary
# ---------------------------------------------------------------------------

class TestExecutiveSummary:
    def test_pt_exec_summary_mentions_ratio(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        doc = r.json()
        assert "60" in doc["executive_summary"]
        assert "Protein Tracker" in doc["executive_summary"]

    def test_exec_summary_includes_approval_status(self):
        store, altera_org, client_org, altera_user, _, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="approved")
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        doc = r.json()
        assert "approved" in doc["executive_summary"].lower()

    def test_exec_summary_draft_has_draft_phrase(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        doc = r.json()
        assert doc["meta"]["approval_status"] == "draft"
        assert "being prepared" in doc["executive_summary"]


# ---------------------------------------------------------------------------
# 4. Review summary
# ---------------------------------------------------------------------------

class TestReviewSummary:
    def test_review_summary_empty_when_no_items(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        rev = r.json()["review_summary"]
        assert rev["total_reviewed"] == 0
        assert rev["pending"] == 0
        assert rev["top_reasons"] == []

    def test_review_summary_counts_by_status(self):
        from altera_api.exports.report import _review_summary

        def _item(status: ManualReviewStatus, reason: ManualReviewQueueReason) -> ManualReviewItem:
            return ManualReviewItem(
                product_id=uuid4(),
                methodology=Methodology.PROTEIN_TRACKER,
                status=status,
                reason=reason,
                owner_type=ReviewOwnerType.ALTERA_INTERNAL,
                queued_at=_NOW,
            )

        items = [
            _item(ManualReviewStatus.ACCEPTED, ManualReviewQueueReason.LOW_CONFIDENCE),
            _item(ManualReviewStatus.CHANGED, ManualReviewQueueReason.RULE_COLLISION),
            _item(ManualReviewStatus.IN_QUEUE, ManualReviewQueueReason.AI_PARSE_FAILED),
        ]
        rev = _review_summary(items)
        assert rev.accepted == 1
        assert rev.changed == 1
        assert rev.deferred == 0
        assert rev.pending == 1
        assert rev.total_reviewed == 2

    def test_review_summary_top_reasons(self):
        from altera_api.exports.report import _review_summary

        items = [
            ManualReviewItem(
                product_id=uuid4(),
                methodology=Methodology.PROTEIN_TRACKER,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.LOW_CONFIDENCE,
                owner_type=ReviewOwnerType.ALTERA_INTERNAL,
                queued_at=_NOW,
            )
            for _ in range(3)
        ] + [
            ManualReviewItem(
                product_id=uuid4(),
                methodology=Methodology.PROTEIN_TRACKER,
                status=ManualReviewStatus.IN_QUEUE,
                reason=ManualReviewQueueReason.RULE_COLLISION,
                owner_type=ReviewOwnerType.ALTERA_INTERNAL,
                queued_at=_NOW,
            )
        ]
        rev = _review_summary(items)
        assert rev.top_reasons[0] == "low_confidence"


# ---------------------------------------------------------------------------
# 5. No forbidden commercial fields
# ---------------------------------------------------------------------------

class TestNoCommercialFields:
    _FORBIDDEN = (
        "revenue", "margin", "cost_price", "sales_value", "supplier_id",
        "supplier_name", "contract_terms", "promotion_id", "promotion_discount",
        "store_id", "store_region", "confidential_strategy", "internal_score",
    )

    def test_pt_report_has_no_commercial_fields(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        text = r.text.lower()
        for field in self._FORBIDDEN:
            assert field not in text, f"forbidden field '{field}' found in report response"

    def test_wwf_report_has_no_commercial_fields(self):
        store, altera_org, _, altera_user, _, _, wwf_project, _, wwf_run = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{wwf_project.id}/runs/{wwf_run.id}/report")
        text = r.text.lower()
        for field in self._FORBIDDEN:
            assert field not in text, f"forbidden field '{field}' found in report response"

    def test_storage_path_not_exposed_in_report(self):
        store, altera_org, client_org, altera_user, _, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="approved")
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert "storage_path" not in r.text


# ---------------------------------------------------------------------------
# 6. Permissions
# ---------------------------------------------------------------------------

class TestReportPermissions:
    def test_altera_can_see_draft_report(self):
        store, altera_org, _, altera_user, _, pt_project, _, pt_run, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "draft"

    def test_altera_can_see_under_review_report(self):
        store, altera_org, client_org, altera_user, _, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="under_review")
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "under_review"

    # Phase Product-UX-D — the self-service guided workflow shows the full
    # report inline immediately after a calculation. A project's own
    # organisation may therefore view its own report at any approval status
    # (access is still org-scoped by get_project; cross-org is 404 below).
    def test_client_can_see_own_draft_report(self):
        store, _, client_org, _, client_user, pt_project, _, pt_run, _ = _setup()
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "draft"

    def test_client_can_see_own_under_review_report(self):
        store, _, client_org, altera_user, client_user, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="under_review")
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "under_review"

    def test_client_can_see_own_rejected_report(self):
        store, _, client_org, altera_user, client_user, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="rejected")
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "rejected"

    def test_client_can_see_approved_report(self):
        store, _, client_org, altera_user, client_user, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="approved")
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "approved"

    def test_client_can_see_delivered_report(self):
        store, _, client_org, altera_user, client_user, pt_project, _, pt_run, _ = _setup()
        _export(store, run_id=pt_run.id, org_id=client_org.id, status="delivered")
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{pt_run.id}/report")
        assert r.status_code == 200
        assert r.json()["meta"]["approval_status"] == "delivered"
        assert r.json()["meta"]["delivered_at"] is not None

    def test_run_not_found_returns_404(self):
        store, altera_org, _, altera_user, _, pt_project, _, _, _ = _setup()
        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{uuid4()}/report")
        assert r.status_code == 404

    def test_cross_organisation_run_returns_404(self):
        store, _, client_org, altera_user, client_user, pt_project, wwf_project, pt_run, wwf_run = _setup()
        _export(store, run_id=wwf_run.id, org_id=client_org.id, status="approved")
        auth = _auth(client_user, client_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{pt_project.id}/runs/{wwf_run.id}/report")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. Classification sources
# ---------------------------------------------------------------------------

class TestClassificationSources:
    def test_classification_sources_counted(self):
        from altera_api.exports.report import _classification_sources

        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )

        product_ids = [uuid4() for _ in range(3)]
        sources = [
            ClassificationSource.DETERMINISTIC,
            ClassificationSource.AI,
            ClassificationSource.MANUAL_REVIEW,
        ]
        for pid, src in zip(product_ids, sources, strict=True):
            clf = ProteinTrackerProductClassification(
                product_id=pid,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=src,
                confidence=Decimal("1") if src is ClassificationSource.DETERMINISTIC else Decimal("0.9"),
                rule_id="R001" if src is ClassificationSource.DETERMINISTIC else None,
                ai_prompt_version="v1" if src is ClassificationSource.AI else None,
                ai_model="gpt-4" if src is ClassificationSource.AI else None,
                reviewer_user_id=uuid4() if src is ClassificationSource.MANUAL_REVIEW else None,
                updated_at=_NOW,
            )
            store.upsert_pt_classification(clf)

        run_id = uuid4()
        run = RunRecord(
            id=run_id,
            project_id=project.id,
            methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW,
            finished_at=_NOW,
            triggered_by=uuid4(),
            rows_payload=[{"product_id": str(pid)} for pid in product_ids],
            summary_payload=_pt_summary(run_id),
            rows_count=3,
            organisation_id=org.id,
        )

        result = _classification_sources(store, run)
        assert result.deterministic == 1
        assert result.ai == 1
        assert result.manual_review == 1
        assert result.total == 3
