"""Phase 22 — data coverage and uncertainty engine tests.

Tests:
- coverage counts from upload/classification/review data
- percentages calculated correctly
- high uncertainty when many unknowns remain
- medium uncertainty when missing data or AI share is high
- low uncertainty for clean fixture
- PT caveats include composite 50/50 when composites exist
- WWF caveats include weight-based methodology and dairy equivalents
- report document includes coverage section
- no forbidden commercial fields appear
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore, RunRecord
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
from altera_api.domain.validation import ValidationError, ValidationReport, ValidationWarning
from altera_api.domain.wwf import (
    WWFCalculationSummary,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
)
from altera_api.main import app

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Shared helpers (same pattern as Phase 21 tests)
# ---------------------------------------------------------------------------

def _org(store: InMemoryStore, *, org_type: OrganisationType = OrganisationType.ALTERA_INTERNAL) -> Organisation:
    o = Organisation(id=uuid4(), name="Org", slug="org", organisation_type=org_type, created_at=_NOW)
    store.organisations[o.id] = o
    return o


def _user(store: InMemoryStore, *, org: Organisation, role: AlteraRole | ClientRole) -> UserProfile:
    uid = uuid4()
    p = UserProfile(
        user_id=uid, organisation_id=org.id, email=f"{uid}@t.local",
        display_name="U", role=role, created_at=_NOW,
    )
    store.users[uid] = p
    return p


def _auth(user: UserProfile, org: Organisation) -> AuthContext:
    return AuthContext(
        user_id=user.user_id, email=user.email, organisation_id=org.id,
        role=user.role, organisation_type=org.organisation_type,
        auth_provider=AuthProvider.DEV, is_dev_auth=True,
    )


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


def _pt_summary(run_id: UUID, *, composite_items: int = 5, per_product_split: int = 0) -> dict:
    aggs = [
        ProteinTrackerGroupAggregate(
            pt_group=g,
            volume_kg=Decimal("100"),
            protein_kg=Decimal("20"),
            item_count=composite_items if g is ProteinTrackerGroup.COMPOSITE_PRODUCTS else 5,
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
        rows_with_per_product_split=per_product_split,
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
            weight_kg_dairy_equiv=Decimal("100") if fg is WWFFoodGroup.FG2 else None,
            share_pct=Decimal("14"),
            phd_reference_share_pct=Decimal("15") if fg in (WWFFoodGroup.FG1, WWFFoodGroup.FG2) else None,
        )
        for fg in (
            WWFFoodGroup.FG1, WWFFoodGroup.FG2, WWFFoodGroup.FG3,
            WWFFoodGroup.FG4, WWFFoodGroup.FG5, WWFFoodGroup.FG6, WWFFoodGroup.FG7,
        )
    ]
    s = WWFCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_food_group=tuple(fg_aggs),
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


def _make_pt_rows(
    pids: list[UUID],
    *,
    groups: list[str] | None = None,
) -> list[dict]:
    """Minimal row dicts sufficient for coverage counting."""
    default_groups = ["plant_based_core"] * len(pids)
    gs = groups or default_groups
    return [
        {"product_id": str(pid), "pt_group": g, "in_scope": g not in ("unknown", "out_of_scope")}
        for pid, g in zip(pids, gs, strict=True)
    ]


def _make_wwf_rows(pids: list[UUID], *, groups: list[str] | None = None) -> list[dict]:
    default_groups = ["FG1"] * len(pids)
    gs = groups or default_groups
    return [
        {"product_id": str(pid), "wwf_food_group": g, "in_scope": g not in ("unknown", "out_of_scope")}
        for pid, g in zip(pids, gs, strict=True)
    ]


def _run(
    store: InMemoryStore,
    *,
    project_id: UUID,
    org_id: UUID,
    methodology: Methodology,
    rows_payload: list[dict] | None = None,
) -> RunRecord:
    run_id = uuid4()
    if rows_payload is None:
        rows_payload = []
    payload = _pt_summary(run_id) if methodology is Methodology.PROTEIN_TRACKER else _wwf_summary(run_id)
    rec = RunRecord(
        id=run_id, project_id=project_id, methodology=methodology,
        started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
        rows_payload=rows_payload,
        summary_payload=payload,
        rows_count=len(rows_payload),
        organisation_id=org_id,
    )
    store.runs[run_id] = rec
    return rec


def _setup_basic():
    """One Altera org + one client org + analyst user."""
    store = InMemoryStore()
    altera_org = _org(store, org_type=OrganisationType.ALTERA_INTERNAL)
    client_org = _org(store, org_type=OrganisationType.GMS_CLIENT)
    altera_user = _user(store, org=altera_org, role=AlteraRole.ALTERA_ANALYST)
    return store, altera_org, client_org, altera_user


# ---------------------------------------------------------------------------
# 1. Build coverage directly
# ---------------------------------------------------------------------------

class TestCoverageBuilderCounts:
    def test_product_group_counts_from_rows_payload(self):
        from altera_api.exports.coverage import build_coverage_section

        store, altera_org, client_org, _ = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        pids = [uuid4() for _ in range(5)]
        rows = _make_pt_rows(
            pids,
            groups=["plant_based_core", "animal_core", "unknown", "out_of_scope", "composite_products"],
        )
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=5,
            organisation_id=client_org.id,
        )

        cov = build_coverage_section(store, run, project)
        assert cov.products_total == 5
        assert cov.products_unknown == 1
        assert cov.products_out_of_scope == 1
        assert cov.products_classified == 3  # plant_based_core + animal_core + composite

    def test_classification_source_counts(self):
        from altera_api.exports.coverage import build_coverage_section

        store, _, client_org, _ = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        pids = [uuid4() for _ in range(3)]
        rows = _make_pt_rows(pids)

        for pid, src in zip(pids, [ClassificationSource.DETERMINISTIC, ClassificationSource.AI, ClassificationSource.MANUAL_REVIEW], strict=True):
            store.upsert_pt_classification(ProteinTrackerProductClassification(
                product_id=pid,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=src,
                confidence=Decimal("1") if src is ClassificationSource.DETERMINISTIC else Decimal("0.8"),
                rule_id="R001" if src is ClassificationSource.DETERMINISTIC else None,
                ai_prompt_version="v1" if src is ClassificationSource.AI else None,
                ai_model="gpt-4" if src is ClassificationSource.AI else None,
                reviewer_user_id=uuid4() if src is ClassificationSource.MANUAL_REVIEW else None,
                updated_at=_NOW,
            ))

        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=3,
            organisation_id=client_org.id,
        )
        cov = build_coverage_section(store, run, project)
        assert cov.products_rule_classified == 1
        assert cov.products_ai_classified == 1
        assert cov.products_manual_classified == 1

    def test_review_queue_counts(self):
        from altera_api.exports.coverage import build_coverage_section

        store, _, client_org, _ = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        pid1, pid2, pid3 = uuid4(), uuid4(), uuid4()

        # Set up products in store so the review lookup links them to the project
        for pid in (pid1, pid2, pid3):
            from altera_api.domain.product import NormalizedProduct, PTProductFields
            store.add_product(NormalizedProduct(
                id=pid, upload_id=uuid4(), project_id=project.id,
                organisation_id=client_org.id, row_number=1,
                external_product_id=str(pid), product_name="Product",
                weight_per_item_kg=Decimal("1"),
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                pt_fields=PTProductFields(items_purchased=Decimal("100"), protein_pct=Decimal("10")),
                created_at=_NOW,
            ))

        store.upsert_review_item(ManualReviewItem(
            product_id=pid1, methodology=Methodology.PROTEIN_TRACKER,
            status=ManualReviewStatus.ACCEPTED, reason=ManualReviewQueueReason.LOW_CONFIDENCE,
            owner_type=ReviewOwnerType.ALTERA_INTERNAL, queued_at=_NOW,
        ))
        store.upsert_review_item(ManualReviewItem(
            product_id=pid2, methodology=Methodology.PROTEIN_TRACKER,
            status=ManualReviewStatus.IN_QUEUE, reason=ManualReviewQueueReason.AI_PARSE_FAILED,
            owner_type=ReviewOwnerType.ALTERA_INTERNAL, queued_at=_NOW,
        ))
        store.upsert_review_item(ManualReviewItem(
            product_id=pid3, methodology=Methodology.PROTEIN_TRACKER,
            status=ManualReviewStatus.CHANGED, reason=ManualReviewQueueReason.RULE_COLLISION,
            owner_type=ReviewOwnerType.ALTERA_INTERNAL, queued_at=_NOW,
        ))

        run_id = uuid4()
        rows = _make_pt_rows([pid1, pid2, pid3])
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=3,
            organisation_id=client_org.id,
        )
        cov = build_coverage_section(store, run, project)
        assert cov.products_sent_to_review == 3
        assert cov.products_reviewed_by_altera == 2  # accepted + changed
        assert "1 still pending" in cov.review_completion_note

    def test_validation_report_counts(self):
        from altera_api.domain.upload import Upload, UploadStatus
        from altera_api.exports.coverage import build_coverage_section

        store, _, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        upload = Upload(
            id=uuid4(), project_id=project.id, organisation_id=client_org.id,
            storage_path="uploads/data.csv", original_filename="data.csv",
            content_type="text/csv", status=UploadStatus.INGESTION_COMPLETED,
            uploaded_by=altera_user.user_id, created_at=_NOW, row_count=100,
        )
        vr = ValidationReport(
            upload_id=upload.id,
            total_rows=100,
            errors=(
                ValidationError(row_number=1, code="E001", message="bad row"),
                ValidationError(row_number=2, code="E001", message="bad row"),
            ),
            warnings=(
                ValidationWarning(row_number=3, code="W001", message="warn"),
            ),
        )
        store.add_upload(upload, [])
        store.set_upload_validation_report(upload.id, vr)

        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=[], summary_payload=_pt_summary(run_id), rows_count=0,
            organisation_id=client_org.id,
        )
        cov = build_coverage_section(store, run, project)
        assert cov.uploaded_rows == 100
        # 2 rows with errors → 98 valid
        assert cov.valid_rows == 98
        assert cov.invalid_rows == 2
        assert cov.error_count == 2
        assert cov.warning_count == 1

    def test_percentages_calculated_correctly(self):
        from altera_api.exports.coverage import build_coverage_section

        store, _, client_org, _ = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        pids = [uuid4() for _ in range(10)]
        # 8 classified, 1 unknown, 1 out_of_scope
        groups = (
            ["plant_based_core"] * 8
            + ["unknown"]
            + ["out_of_scope"]
        )
        rows = _make_pt_rows(pids, groups=groups)
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=10,
            organisation_id=client_org.id,
        )
        cov = build_coverage_section(store, run, project)
        assert cov.classified_product_share_pct == "80.00"
        assert cov.unknown_product_share_pct == "10.00"

    def test_no_rows_returns_none_percentages(self):
        from altera_api.exports.coverage import build_coverage_section

        store, _, client_org, _ = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
        )
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=[], summary_payload=_pt_summary(run_id), rows_count=0,
            organisation_id=client_org.id,
        )
        cov = build_coverage_section(store, run, project)
        assert cov.products_total == 0
        assert cov.classified_product_share_pct is None


# ---------------------------------------------------------------------------
# 2. Uncertainty labels
# ---------------------------------------------------------------------------

class TestUncertaintyLabels:
    def _run_uncertainty(self, **kwargs):
        from altera_api.exports.coverage import _compute_uncertainty
        return _compute_uncertainty(**kwargs)

    def test_high_uncertainty_many_unknowns(self):
        level, rationale = self._run_uncertainty(
            unknown_pct=Decimal("15"),
            pending_count=0,
            products_total=100,
            ai_pct=None,
            missing_protein_pct=None,
            missing_weight_pct=None,
            error_count=0,
        )
        assert level == "high"
        assert "15" in rationale

    def test_high_uncertainty_blocking_errors(self):
        level, _ = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=0,
            products_total=100,
            ai_pct=None,
            missing_protein_pct=None,
            missing_weight_pct=None,
            error_count=5,
        )
        assert level == "high"

    def test_high_uncertainty_many_pending_reviews(self):
        # 6 pending out of 100 products = 6% >= 5% threshold
        level, _ = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=6,
            products_total=100,
            ai_pct=None,
            missing_protein_pct=None,
            missing_weight_pct=None,
            error_count=0,
        )
        assert level == "high"

    def test_medium_uncertainty_high_ai_share(self):
        level, rationale = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=0,
            products_total=100,
            ai_pct=Decimal("35"),
            missing_protein_pct=None,
            missing_weight_pct=None,
            error_count=0,
        )
        assert level == "medium"
        assert "35" in rationale

    def test_medium_uncertainty_missing_protein(self):
        level, rationale = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=0,
            products_total=100,
            ai_pct=None,
            missing_protein_pct=Decimal("12"),
            missing_weight_pct=None,
            error_count=0,
        )
        assert level == "medium"
        assert "12" in rationale

    def test_medium_uncertainty_pending_below_high_threshold(self):
        # 3 pending out of 100 = 3% < 5% → medium, not high
        level, _ = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=3,
            products_total=100,
            ai_pct=None,
            missing_protein_pct=None,
            missing_weight_pct=None,
            error_count=0,
        )
        assert level == "medium"

    def test_low_uncertainty_clean_data(self):
        level, rationale = self._run_uncertainty(
            unknown_pct=Decimal("0"),
            pending_count=0,
            products_total=100,
            ai_pct=Decimal("5"),
            missing_protein_pct=Decimal("0"),
            missing_weight_pct=Decimal("0"),
            error_count=0,
        )
        assert level == "low"
        assert "deterministically" in rationale


# ---------------------------------------------------------------------------
# 3. PT caveats
# ---------------------------------------------------------------------------

class TestPTCaveats:
    def test_composite_50_50_note_when_composites_exist(self):
        from altera_api.exports.coverage import _pt_caveats

        run_id = uuid4()
        s = ProteinTrackerCalculationSummary.model_validate(_pt_summary(run_id, composite_items=8))
        caveats = _pt_caveats(s, products_with_missing_protein=0)
        assert any("50/50" in c for c in caveats)
        assert any("composite" in c.lower() for c in caveats)

    def test_per_product_split_disclosed(self):
        from altera_api.exports.coverage import _pt_caveats

        run_id = uuid4()
        s = ProteinTrackerCalculationSummary.model_validate(
            _pt_summary(run_id, composite_items=10, per_product_split=3)
        )
        caveats = _pt_caveats(s, products_with_missing_protein=0)
        assert any("Per-product" in c for c in caveats)
        assert any("3" in c for c in caveats)

    def test_missing_protein_caveat(self):
        from altera_api.exports.coverage import _pt_caveats

        run_id = uuid4()
        s = ProteinTrackerCalculationSummary.model_validate(_pt_summary(run_id))
        caveats = _pt_caveats(s, products_with_missing_protein=4)
        assert any("4 product" in c for c in caveats)
        assert any("reference database" in c for c in caveats)

    def test_no_caveats_when_no_composites_and_complete_data(self):
        from altera_api.exports.coverage import _pt_caveats

        run_id = uuid4()
        s = ProteinTrackerCalculationSummary.model_validate(_pt_summary(run_id, composite_items=0))
        caveats = _pt_caveats(s, products_with_missing_protein=0)
        assert caveats == []


# ---------------------------------------------------------------------------
# 4. WWF caveats
# ---------------------------------------------------------------------------

class TestWWFCaveats:
    def test_weight_based_methodology_caveat_always_present(self):
        from altera_api.exports.coverage import _wwf_caveats

        run_id = uuid4()
        s = WWFCalculationSummary.model_validate(_wwf_summary(run_id))
        caveats = _wwf_caveats(s)
        assert any("weight" in c.lower() for c in caveats)
        assert any("protein" in c.lower() for c in caveats)

    def test_dairy_equivalent_caveat_present(self):
        from altera_api.exports.coverage import _wwf_caveats

        run_id = uuid4()
        s = WWFCalculationSummary.model_validate(_wwf_summary(run_id))
        caveats = _wwf_caveats(s)
        assert any("dairy" in c.lower() for c in caveats)
        assert any("×10" in c for c in caveats)

    def test_composite_caveat_present_when_composites_exist(self):
        from altera_api.exports.coverage import _wwf_caveats

        run_id = uuid4()
        s = WWFCalculationSummary.model_validate(_wwf_summary(run_id))
        assert s.composites_total_weight_kg > 0
        caveats = _wwf_caveats(s)
        assert any("Composite" in c or "composite" in c.lower() for c in caveats)

    def test_no_composite_caveat_when_no_composites(self):
        from altera_api.exports.coverage import _wwf_caveats

        fg_aggs = [
            WWFFoodGroupAggregate(
                food_group=fg,
                weight_kg=Decimal("100"),
                weight_kg_dairy_equiv=Decimal("100") if fg is WWFFoodGroup.FG2 else None,
                share_pct=Decimal("14"),
                phd_reference_share_pct=None,
            )
            for fg in (
                WWFFoodGroup.FG1, WWFFoodGroup.FG2, WWFFoodGroup.FG3,
                WWFFoodGroup.FG4, WWFFoodGroup.FG5, WWFFoodGroup.FG6, WWFFoodGroup.FG7,
            )
        ]
        s = WWFCalculationSummary(
            run_id=uuid4(),
            reporting_period_label="2024",
            per_food_group=tuple(fg_aggs),
            total_sales_weight_in_scope_kg=Decimal("700"),
            composites_total_weight_kg=Decimal("0"),
            composites_meat_based_kg=Decimal("0"),
            composites_seafood_based_kg=Decimal("0"),
            composites_vegetarian_kg=Decimal("0"),
            composites_vegan_kg=Decimal("0"),
            whole_diet_plant_weight_kg=Decimal("400"),
            whole_diet_animal_weight_kg=Decimal("300"),
            out_of_scope_count=0,
            unknown_count=0,
            methodology_version="1.0.0",
            methodology_source_edition="WWF Food Practice 2024",
            taxonomy_version="1.0.0",
            rules_version="0.1.0",
        )
        caveats = _wwf_caveats(s)
        assert not any("Step 1" in c for c in caveats)


# ---------------------------------------------------------------------------
# 5. Coverage in report document (via HTTP endpoint)
# ---------------------------------------------------------------------------

class TestCoverageInReport:
    def test_report_document_includes_coverage_section(self):
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        pids = [uuid4() for _ in range(4)]
        rows = _make_pt_rows(pids)
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=4,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        assert r.status_code == 200
        doc = r.json()
        assert "coverage" in doc
        cov = doc["coverage"]
        assert cov["products_total"] == 4
        assert "uncertainty_level" in cov
        assert "caveats" in cov
        assert isinstance(cov["caveats"], list)
        assert "review_completion_note" in cov

    def test_coverage_uncertainty_level_present(self):
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=[], summary_payload=_pt_summary(run_id), rows_count=0,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        cov = r.json()["coverage"]
        assert cov["uncertainty_level"] in ("low", "medium", "high")

    def test_wwf_report_coverage_missing_protein_is_null(self):
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        pids = [uuid4() for _ in range(3)]
        rows = _make_wwf_rows(pids)
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.WWF,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=rows, summary_payload=_wwf_summary(run_id), rows_count=3,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        cov = r.json()["coverage"]
        assert cov["products_with_missing_protein"] is None
        assert cov["missing_protein_share_pct"] is None

    def test_high_uncertainty_reflected_in_coverage_section(self):
        """When many products are unknown, uncertainty_level should be 'high'."""
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        pids = [uuid4() for _ in range(10)]
        # 2 classified, 8 unknown → 80% unknown → high
        groups = ["plant_based_core", "animal_core"] + ["unknown"] * 8
        rows = _make_pt_rows(pids, groups=groups)
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=rows, summary_payload=_pt_summary(run_id), rows_count=10,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        cov = r.json()["coverage"]
        assert cov["uncertainty_level"] == "high"

    def test_no_commercial_fields_in_coverage(self):
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=[], summary_payload=_pt_summary(run_id), rows_count=0,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        text = r.text.lower()
        for field in ("revenue", "margin", "cost_price", "sales_value", "supplier_id"):
            assert field not in text, f"forbidden field '{field}' in coverage response"

    def test_review_completion_note_in_coverage(self):
        store, altera_org, client_org, altera_user = _setup_basic()
        project = store.create_project(
            name="p", methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024", organisation_id=client_org.id,
            created_by=altera_user.user_id,
        )
        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=altera_user.user_id,
            rows_payload=[], summary_payload=_pt_summary(run_id), rows_count=0,
            organisation_id=client_org.id,
        )
        store.runs[run_id] = run

        auth = _auth(altera_user, altera_org)
        with _client(store, auth) as c:
            r = c.get(f"/api/v1/projects/{project.id}/runs/{run.id}/report")
        cov = r.json()["coverage"]
        assert "No products required manual review" in cov["review_completion_note"]
