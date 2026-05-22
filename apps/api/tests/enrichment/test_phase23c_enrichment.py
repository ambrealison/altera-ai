"""Phase 23C — explicit enriched nutrition usage in Protein Tracker calculations.

Tests:
- Default run does not use enrichment (backward compatibility)
- use_enriched_nutrition=True uses manual_altera value when protein_pct missing
- manual_altera preferred over category_average when both exist
- category_average used when only category_average record exists
- Enrichment NOT used if retailer-provided protein_pct already exists
- FAILED / NEEDED / NEEDS_MANUAL_REVIEW records ignored during selection
- Missing protein remains excluded if no valid enrichment record exists
- Run summary includes enrichment usage counts
- Client cannot enable use_enriched_nutrition (HTTP 403)
- enqueue_calculate client 403 for use_enriched_nutrition
- WWF calculation is unaffected by the flag
- Coverage caveats reflect actual run usage when enrichment was applied
- Coverage caveats fall back to project-level mode when enrichment not applied
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.auth.dependency import authed_user
from altera_api.auth.models import AuthContext, AuthProvider
from altera_api.calculation.protein_tracker import PTRunVersions, calculate_pt_run
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    ClientRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
    ProteinTrackerProductClassification,
)
from altera_api.enrichment.selection import (
    ResolvedProteinEnrichment,
    select_protein_enrichment,
)
from altera_api.exports.coverage import build_coverage_section
from altera_api.main import app

_NOW = datetime.now(UTC)

_PT_VERSIONS = PTRunVersions(
    methodology_version="0.1.0",
    methodology_source_edition="2023",
    taxonomy_version="0.1.0",
    rules_version="0.1.0",
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _org(store: InMemoryStore) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=_NOW,
    )
    user = UserProfile(
        user_id=store.default_user_id,
        email="altera@example.com",
        display_name="Altera Analyst",
        organisation_id=org.id,
        role=AlteraRole.ALTERA_ANALYST,
        created_at=_NOW,
    )
    store.upsert_user(user)
    return org


def _pt_product(
    project_id: UUID,
    org_id: UUID,
    *,
    protein_pct: Decimal | None = None,
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id="P001",
        product_name="Test Product",
        weight_per_item_kg=Decimal("1.0"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=protein_pct,
        ),
        created_at=_NOW,
    )


def _pt_classification(
    product_id: UUID,
    *,
    pt_group: ProteinTrackerGroup = ProteinTrackerGroup.PLANT_BASED_CORE,
) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product_id,
        pt_group=pt_group,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="r001",
        updated_at=_NOW,
    )


def _altera_ctx(org_id: UUID) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        email="altera@example.com",
        organisation_id=org_id,
        role=AlteraRole.ALTERA_ANALYST,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
    )


def _client_ctx(org_id: UUID) -> AuthContext:
    return AuthContext(
        user_id=uuid4(),
        email="client@retailco.example",
        organisation_id=org_id,
        role=ClientRole.CLIENT_OWNER,
        auth_provider=AuthProvider.DEV,
        is_dev_auth=True,
        organisation_type=OrganisationType.GMS_CLIENT,
    )


def _enrichment_record(
    product_id: UUID,
    *,
    source: NutritionEnrichmentSource,
    status: NutritionEnrichmentStatus = NutritionEnrichmentStatus.ENRICHED,
    enriched_value: Decimal | None = Decimal("15.0"),
) -> NutritionEnrichmentRecord:
    return NutritionEnrichmentRecord(
        product_id=product_id,
        nutrient="protein_pct",
        original_value=None,
        enriched_value=enriched_value,
        unit="g_per_100g",
        source=source,
        confidence=Decimal("0.85"),
        status=status,
        rationale="Test enrichment.",
        created_at=_NOW,
        created_by=uuid4(),
    )


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _setup_pt_project(store: InMemoryStore) -> tuple[Organisation, object, NormalizedProduct]:
    org = _org(store)
    project = store.create_project(
        name="PT Enrichment Test",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )
    product = _pt_product(project.id, org.id, protein_pct=None)
    store.add_product(product)
    clf = _pt_classification(product.id)
    store.upsert_pt_classification(clf)
    return org, project, product


# ---------------------------------------------------------------------------
# select_protein_enrichment — pure unit tests
# ---------------------------------------------------------------------------


class TestSelectProteinEnrichment:
    def test_returns_none_for_empty_list(self) -> None:
        assert select_protein_enrichment([]) is None

    def test_returns_none_when_all_needed(self) -> None:
        r = _enrichment_record(
            uuid4(),
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            status=NutritionEnrichmentStatus.NEEDED,
            enriched_value=None,
        )
        assert select_protein_enrichment([r]) is None

    def test_returns_none_when_all_failed(self) -> None:
        r = _enrichment_record(
            uuid4(),
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            status=NutritionEnrichmentStatus.FAILED,
            enriched_value=None,
        )
        assert select_protein_enrichment([r]) is None

    def test_returns_manual_altera_value(self) -> None:
        r = _enrichment_record(
            uuid4(),
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            enriched_value=Decimal("14.5"),
        )
        result = select_protein_enrichment([r])
        assert result is not None
        value, source = result.protein_pct, result.source
        assert value == Decimal("14.5")
        assert source is NutritionEnrichmentSource.MANUAL_ALTERA

    def test_manual_altera_preferred_over_category_average(self) -> None:
        pid = uuid4()
        manual = _enrichment_record(
            pid,
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            enriched_value=Decimal("12.0"),
        )
        cat_avg = _enrichment_record(
            pid,
            source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
            enriched_value=Decimal("15.0"),
        )
        result = select_protein_enrichment([cat_avg, manual])
        assert result is not None
        value, source = result.protein_pct, result.source
        assert source is NutritionEnrichmentSource.MANUAL_ALTERA
        assert value == Decimal("12.0")

    def test_category_average_used_when_no_manual(self) -> None:
        r = _enrichment_record(
            uuid4(),
            source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
            enriched_value=Decimal("15.0"),
        )
        result = select_protein_enrichment([r])
        assert result is not None
        value, source = result.protein_pct, result.source
        assert source is NutritionEnrichmentSource.CATEGORY_AVERAGE
        assert value == Decimal("15.0")

    def test_ignores_non_protein_nutrient(self) -> None:
        r = NutritionEnrichmentRecord(
            product_id=uuid4(),
            nutrient="fat_pct",
            original_value=None,
            enriched_value=Decimal("5.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.9"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="fat enrichment",
            created_at=_NOW,
            created_by=uuid4(),
        )
        assert select_protein_enrichment([r]) is None

    def test_ignores_enriched_record_with_none_value(self) -> None:
        r = _enrichment_record(
            uuid4(),
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            status=NutritionEnrichmentStatus.ENRICHED,
            enriched_value=None,
        )
        assert select_protein_enrichment([r]) is None


# ---------------------------------------------------------------------------
# Calculator — enrichment_lookup integration
# ---------------------------------------------------------------------------


class TestCalculatorWithEnrichmentLookup:
    def test_default_run_ignores_enrichment_lookup_none(self) -> None:
        """When enrichment_lookup is None, products missing protein are excluded."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        clf = _pt_classification(product.id)
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
            enrichment_lookup=None,
        )
        assert len(result.rows) == 0
        assert result.summary.total_in_scope_protein_kg == Decimal("0")
        assert result.summary.use_enriched_nutrition is False

    def test_enrichment_lookup_used_for_missing_protein(self) -> None:
        """When enrichment_lookup has an entry, it fills in missing protein_pct."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        clf = _pt_classification(product.id)

        enrichment_lookup = {
            product.id: ResolvedProteinEnrichment(protein_pct=Decimal("20.0"), source=NutritionEnrichmentSource.MANUAL_ALTERA)
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
            enrichment_lookup=enrichment_lookup,
        )
        # 100 items × 1 kg × 20% = 20 kg protein
        assert len(result.rows) == 1
        assert result.summary.total_in_scope_protein_kg == Decimal("20.00000000")
        assert result.summary.use_enriched_nutrition is True
        assert result.summary.enriched_nutrition_used_count == 1
        assert result.summary.manual_enrichment_used_count == 1
        assert result.summary.category_average_used_count == 0

    def test_retailer_protein_pct_not_overridden(self) -> None:
        """When product has retailer-provided protein_pct, enrichment_lookup entry is ignored."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=Decimal("30.0"))
        clf = _pt_classification(product.id)

        # Even if the lookup has an entry, the retailer value takes precedence
        enrichment_lookup = {
            product.id: ResolvedProteinEnrichment(protein_pct=Decimal("10.0"), source=NutritionEnrichmentSource.MANUAL_ALTERA)
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
            enrichment_lookup=enrichment_lookup,
        )
        # 100 × 1 kg × 30% = 30 kg (retailer value used, NOT 10%)
        assert result.summary.total_in_scope_protein_kg == Decimal("30.00000000")
        assert result.summary.enriched_nutrition_used_count == 0

    def test_category_average_counter_incremented(self) -> None:
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        clf = _pt_classification(product.id)

        enrichment_lookup = {
            product.id: ResolvedProteinEnrichment(protein_pct=Decimal("15.0"), source=NutritionEnrichmentSource.CATEGORY_AVERAGE)
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
            enrichment_lookup=enrichment_lookup,
        )
        assert result.summary.category_average_used_count == 1
        assert result.summary.manual_enrichment_used_count == 0
        assert result.summary.enriched_nutrition_used_count == 1

    def test_missing_protein_after_enrichment_count(self) -> None:
        """Product not in lookup still excluded — counter incremented."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        clf = _pt_classification(product.id)

        # empty lookup — no enrichment for this product
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
            enrichment_lookup={},  # non-None but empty → enrichment mode ON, no entry
        )
        assert len(result.rows) == 0
        assert result.summary.use_enriched_nutrition is True
        assert result.summary.missing_protein_after_enrichment_count == 1
        assert result.summary.enriched_nutrition_used_count == 0

    def test_summary_backward_compat_defaults(self) -> None:
        """Existing summary_payload dicts without enrichment fields still parse.

        Simulates a pre-23C record: build a summary, dump it, remove the new
        fields, then re-validate — the defaults must kick in.
        """
        aggs = tuple(
            ProteinTrackerGroupAggregate(
                pt_group=g,
                volume_kg=Decimal("0"),
                protein_kg=Decimal("0"),
                item_count=0,
            )
            for g in (
                ProteinTrackerGroup.PLANT_BASED_CORE,
                ProteinTrackerGroup.PLANT_BASED_NON_CORE,
                ProteinTrackerGroup.COMPOSITE_PRODUCTS,
                ProteinTrackerGroup.ANIMAL_CORE,
            )
        )
        summary = ProteinTrackerCalculationSummary(
            run_id=uuid4(),
            reporting_period_label="2024",
            per_group=aggs,
            plant_protein_kg=Decimal("0"),
            animal_protein_kg=Decimal("0"),
            total_in_scope_protein_kg=Decimal("0"),
            plant_share_pct=None,
            animal_share_pct=None,
            rows_with_per_product_split=0,
            rows_protein_source_label=0,
            rows_protein_source_reference_db=0,
            out_of_scope_count=0,
            unknown_count=0,
            methodology_version="0.1.0",
            methodology_source_edition="2023",
            taxonomy_version="0.1.0",
            rules_version="0.1.0",
        )
        old_payload = summary.model_dump()
        # Remove Phase 23C fields to simulate a pre-23C stored record.
        for field in (
            "use_enriched_nutrition",
            "enriched_nutrition_used_count",
            "manual_enrichment_used_count",
            "category_average_used_count",
            "missing_protein_after_enrichment_count",
        ):
            old_payload.pop(field, None)

        reparsed = ProteinTrackerCalculationSummary.model_validate(old_payload)
        assert reparsed.use_enriched_nutrition is False
        assert reparsed.enriched_nutrition_used_count == 0
        assert reparsed.manual_enrichment_used_count == 0
        assert reparsed.category_average_used_count == 0
        assert reparsed.missing_protein_after_enrichment_count == 0


# ---------------------------------------------------------------------------
# Orchestrator / API — use_enriched_nutrition=True end-to-end
# ---------------------------------------------------------------------------


class TestRunCalculationWithEnrichment:
    def test_run_with_enrichment_uses_manual_record(self) -> None:
        """Full orchestrator path: enriched record fills in missing protein_pct."""
        store = InMemoryStore()
        org, project, product = _setup_pt_project(store)

        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                enriched_value=Decimal("20.0"),
            )
        )

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.PROTEIN_TRACKER,
            triggered_by=uuid4(),
            use_enriched_nutrition=True,
        )
        assert record.use_enriched_nutrition is True
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        assert summary.use_enriched_nutrition is True
        assert summary.enriched_nutrition_used_count == 1
        assert summary.manual_enrichment_used_count == 1
        assert summary.total_in_scope_protein_kg == Decimal("20.00000000")

    def test_run_without_flag_ignores_enrichment(self) -> None:
        """Default run (use_enriched_nutrition=False) ignores enrichment records."""
        store = InMemoryStore()
        org, project, product = _setup_pt_project(store)

        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                enriched_value=Decimal("20.0"),
            )
        )

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.PROTEIN_TRACKER,
            triggered_by=uuid4(),
            use_enriched_nutrition=False,
        )
        assert record.use_enriched_nutrition is False
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        assert summary.use_enriched_nutrition is False
        assert summary.total_in_scope_protein_kg == Decimal("0")

    def test_manual_preferred_over_category_average_in_full_path(self) -> None:
        """Orchestrator selects manual_altera over category_average."""
        store = InMemoryStore()
        org, project, product = _setup_pt_project(store)

        # Add both manual and category_average records
        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
                enriched_value=Decimal("15.0"),
            )
        )
        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                enriched_value=Decimal("25.0"),
            )
        )

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.PROTEIN_TRACKER,
            triggered_by=uuid4(),
            use_enriched_nutrition=True,
        )
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        # 100 × 1 kg × 25% = 25 kg (manual value used)
        assert summary.total_in_scope_protein_kg == Decimal("25.00000000")
        assert summary.manual_enrichment_used_count == 1
        assert summary.category_average_used_count == 0

    def test_failed_record_not_used(self) -> None:
        """FAILED enrichment records are skipped; product remains excluded."""
        store = InMemoryStore()
        org, project, product = _setup_pt_project(store)

        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                status=NutritionEnrichmentStatus.FAILED,
                enriched_value=Decimal("18.0"),
            )
        )

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.PROTEIN_TRACKER,
            triggered_by=uuid4(),
            use_enriched_nutrition=True,
        )
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        assert summary.total_in_scope_protein_kg == Decimal("0")
        assert summary.enriched_nutrition_used_count == 0
        assert summary.missing_protein_after_enrichment_count == 1

    def test_needed_record_not_used(self) -> None:
        """NEEDED enrichment records are skipped; product remains excluded."""
        store = InMemoryStore()
        org, project, product = _setup_pt_project(store)

        store.add_enrichment_record(
            _enrichment_record(
                product.id,
                source=NutritionEnrichmentSource.UNKNOWN,
                status=NutritionEnrichmentStatus.NEEDED,
                enriched_value=None,
            )
        )

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.PROTEIN_TRACKER,
            triggered_by=uuid4(),
            use_enriched_nutrition=True,
        )
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        assert summary.total_in_scope_protein_kg == Decimal("0")
        assert summary.enriched_nutrition_used_count == 0
        assert summary.missing_protein_after_enrichment_count == 1


# ---------------------------------------------------------------------------
# HTTP API — auth gate
# ---------------------------------------------------------------------------


class TestAuthGate:
    def test_client_can_now_use_enriched_nutrition_create_run(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Phase 34M — the Altera-only gate on use_enriched_nutrition
        has been removed. The wizard is now the canonical flow and
        NEVO/manual enrichment records ARE the normal nutrition source
        for everyone. The 403 / "forbidden" branch this test guarded
        is gone; a GMS-client run with use_enriched_nutrition=True
        falls through to the workflow gate (run_not_ready when the
        project has no products) instead of being denied for
        permissions."""
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        ctx = _client_ctx(org.id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/runs",
                json={"methodology": "protein_tracker", "use_enriched_nutrition": True},
            )
            assert r.status_code != 403, r.text
            assert r.status_code == 400
            assert r.json()["detail"]["error_code"] == "run_not_ready"
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_altera_can_use_enriched_nutrition_create_run(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Same behaviour as the client case post-Phase-34M: empty
        project → run_not_ready, not a permission denial."""
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        ctx = _altera_ctx(org.id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/runs",
                json={"methodology": "protein_tracker", "use_enriched_nutrition": True},
            )
            assert r.status_code != 403, r.text
            assert r.status_code == 400
            assert r.json()["detail"]["error_code"] == "run_not_ready"
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_enqueue_calculate_with_enrichment(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """HTTP 403 when client tries to enqueue a job with use_enriched_nutrition."""
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        ctx = _client_ctx(org.id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/jobs/calculate",
                json={"methodology": "protein_tracker", "use_enriched_nutrition": True},
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_default_run_no_flag_client_allowed(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Client users are NOT blocked by the use_enriched_nutrition
        permission gate when the flag is left at its default ``False``.
        Phase 34A: an empty project still fails the workflow gate with
        ``run_not_ready`` — but crucially not with ``403``."""
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        ctx = _client_ctx(org.id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/runs",
                json={"methodology": "protein_tracker"},
            )
            assert r.status_code != 403, r.text
            # Phase 34A: empty project → 400 run_not_ready.
            assert r.status_code == 400
            assert r.json()["detail"]["error_code"] == "run_not_ready"
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# WWF unaffected
# ---------------------------------------------------------------------------


class TestWWFUnaffected:
    def test_wwf_run_ignores_use_enriched_nutrition(self) -> None:
        """WWF calculation path is unaffected by the enrichment flag."""
        from altera_api.api.orchestrator import run_calculation

        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="WWF Project",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        # No products, no classifications — empty run is valid
        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.WWF,
            triggered_by=uuid4(),
            use_enriched_nutrition=True,  # flag present but ignored for WWF
        )
        # RunRecord captures the flag but WWF summary has no enrichment fields
        assert record.use_enriched_nutrition is True
        # WWF summary schema does not have use_enriched_nutrition
        assert "use_enriched_nutrition" not in record.summary_payload


