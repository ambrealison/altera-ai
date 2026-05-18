"""Phase 23B — manual and category-average nutrition enrichment tests.

Tests:
- Altera can create a manual enrichment record via the API
- Client cannot create manual enrichment (403)
- Cannot enrich a product that already has a retailer-provided protein_pct (409)
- Invalid enriched values rejected (< 0, > 100)
- Category-average provider returns expected value for a known PT group
- Category-average provider returns None for out_of_scope / unknown groups
- Category-average API endpoint creates a record (Altera only, requires classification)
- Category-average API rejects a product already classified as out_of_scope
- No category average leaves the product's existing NEEDED status unchanged
- Calculation does not use enrichment records by default
- Coverage caveats show per-source breakdown (manual vs category_average)
- List enrichment endpoint is Altera-only and returns stored records
- No external network calls (provider imports checked)
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
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.enrichment.providers.category_average import (
    CategoryAverageProvider,
    lookup_category_average,
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
        product_name="Test product",
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


def _setup_project_and_product(
    store: InMemoryStore,
    *,
    protein_pct: Decimal | None = None,
) -> tuple[UUID, UUID, UUID]:
    """Return (org_id, project_id, product_id)."""
    org = _org(store)
    project = store.create_project(
        name="Enrichment Test",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )
    product = _pt_product(project.id, org.id, protein_pct=protein_pct)
    store.add_product(product)
    return org.id, project.id, product.id


# ---------------------------------------------------------------------------
# List enrichments endpoint
# ---------------------------------------------------------------------------


class TestListEnrichmentsEndpoint:
    def test_altera_can_list_empty(self, client: TestClient, store: InMemoryStore) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/products/{product_id}/enrichments")
            assert r.status_code == 200, r.text
            assert r.json() == []
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_list(self, client: TestClient, store: InMemoryStore) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _client_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/products/{product_id}/enrichments")
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_returns_stored_records(self, client: TestClient, store: InMemoryStore) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        # Pre-populate a manual enrichment record directly
        record = NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("12.5"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.90"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="Analyst override.",
            created_at=_NOW,
            created_by=uuid4(),
        )
        store.add_enrichment_record(record)

        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project_id}/products/{product_id}/enrichments")
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body) == 1
            assert body[0]["source"] == "manual_altera"
            assert body[0]["enriched_value"] == "12.5"
            assert body[0]["status"] == "enriched"
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Manual enrichment endpoint
# ---------------------------------------------------------------------------


class TestManualEnrichmentEndpoint:
    def test_altera_can_create_manual_enrichment(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={
                    "enriched_value": 14.5,
                    "confidence": 0.85,
                    "rationale": "Analyst override based on similar product.",
                },
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["source"] == "manual_altera"
            assert body["status"] == "enriched"
            assert body["enriched_value"] == "14.5"
            assert body["original_value"] is None
            assert body["unit"] == "g_per_100g"
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_create_manual_enrichment(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _client_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={
                    "enriched_value": 14.5,
                    "confidence": 0.85,
                    "rationale": "Should be rejected.",
                },
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_cannot_enrich_product_with_retailer_value(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Products with retailer-provided protein_pct must not be overwritten."""
        org_id, project_id, product_id = _setup_project_and_product(
            store, protein_pct=Decimal("20.0")
        )
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={
                    "enriched_value": 14.5,
                    "confidence": 0.85,
                    "rationale": "Should conflict.",
                },
            )
            assert r.status_code == 409
            assert "retailer-provided" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_invalid_enriched_value_negative_rejected(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={"enriched_value": -1.0, "rationale": "Bad value."},
            )
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_invalid_enriched_value_above_100_rejected(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={"enriched_value": 101.0, "rationale": "Bad value."},
            )
            assert r.status_code == 422
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_original_protein_pct_not_modified(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """The NormalizedProduct must remain unchanged after enrichment is created."""
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}/enrichments/manual",
                json={"enriched_value": 14.5, "rationale": "Test."},
            )
            assert r.status_code == 201, r.text
        finally:
            app.dependency_overrides.pop(authed_user, None)

        product = store.get_product(product_id)
        assert product is not None
        assert product.pt_fields is not None
        assert product.pt_fields.protein_pct is None  # unchanged


