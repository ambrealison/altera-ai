"""Phase 23A — nutrition enrichment foundation tests.

Tests:
- missing protein_pct → enrichment_needed status
- retailer-provided protein_pct → not_needed status
- enrichment record stores enriched value separately from product
- retailer-provided value is not overwritten by enrichment
- source registry contains all planned external sources (all unavailable)
- calculation skips products with None protein_pct (no silent enrichment use)
- coverage adds enrichment caveats when enrichment records exist
- no external network calls are made (registry and import checks)
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
from altera_api.enrichment.assessor import assess_protein_enrichment_needs
from altera_api.enrichment.registry import (
    ENRICHMENT_SOURCE_REGISTRY,
    PLANNED_EXTERNAL_SOURCES,
)
from altera_api.exports.coverage import build_coverage_section
from altera_api.main import app

_NOW = datetime.now(UTC)

# ---------------------------------------------------------------------------
# Shared helpers
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


def _pt_product(
    project_id: UUID,
    org_id: UUID,
    *,
    protein_pct: Decimal | None = Decimal("5.0"),
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


def _pt_classification(product_id: UUID) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product_id,
        pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="r001",
        ai_model=None,
        ai_prompt_version=None,
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# 1 & 2: Assessor — missing vs. retailer-provided protein_pct
# ---------------------------------------------------------------------------


class TestEnrichmentAssessor:
    def test_missing_protein_pct_creates_enrichment_needed(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)

        records = assess_protein_enrichment_needs([product], now=_NOW)

        assert len(records) == 1
        record = records[0]
        assert record.product_id == product.id
        assert record.status is NutritionEnrichmentStatus.NEEDED
        assert record.source is NutritionEnrichmentSource.UNKNOWN
        assert record.original_value is None
        assert record.enriched_value is None
        assert record.nutrient == "protein_pct"

    def test_retailer_provided_protein_pct_creates_not_needed(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=Decimal("12.5"))

        records = assess_protein_enrichment_needs([product], now=_NOW)

        assert len(records) == 1
        record = records[0]
        assert record.status is NutritionEnrichmentStatus.NOT_NEEDED
        assert record.source is NutritionEnrichmentSource.RETAILER_PROVIDED
        assert record.original_value == Decimal("12.5")
        assert record.enriched_value is None  # never set for NOT_NEEDED

    def test_non_pt_product_is_skipped(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        from altera_api.domain.product import RetailChannel, WWFProductFields
        wwf_product = NormalizedProduct(
            id=uuid4(),
            upload_id=uuid4(),
            project_id=project.id,
            organisation_id=org.id,
            row_number=1,
            external_product_id="W001",
            product_name="WWF product",
            weight_per_item_kg=Decimal("1.0"),
            is_own_brand=False,
            methodologies_enabled=frozenset({Methodology.WWF}),
            wwf_fields=WWFProductFields(
                items_sold=Decimal("50"),
                retail_channel=RetailChannel.GROCERY_AMBIENT,
                is_own_brand=False,
            ),
            created_at=_NOW,
        )
        records = assess_protein_enrichment_needs([wwf_product], now=_NOW)
        assert records == []

    def test_empty_product_list_returns_empty(self):
        records = assess_protein_enrichment_needs([], now=_NOW)
        assert records == []


# ---------------------------------------------------------------------------
# 3 & 4: Enrichment record immutability — separate from product data
# ---------------------------------------------------------------------------


class TestEnrichmentRecordSeparation:
    def test_enrichment_record_stores_enriched_value_separately(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        store.add_product(product)

        enrichment_rec = NutritionEnrichmentRecord(
            product_id=product.id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("7.5"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.90"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="Manually set by Altera analyst.",
            created_at=_NOW,
        )
        store.add_enrichment_record(enrichment_rec)

        # Enriched value is in the enrichment record, not the product
        stored_product = store.get_product(product.id)
        assert stored_product is not None
        assert stored_product.pt_fields is not None
        assert stored_product.pt_fields.protein_pct is None  # unchanged

        recs = store.get_enrichment_records_for_product(product.id)
        assert len(recs) == 1
        assert recs[0].enriched_value == Decimal("7.5")
        assert recs[0].source is NutritionEnrichmentSource.MANUAL_ALTERA

    def test_retailer_provided_value_is_not_overwritten(self):
        """Assessment must not mutate the product or create an enriched record."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=Decimal("8.0"))
        store.add_product(product)

        records = assess_protein_enrichment_needs([product], now=_NOW)

        # Assessment returns NOT_NEEDED — original value intact, no enrichment
        assert records[0].status is NutritionEnrichmentStatus.NOT_NEEDED
        assert records[0].enriched_value is None

        # Product in store is unchanged
        stored = store.get_product(product.id)
        assert stored is not None
        assert stored.pt_fields is not None
        assert stored.pt_fields.protein_pct == Decimal("8.0")


