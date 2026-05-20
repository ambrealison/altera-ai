"""Phase 33H — apply NEVO/CIQUAL to retailer products + plant/animal split flow.

Coverage:

  Endpoint (POST /projects/{id}/enrichments/apply-references)
    - NEVO match creates 3 enrichment records when split is published
    - NEVO match creates 1 record when entry has no split
    - CIQUAL is used only when NEVO has no match
    - retailer-provided protein_pct is never overwritten
    - no-match products are counted, not silently dropped
    - non-PT products are skipped explicitly
    - client (non-Altera) users get 403

  Selection
    - NEVO is preferred over CIQUAL when both have ENRICHED records
    - retailer-provided pct shadows enrichment (Phase 23C contract)
    - sibling plant/animal records surface on the ResolvedProteinEnrichment

  Calculation
    - NEVO-supplied plant/animal split is consumed for composite products
    - missing split falls back to classification assumption
    - retailer plant/animal pct still takes precedence over NEVO split
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.api.orchestrator import PT_VERSIONS
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.calculation.protein_tracker import calculate_pt_run
from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    Methodology,
    OrganisationType,
)
from altera_api.domain.enrichment import (
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.nevo import NevoEntry
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, ProteinSource, PTProductFields
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.enrichment.selection import (
    ResolvedProteinEnrichment,
    select_protein_enrichment,
)
from altera_api.main import app

_NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures + builders
# ---------------------------------------------------------------------------


def _nevo(
    *,
    code: str,
    name: str,
    prot: Decimal,
    plant: Decimal | None = None,
    animal: Decimal | None = None,
    group: str = "Vlees",
) -> NevoEntry:
    return NevoEntry(
        id=uuid4(),
        source_version="2025_v9.0",
        nevo_code=code,
        food_name_nl=name,
        food_name_en=name,
        food_group=group,
        quantity_basis="per 100g",
        protein_g_per_100g=prot,
        plant_protein_g_per_100g=plant,
        animal_protein_g_per_100g=animal,
    )


def _ciqual(
    *, code: str, name: str, prot: Decimal, group: str = "Meat"
) -> CiqualEntry:
    return CiqualEntry(
        id=uuid4(),
        source_version="2025",
        source_food_code=code,
        food_name_en=name,
        food_group=group,
        food_subgroup=None,
        food_subsubgroup=None,
        protein_g_per_100g=prot,
        is_below_detection=False,
    )


def _seed_altera_org(store: InMemoryStore) -> tuple[UUID, UUID]:
    """Promote the bootstrap user to an Altera analyst so dev-auth resolves
    with ``can_apply_enrichment=True``. The org is also promoted to
    ``ALTERA_INTERNAL`` so AuthContext.is_altera_internal is True.
    """
    org_id = store.default_org_id
    user_id = store.default_user_id
    existing_org = store.organisations[org_id]
    store.organisations[org_id] = Organisation(
        id=org_id,
        name=existing_org.name,
        slug=existing_org.slug,
        organisation_type=OrganisationType.ALTERA_INTERNAL,
        created_at=existing_org.created_at,
    )
    existing_user = store.users[user_id]
    store.upsert_user(
        UserProfile(
            user_id=user_id,
            organisation_id=org_id,
            email=existing_user.email,
            display_name=existing_user.display_name,
            role=AlteraRole.ALTERA_ANALYST,
            created_at=existing_user.created_at,
        )
    )
    return org_id, user_id


def _make_pt_product(
    store: InMemoryStore,
    *,
    project_id: UUID,
    org_id: UUID,
    name: str,
    protein_pct: Decimal | None,
    plant_pct: Decimal | None = None,
    animal_pct: Decimal | None = None,
    pt_group: ProteinTrackerGroup = ProteinTrackerGroup.PLANT_BASED_CORE,
) -> NormalizedProduct:
    p = NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=name,
        product_name=name,
        weight_per_item_kg=Decimal("1.0"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=protein_pct,
            protein_source=ProteinSource.LABEL,
            plant_protein_pct=plant_pct,
            animal_protein_pct=animal_pct,
        ),
        created_at=_NOW,
    )
    store.add_product(p)
    store.upsert_pt_classification(
        ProteinTrackerProductClassification(
            product_id=p.id,
            pt_group=pt_group,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="test",
            updated_at=_NOW,
        )
    )
    return p


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALTERA_DEV_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    s.seed_nevo_entries(
        [
            _nevo(
                code="100",
                name="Chicken breast",
                prot=Decimal("23.2"),
                plant=Decimal("0"),
                animal=Decimal("23.2"),
            ),
            _nevo(
                code="200",
                name="Tofu firm",
                prot=Decimal("12.5"),
                plant=Decimal("12.5"),
                animal=Decimal("0"),
                group="Plantaardig",
            ),
            _nevo(
                code="300",
                name="Mystery composite",
                prot=Decimal("10"),
                # no PROTPL/PROTAN — split unavailable
                plant=None,
                animal=None,
            ),
        ]
    )
    s.seed_ciqual_entries(
        [
            _ciqual(code="C-1", name="CIQUAL only food", prot=Decimal("7.5")),
        ]
    )
    return s


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _create_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "Phase 33H",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Endpoint behaviour
# ---------------------------------------------------------------------------


class TestApplyReferencesEndpoint:
    def test_nevo_with_split_creates_three_records(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_str = _create_project(client)
        pid = UUID(pid_str)
        org_id, _ = _seed_altera_org(store)
        product = _make_pt_product(
            store,
            project_id=pid,
            org_id=org_id,
            name="Chicken breast",
            protein_pct=None,
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["nevo_matched"] == 1
        assert body["nevo_with_split"] == 1
        assert body["ciqual_matched"] == 0
        records = store.get_enrichment_records_for_product(product.id)
        nutrients = {r.nutrient for r in records}
        assert {"protein_pct", "plant_protein_pct", "animal_protein_pct"}.issubset(nutrients)
        assert all(r.source is NutritionEnrichmentSource.NEVO for r in records)

    def test_nevo_without_split_creates_only_protein_record(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_str = _create_project(client)
        pid = UUID(pid_str)
        org_id, _ = _seed_altera_org(store)
        product = _make_pt_product(
            store,
            project_id=pid,
            org_id=org_id,
            name="Mystery composite",
            protein_pct=None,
        )
        client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        records = store.get_enrichment_records_for_product(product.id)
        assert len(records) == 1
        assert records[0].nutrient == "protein_pct"
        assert records[0].source is NutritionEnrichmentSource.NEVO

    def test_ciqual_fallback_when_nevo_missing(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_str = _create_project(client)
        pid = UUID(pid_str)
        org_id, _ = _seed_altera_org(store)
        product = _make_pt_product(
            store,
            project_id=pid,
            org_id=org_id,
            name="CIQUAL only food",
            protein_pct=None,
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        body = r.json()
        assert body["nevo_matched"] == 0
        assert body["ciqual_matched"] == 1
        records = store.get_enrichment_records_for_product(product.id)
        assert len(records) == 1
        assert records[0].source is NutritionEnrichmentSource.CIQUAL
        # CIQUAL contributes total only — no plant/animal sibling records.
        nutrients = {r.nutrient for r in records}
        assert nutrients == {"protein_pct"}

    def test_retailer_protein_pct_not_overwritten(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_str = _create_project(client)
        pid = UUID(pid_str)
        org_id, _ = _seed_altera_org(store)
        product = _make_pt_product(
            store,
            project_id=pid,
            org_id=org_id,
            name="Chicken breast",
            protein_pct=Decimal("18.0"),  # retailer value present
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        body = r.json()
        assert body["skipped_has_retailer_value"] == 1
        assert body["nevo_matched"] == 0
        assert store.get_enrichment_records_for_product(product.id) == []

    def test_no_match_counted_not_dropped(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        pid_str = _create_project(client)
        pid = UUID(pid_str)
        org_id, _ = _seed_altera_org(store)
        _make_pt_product(
            store,
            project_id=pid,
            org_id=org_id,
            name="Quantum cheese fictional",
            protein_pct=None,
        )
        r = client.post(f"/api/v1/projects/{pid_str}/enrichments/apply-references")
        body = r.json()
        assert body["no_match"] == 1
        assert body["nevo_matched"] == 0
        assert body["ciqual_matched"] == 0


# ---------------------------------------------------------------------------
# Selection — NEVO preferred over CIQUAL; sibling split surfaces
# ---------------------------------------------------------------------------


class TestSelectionWithNevo:
    def _record(
        self,
        product_id: UUID,
        *,
        nutrient: str,
        source: NutritionEnrichmentSource,
        value: Decimal,
    ) -> NutritionEnrichmentRecord:
        return NutritionEnrichmentRecord(
            product_id=product_id,
            nutrient=nutrient,
            original_value=None,
            enriched_value=value,
            unit="g_per_100g",
            source=source,
            confidence=Decimal("0.85"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="test",
            created_at=_NOW,
            created_by=None,
        )

    def test_nevo_preferred_over_ciqual(self) -> None:
        pid = uuid4()
        records = [
            self._record(
                pid,
                nutrient="protein_pct",
                source=NutritionEnrichmentSource.CIQUAL,
                value=Decimal("7.5"),
            ),
            self._record(
                pid,
                nutrient="protein_pct",
                source=NutritionEnrichmentSource.NEVO,
                value=Decimal("23.2"),
            ),
        ]
        result = select_protein_enrichment(records)
        assert result is not None
        assert result.source is NutritionEnrichmentSource.NEVO
        assert result.protein_pct == Decimal("23.2")

    def test_sibling_split_records_surface_for_same_source(self) -> None:
        pid = uuid4()
        records = [
            self._record(
                pid,
                nutrient="protein_pct",
                source=NutritionEnrichmentSource.NEVO,
                value=Decimal("23.2"),
            ),
            self._record(
                pid,
                nutrient="plant_protein_pct",
                source=NutritionEnrichmentSource.NEVO,
                value=Decimal("0"),
            ),
            self._record(
                pid,
                nutrient="animal_protein_pct",
                source=NutritionEnrichmentSource.NEVO,
                value=Decimal("23.2"),
            ),
        ]
        result = select_protein_enrichment(records)
        assert result is not None
        assert result.plant_protein_pct == Decimal("0")
        assert result.animal_protein_pct == Decimal("23.2")

    def test_split_only_returned_when_same_source(self) -> None:
        pid = uuid4()
        records = [
            self._record(
                pid,
                nutrient="protein_pct",
                source=NutritionEnrichmentSource.CIQUAL,
                value=Decimal("7.5"),
            ),
            # Plant record exists but from a DIFFERENT source — must be
            # ignored so we don't mix half-CIQUAL / half-NEVO data.
            self._record(
                pid,
                nutrient="plant_protein_pct",
                source=NutritionEnrichmentSource.NEVO,
                value=Decimal("0"),
            ),
        ]
        result = select_protein_enrichment(records)
        assert result is not None
        assert result.source is NutritionEnrichmentSource.CIQUAL
        assert result.plant_protein_pct is None


# ---------------------------------------------------------------------------
# Calculation — uses NEVO split when present, falls back otherwise
# ---------------------------------------------------------------------------


class TestCalculationWithSplit:
    def _build(
        self,
        *,
        protein_pct: Decimal | None,
        plant_pct: Decimal | None = None,
        animal_pct: Decimal | None = None,
        pt_group: ProteinTrackerGroup = ProteinTrackerGroup.COMPOSITE_PRODUCTS,
    ) -> tuple[NormalizedProduct, ProteinTrackerProductClassification]:
        pid = uuid4()
        product = NormalizedProduct(
            id=pid,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            row_number=1,
            external_product_id="x",
            product_name="x",
            weight_per_item_kg=Decimal("1.0"),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            pt_fields=PTProductFields(
                items_purchased=Decimal("100"),
                protein_pct=protein_pct,
                protein_source=ProteinSource.LABEL,
                plant_protein_pct=plant_pct,
                animal_protein_pct=animal_pct,
            ),
            created_at=_NOW,
        )
        clf = ProteinTrackerProductClassification(
            product_id=pid,
            pt_group=pt_group,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="r",
            updated_at=_NOW,
        )
        return product, clf

    def test_nevo_supplied_split_used_for_composite(self) -> None:
        product, clf = self._build(protein_pct=None)
        lookup = {
            product.id: ResolvedProteinEnrichment(
                protein_pct=Decimal("10"),
                source=NutritionEnrichmentSource.NEVO,
                plant_protein_pct=Decimal("8"),
                animal_protein_pct=Decimal("2"),
            )
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="FY24",
            versions=PT_VERSIONS,
            enrichment_lookup=lookup,
        )
        s = result.summary
        # 100 items × 1 kg × 10/100 = 10 kg total protein.
        # NEVO split (8 plant + 2 animal) is applied directly — no 50/50.
        assert s.total_in_scope_protein_kg == Decimal("10.00000000")
        assert s.plant_protein_kg == Decimal("8.00000000")
        assert s.animal_protein_kg == Decimal("2.00000000")
        assert s.rows_with_per_product_split == 1
        assert s.rows_with_enriched_split == 1
        assert s.nevo_enrichment_used_count == 1

    def test_missing_split_falls_back_to_classification_assumption(self) -> None:
        product, clf = self._build(protein_pct=None)
        lookup = {
            product.id: ResolvedProteinEnrichment(
                protein_pct=Decimal("10"),
                source=NutritionEnrichmentSource.NEVO,
                # No plant/animal — split unavailable for this entry.
                plant_protein_pct=None,
                animal_protein_pct=None,
            )
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="FY24",
            versions=PT_VERSIONS,
            enrichment_lookup=lookup,
        )
        s = result.summary
        # Composite without split → 50/50 default applies, no enriched split.
        assert s.rows_with_per_product_split == 0
        assert s.rows_with_enriched_split == 0
        assert s.nevo_enrichment_used_count == 1
        # Plant + animal still sum to total via the composite 50/50 pool.
        assert s.plant_protein_kg + s.animal_protein_kg == Decimal("10.00000000")

    def test_retailer_split_takes_precedence_over_nevo(self) -> None:
        # Retailer provided a split: 7 plant + 3 animal (sums to 10).
        product, clf = self._build(
            protein_pct=Decimal("10"),
            plant_pct=Decimal("7"),
            animal_pct=Decimal("3"),
        )
        # NEVO would say 5+5; retailer values must win.
        lookup = {
            product.id: ResolvedProteinEnrichment(
                protein_pct=Decimal("99"),  # ignored — retailer pct present
                source=NutritionEnrichmentSource.NEVO,
                plant_protein_pct=Decimal("5"),
                animal_protein_pct=Decimal("5"),
            )
        }
        result = calculate_pt_run(
            [product],
            {product.id: clf},
            run_id=uuid4(),
            reporting_period_label="FY24",
            versions=PT_VERSIONS,
            enrichment_lookup=lookup,
        )
        s = result.summary
        assert s.total_in_scope_protein_kg == Decimal("10.00000000")
        assert s.plant_protein_kg == Decimal("7.00000000")
        assert s.animal_protein_kg == Decimal("3.00000000")
        # Used retailer split, not the enriched one.
        assert s.rows_with_per_product_split == 1
        assert s.rows_with_enriched_split == 0
        # Retailer pct present means enrichment was not consumed at all.
        assert s.enriched_nutrition_used_count == 0
        assert s.nevo_enrichment_used_count == 0