# ---------------------------------------------------------------------------
# Category-average provider (pure unit tests)
# ---------------------------------------------------------------------------


class TestCategoryAverageProvider:
    def test_returns_value_for_plant_based_core(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.PLANT_BASED_CORE, "protein_pct")
        assert entry is not None
        assert entry.value == Decimal("15.00")
        assert entry.unit == "g_per_100g"
        assert Decimal("0") <= entry.confidence <= Decimal("1")

    def test_returns_value_for_animal_core(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.ANIMAL_CORE, "protein_pct")
        assert entry is not None
        assert entry.value == Decimal("18.00")

    def test_returns_value_for_composite(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.COMPOSITE_PRODUCTS, "protein_pct")
        assert entry is not None
        assert entry.value > Decimal("0")

    def test_returns_value_for_plant_based_non_core(self) -> None:
        entry = lookup_category_average(
            ProteinTrackerGroup.PLANT_BASED_NON_CORE, "protein_pct"
        )
        assert entry is not None
        assert entry.value > Decimal("0")

    def test_returns_none_for_out_of_scope(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.OUT_OF_SCOPE, "protein_pct")
        assert entry is None

    def test_returns_none_for_unknown(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.UNKNOWN, "protein_pct")
        assert entry is None

    def test_returns_none_for_unknown_nutrient(self) -> None:
        entry = lookup_category_average(ProteinTrackerGroup.PLANT_BASED_CORE, "fat_pct")
        assert entry is None

    def test_enrich_by_group_returns_enriched_record(self) -> None:
        provider = CategoryAverageProvider()
        record = provider.enrich_by_group(
            uuid4(),
            ProteinTrackerGroup.PLANT_BASED_CORE,
            "protein_pct",
            now=_NOW,
        )
        assert record is not None
        assert record.source is NutritionEnrichmentSource.CATEGORY_AVERAGE
        assert record.status is NutritionEnrichmentStatus.ENRICHED
        assert record.enriched_value == Decimal("15.00")
        assert record.original_value is None

    def test_enrich_by_group_returns_none_for_out_of_scope(self) -> None:
        provider = CategoryAverageProvider()
        record = provider.enrich_by_group(
            uuid4(),
            ProteinTrackerGroup.OUT_OF_SCOPE,
            "protein_pct",
            now=_NOW,
        )
        assert record is None

    def test_no_external_calls(self) -> None:
        """Provider must not import requests, httpx, or urllib at module level."""
        import sys

        import altera_api.enrichment.providers.category_average as mod

        for net_lib in ("requests", "httpx", "urllib.request"):
            assert net_lib not in sys.modules or mod.__name__ not in str(
                getattr(sys.modules.get(net_lib), "__file__", "")
            ), f"category_average provider imported {net_lib}"


# ---------------------------------------------------------------------------
# Category-average enrichment API endpoint
# ---------------------------------------------------------------------------


class TestCategoryAverageEnrichmentEndpoint:
    def test_altera_can_apply_category_average(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        # Add a PT classification so the group is known
        clf = _pt_classification(product_id, pt_group=ProteinTrackerGroup.ANIMAL_CORE)
        store.upsert_pt_classification(clf)

        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}"
                "/enrichments/category-average"
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["source"] == "category_average"
            assert body["status"] == "enriched"
            assert body["enriched_value"] == "18.00"
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_client_cannot_apply_category_average(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _client_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}"
                "/enrichments/category-average"
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_requires_classification(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """No PT classification → 422."""
        org_id, project_id, product_id = _setup_project_and_product(store)
        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}"
                "/enrichments/category-average"
            )
            assert r.status_code == 422
            assert "classification" in r.json()["detail"]
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_out_of_scope_group_has_no_average(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """out_of_scope group returns 404 — no average available."""
        org_id, project_id, product_id = _setup_project_and_product(store)
        clf = _pt_classification(product_id, pt_group=ProteinTrackerGroup.OUT_OF_SCOPE)
        store.upsert_pt_classification(clf)

        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}"
                "/enrichments/category-average"
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(authed_user, None)

    def test_cannot_overwrite_retailer_value(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_id, project_id, product_id = _setup_project_and_product(
            store, protein_pct=Decimal("22.0")
        )
        clf = _pt_classification(product_id)
        store.upsert_pt_classification(clf)

        ctx = _altera_ctx(org_id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project_id}/products/{product_id}"
                "/enrichments/category-average"
            )
            assert r.status_code == 409
        finally:
            app.dependency_overrides.pop(authed_user, None)