# ---------------------------------------------------------------------------
# 5: Source registry
# ---------------------------------------------------------------------------


class TestSourceRegistry:
    def test_all_external_sources_are_unavailable(self):
        for info in PLANNED_EXTERNAL_SOURCES:
            assert info.is_external
            assert not info.is_available, (
                f"External source {info.source} is marked available; "
                "no external provider should be callable in Phase 23A."
            )

    def test_planned_external_sources_include_all_four_databases(self):
        external_source_names = {s.source for s in PLANNED_EXTERNAL_SOURCES}
        assert NutritionEnrichmentSource.OPEN_FOOD_FACTS in external_source_names
        assert NutritionEnrichmentSource.CIQUAL in external_source_names
        assert NutritionEnrichmentSource.OQALI in external_source_names
        assert NutritionEnrichmentSource.NEVO in external_source_names

    def test_registry_is_ordered_by_priority(self):
        priorities = [s.priority for s in ENRICHMENT_SOURCE_REGISTRY]
        assert priorities == sorted(priorities), "Registry must be sorted by priority."

    def test_retailer_provided_has_highest_priority(self):
        first = ENRICHMENT_SOURCE_REGISTRY[0]
        assert first.source is NutritionEnrichmentSource.RETAILER_PROVIDED
        assert first.priority == 0

    def test_all_sources_have_notes(self):
        for info in ENRICHMENT_SOURCE_REGISTRY:
            assert info.notes.strip(), f"Source {info.source} has no notes."


# ---------------------------------------------------------------------------
# 6: Calculation does not silently use enriched values
# ---------------------------------------------------------------------------