# ---------------------------------------------------------------------------
# Coverage caveats — Phase 23C run-mode vs project-mode
# ---------------------------------------------------------------------------


def _make_run_record(
    project_id: UUID,
    org_id: UUID,
    product_ids: list[UUID],
    *,
    use_enriched_nutrition: bool = False,
    manual_enrichment_used_count: int = 0,
    category_average_used_count: int = 0,
    missing_protein_after_enrichment_count: int = 0,
) -> object:
    from altera_api.api.state import RunRecord

    run_id = uuid4()
    aggs = tuple(
        ProteinTrackerGroupAggregate(
            pt_group=g,
            volume_kg=Decimal("0"),
            protein_kg=Decimal("0"),
            item_count=0,
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    )
    summary = ProteinTrackerCalculationSummary(
        run_id=run_id,
        reporting_period_label="2024",
        per_group=aggs,
        plant_protein_kg=Decimal("0"),
        animal_protein_kg=Decimal("0"),
        total_in_scope_protein_kg=Decimal("0"),
        plant_share_pct=None,
        animal_share_pct=None,
        rows_with_per_product_split=0,
        rows_protein_source_label=0,
        rows_protein_source_reference_db=0,
        out_of_scope_count=0,
        unknown_count=0,
        use_enriched_nutrition=use_enriched_nutrition,
        manual_enrichment_used_count=manual_enrichment_used_count,
        category_average_used_count=category_average_used_count,
        missing_protein_after_enrichment_count=missing_protein_after_enrichment_count,
        methodology_version="0.1.0",
        methodology_source_edition="2023",
        taxonomy_version="0.1.0",
        rules_version="0.1.0",
    )
    rows_payload = [
        {
            "product_id": str(pid),
            "pt_group": "plant_based_core",
            "in_scope": True,
            "volume_kg": "1.0",
            "protein_pct": "15.0",
            "protein_kg": "0.15",
            "used_per_product_split": False,
            "plant_protein_kg": None,
            "animal_protein_kg": None,
            "run_id": str(run_id),
            "methodology_version": "0.1.0",
            "methodology_source_edition": "2023",
            "taxonomy_version": "0.1.0",
            "rules_version": "0.1.0",
        }
        for pid in product_ids
    ]
    return RunRecord(
        id=run_id,
        project_id=project_id,
        organisation_id=org_id,
        methodology=Methodology.PROTEIN_TRACKER,
        started_at=_NOW,
        finished_at=_NOW,
        triggered_by=uuid4(),
        rows_payload=rows_payload,
        summary_payload=summary.model_dump(),
        rows_count=len(product_ids),
        use_enriched_nutrition=use_enriched_nutrition,
    )


class TestCoverageEnrichmentCaveats23C:
    def _setup(self, store: InMemoryStore) -> tuple[object, object, UUID]:
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        store.add_product(product)
        return org, project, product.id

    def test_run_mode_manual_caveat(self) -> None:
        """When use_enriched_nutrition=True and manual was used, caveat says 'in this calculation'."""
        store = InMemoryStore()
        org, project, product_id = self._setup(store)
        run = _make_run_record(
            project.id,
            org.id,
            [product_id],
            use_enriched_nutrition=True,
            manual_enrichment_used_count=1,
        )
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "manually-entered" in caveat_text
        assert "in this calculation" in caveat_text
        assert "not yet applied" not in caveat_text

    def test_run_mode_category_average_caveat(self) -> None:
        """When use_enriched_nutrition=True and category_average was used, caveat says 'in this calculation'."""
        store = InMemoryStore()
        org, project, product_id = self._setup(store)
        run = _make_run_record(
            project.id,
            org.id,
            [product_id],
            use_enriched_nutrition=True,
            category_average_used_count=2,
        )
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "category-average" in caveat_text
        assert "in this calculation" in caveat_text
        assert "not yet applied" not in caveat_text

    def test_run_mode_missing_caveat(self) -> None:
        """Missing protein after enrichment produces its own caveat."""
        store = InMemoryStore()
        org, project, product_id = self._setup(store)
        run = _make_run_record(
            project.id,
            org.id,
            [product_id],
            use_enriched_nutrition=True,
            missing_protein_after_enrichment_count=3,
        )
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "3 product(s) had missing" in caveat_text
        assert "excluded from protein totals" in caveat_text

    def test_project_mode_fallback_when_enrichment_not_applied(self) -> None:
        """When use_enriched_nutrition=False but enrichment records exist, project-mode caveats shown."""
        store = InMemoryStore()
        org, project, product_id = self._setup(store)

        store.add_enrichment_record(
            _enrichment_record(
                product_id,
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                enriched_value=Decimal("14.0"),
            )
        )
        run = _make_run_record(
            project.id, org.id, [product_id], use_enriched_nutrition=False
        )
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "manually-entered" in caveat_text
        assert "not yet applied to this calculation" in caveat_text

    def test_run_mode_zero_counts_produces_no_enrichment_caveats(self) -> None:
        """When enrichment was applied but all products had retailer values, no enrichment caveats."""
        store = InMemoryStore()
        org, project, product_id = self._setup(store)
        # use_enriched_nutrition=True but zero products actually used enrichment
        run = _make_run_record(
            project.id,
            org.id,
            [product_id],
            use_enriched_nutrition=True,
            manual_enrichment_used_count=0,
            category_average_used_count=0,
            missing_protein_after_enrichment_count=0,
        )
        section = build_coverage_section(store, run, project)

        enrichment_caveats = [
            c for c in section.caveats if "enriched" in c.lower() or "manually-entered" in c.lower()
        ]
        assert enrichment_caveats == []