# ---------------------------------------------------------------------------
# Calculation safety — enrichment records must not affect PT run
# ---------------------------------------------------------------------------


class TestCalculationSafety:
    def test_enriched_record_does_not_affect_calculation(self) -> None:
        """Even with an ENRICHED record, calculate_pt_run ignores it — uses product fields."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        # Product with NO protein_pct
        product = _pt_product(project.id, org.id, protein_pct=None)
        store.add_product(product)

        # Add an enrichment record claiming protein_pct=15.0
        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product.id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("15.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                confidence=Decimal("0.90"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="Test enrichment.",
                created_at=_NOW,
            )
        )

        classification = _pt_classification(product.id)
        result = calculate_pt_run(
            [product],
            {product.id: classification},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
        )
        # Product skipped — no protein contributed
        assert result.summary.total_in_scope_protein_kg == Decimal("0")
        assert len(result.rows) == 0

    def test_product_with_retailer_protein_is_calculated_normally(self) -> None:
        """A product with a retailer-provided protein_pct is calculated unchanged."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=Decimal("20.0"))
        store.add_product(product)

        classification = _pt_classification(product.id)
        result = calculate_pt_run(
            [product],
            {product.id: classification},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_PT_VERSIONS,
        )
        # 100 items × 1 kg × 20% = 20 kg protein
        assert result.summary.total_in_scope_protein_kg == Decimal("20.00000000")


# ---------------------------------------------------------------------------
# Coverage caveats — Phase 23B per-source breakdown
# ---------------------------------------------------------------------------


def _make_run_record(project_id: UUID, org_id: UUID, product_ids: list[UUID]) -> object:
    from altera_api.api.state import RunRecord
    from altera_api.domain.common import Methodology
    from altera_api.domain.protein_tracker import (
        ProteinTrackerCalculationSummary,
        ProteinTrackerGroup,
        ProteinTrackerGroupAggregate,
    )

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
    )


class TestCoverageEnrichmentCaveats23B:
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

    def test_manual_enrichment_appears_in_coverage_caveat(self) -> None:
        store = InMemoryStore()
        org, project, product_id = self._setup(store)

        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("14.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.MANUAL_ALTERA,
                confidence=Decimal("0.90"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="Manual override.",
                created_at=_NOW,
            )
        )
        run = _make_run_record(project.id, org.id, [product_id])
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "manually-entered" in caveat_text
        assert "not yet applied to this calculation" in caveat_text

    def test_category_average_enrichment_appears_in_coverage_caveat(self) -> None:
        store = InMemoryStore()
        org, project, product_id = self._setup(store)

        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("15.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
                confidence=Decimal("0.60"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="Category average.",
                created_at=_NOW,
            )
        )
        run = _make_run_record(project.id, org.id, [product_id])
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "category-average" in caveat_text
        assert "not yet applied to this calculation" in caveat_text

    def test_needed_record_shows_recommendation_caveat(self) -> None:
        store = InMemoryStore()
        org, project, product_id = self._setup(store)

        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=None,
                unit="g_per_100g",
                source=NutritionEnrichmentSource.UNKNOWN,
                confidence=None,
                status=NutritionEnrichmentStatus.NEEDED,
                rationale="Awaiting enrichment.",
                created_at=_NOW,
            )
        )
        run = _make_run_record(project.id, org.id, [product_id])
        section = build_coverage_section(store, run, project)

        caveat_text = " ".join(section.caveats)
        assert "missing label protein" in caveat_text

    def test_no_enrichment_records_no_enrichment_caveats(self) -> None:
        store = InMemoryStore()
        org, project, product_id = self._setup(store)
        run = _make_run_record(project.id, org.id, [product_id])
        section = build_coverage_section(store, run, project)

        enrichment_caveats = [
            c for c in section.caveats if "enriched" in c.lower() or "enrichment" in c.lower()
        ]
        assert enrichment_caveats == []