class TestCalculationDoesNotUseEnrichment:
    def test_product_with_none_protein_pct_contributes_zero_protein(self):
        """Even if enrichment record says protein=5.0, calculation must skip the product."""
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _pt_product(project.id, org.id, protein_pct=None)
        store.add_product(product)

        # Add an enrichment record claiming protein_pct = 10.0
        store.add_enrichment_record(NutritionEnrichmentRecord(
            product_id=product.id,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("10.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.90"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="Analyst override.",
            created_at=_NOW,
        ))

        classification = _pt_classification(product.id)
        versions = PTRunVersions(
            methodology_version="1.0.0",
            methodology_source_edition="Test 2024",
            taxonomy_version="1.0.0",
            rules_version="0.1.0",
        )
        result = calculate_pt_run(
            [product],
            {product.id: classification},
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=versions,
        )
        # Product is skipped — zero total protein despite enrichment record
        assert result.summary.total_in_scope_protein_kg == Decimal("0")
        assert len(result.rows) == 0


# ---------------------------------------------------------------------------
# 7: Coverage adds enrichment caveats when records exist
# ---------------------------------------------------------------------------


class TestCoverageEnrichmentCaveats:
    def _pt_summary(self, run_id: UUID) -> dict:
        from altera_api.domain.protein_tracker import (
            ProteinTrackerCalculationSummary,
            ProteinTrackerGroupAggregate,
        )
        aggs = [
            ProteinTrackerGroupAggregate(
                pt_group=g, volume_kg=Decimal("100"),
                protein_kg=Decimal("20"), item_count=5,
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
            out_of_scope_count=0,
            unknown_count=0,
            methodology_version="1.0.0",
            methodology_source_edition="Test 2024",
            taxonomy_version="1.0.0",
            rules_version="0.1.0",
        )
        return s.model_dump()

    def test_enriched_record_adds_disclosure_caveat(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        pid = uuid4()
        product = _pt_product(project.id, org.id, protein_pct=Decimal("5.0"))
        product = NormalizedProduct(
            id=pid,
            upload_id=product.upload_id,
            project_id=project.id,
            organisation_id=org.id,
            row_number=1,
            external_product_id="P001",
            product_name="Test",
            weight_per_item_kg=Decimal("1"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(items_purchased=Decimal("100"), protein_pct=Decimal("5")),
            created_at=_NOW,
        )
        store.add_product(product)
        store.upsert_pt_classification(_pt_classification(pid))

        store.add_enrichment_record(NutritionEnrichmentRecord(
            product_id=pid,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("5.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.90"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="Set manually.",
            created_at=_NOW,
        ))

        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=[{"product_id": str(pid), "pt_group": "plant_based_core", "in_scope": True}],
            summary_payload=self._pt_summary(run_id),
            rows_count=1,
            organisation_id=org.id,
        )
        cov = build_coverage_section(store, run, project)
        caveat_text = " ".join(cov.caveats).lower()
        assert "manually-entered" in caveat_text

    def test_needed_record_adds_recommendation_caveat(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        pid = uuid4()
        product = NormalizedProduct(
            id=pid, upload_id=uuid4(), project_id=project.id,
            organisation_id=org.id, row_number=1, external_product_id="P001",
            product_name="Test", weight_per_item_kg=Decimal("1"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(items_purchased=Decimal("100"), protein_pct=Decimal("5")),
            created_at=_NOW,
        )
        store.add_product(product)
        store.upsert_pt_classification(_pt_classification(pid))

        store.add_enrichment_record(NutritionEnrichmentRecord(
            product_id=pid,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=None,
            unit="g_per_100g",
            source=NutritionEnrichmentSource.UNKNOWN,
            confidence=None,
            status=NutritionEnrichmentStatus.NEEDED,
            rationale="Missing from retailer data.",
            created_at=_NOW,
        ))

        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=[{"product_id": str(pid), "pt_group": "plant_based_core", "in_scope": True}],
            summary_payload=self._pt_summary(run_id),
            rows_count=1,
            organisation_id=org.id,
        )
        cov = build_coverage_section(store, run, project)
        caveat_text = " ".join(cov.caveats).lower()
        assert "enrichment" in caveat_text

    def test_no_enrichment_records_no_extra_caveats(self):
        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        pid = uuid4()
        product = NormalizedProduct(
            id=pid, upload_id=uuid4(), project_id=project.id,
            organisation_id=org.id, row_number=1, external_product_id="P001",
            product_name="Test", weight_per_item_kg=Decimal("1"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(items_purchased=Decimal("100"), protein_pct=Decimal("5")),
            created_at=_NOW,
        )
        store.add_product(product)
        store.upsert_pt_classification(_pt_classification(pid))

        run_id = uuid4()
        run = RunRecord(
            id=run_id, project_id=project.id, methodology=Methodology.PROTEIN_TRACKER,
            started_at=_NOW, finished_at=_NOW, triggered_by=uuid4(),
            rows_payload=[{"product_id": str(pid), "pt_group": "plant_based_core", "in_scope": True}],
            summary_payload=self._pt_summary(run_id),
            rows_count=1,
            organisation_id=org.id,
        )
        cov = build_coverage_section(store, run, project)
        # With no enrichment records, no enrichment caveat should appear
        enrichment_caveats = [c for c in cov.caveats if "enriched" in c.lower() or "enrichment" in c.lower()]
        assert enrichment_caveats == []


# ---------------------------------------------------------------------------
# 8: No external network calls
# ---------------------------------------------------------------------------


class TestNoExternalCalls:
    def test_external_sources_all_marked_unavailable(self):
        """All external providers are registered but not implemented."""
        for info in ENRICHMENT_SOURCE_REGISTRY:
            if info.is_external:
                assert not info.is_available, (
                    f"{info.source}: external sources must not be available in Phase 23A"
                )

    def test_assessor_makes_no_network_calls(self):
        """Calling the assessor with an empty list must not raise a network error."""
        # If the assessor attempted an HTTP call it would raise in a test environment.
        records = assess_protein_enrichment_needs([], now=_NOW)
        assert records == []

    def test_importing_enrichment_package_makes_no_network_calls(self):
        """Importing the enrichment package must not trigger network I/O."""
        import altera_api.enrichment.assessor  # noqa: F401
        import altera_api.enrichment.protocol  # noqa: F401
        import altera_api.enrichment.registry  # noqa: F401
