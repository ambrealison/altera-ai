"""Phase 24A — WWF Step 2 ingredient upload and validation tests.

Tests:
- Valid JSON file accepted and stored
- Unknown parent product rejected
- Branded composite rejected (warning, not stored)
- Non-composite parent rejected
- Invalid food group rejected
- Invalid FG1 subgroup rejected
- Missing FG1 subgroup rejected
- Invalid FG2 subgroup rejected
- Negative weight rejected
- Zero weight rejected
- Residual weight calculated correctly
- Ingredient sum exceeding product weight produces warning
- Cross-org access blocked
- GMS client can upload for own project
- Stored ingredients can be listed via API
- WWF calculation uses stored ingredients
- FG3..FG6 subgroup in JSON accepted and ignored
- Product without classification rejected
- Non-WWF project returns 422
"""

from __future__ import annotations

import io
import json
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
from altera_api.calculation.wwf import WWFRunVersions
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    ClientRole,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, RetailChannel, WWFProductFields
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.ingestion.wwf_step2 import validate_wwf_step2_json
from altera_api.main import app

_NOW = datetime.now(UTC)

_WWF_VERSIONS = WWFRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


# ---------------------------------------------------------------------------
# Helpers
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


def _wwf_composite_product(
    project_id: UUID,
    org_id: UUID,
    *,
    external_id: str = "P001",
    is_own_brand: bool = True,
    weight_kg: Decimal = Decimal("0.4"),
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=external_id,
        product_name="Own Brand Lasagna",
        weight_per_item_kg=weight_kg,
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=Decimal("500"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=is_own_brand,
        ),
        is_own_brand=is_own_brand,
        created_at=_NOW,
    )


def _wwf_non_composite_product(
    project_id: UUID,
    org_id: UUID,
    *,
    external_id: str = "P002",
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=2,
        external_product_id=external_id,
        product_name="Beef Mince",
        weight_per_item_kg=Decimal("0.5"),
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=Decimal("1000"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=False,
        ),
        is_own_brand=False,
        created_at=_NOW,
    )


def _composite_classification(
    product_id: UUID,
    *,
    is_own_brand: bool = True,
) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=WWFFoodGroup.FG1,
        wwf_is_composite=True,
        fg1_subgroup=WWFFG1Subgroup.POULTRY,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="r001",
        updated_at=_NOW,
    )


def _non_composite_classification(product_id: UUID) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=WWFFoodGroup.FG1,
        wwf_is_composite=False,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
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


def _setup_wwf_project(
    store: InMemoryStore,
) -> tuple[Organisation, object, NormalizedProduct]:
    org = _org(store)
    project = store.create_project(
        name="WWF Project",
        methodologies_enabled=frozenset({Methodology.WWF}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )
    product = _wwf_composite_product(project.id, org.id)
    store.add_product(product)
    clf = _composite_classification(product.id)
    store.upsert_wwf_classification(clf)
    return org, project, product


def _upload_json(
    client: TestClient,
    project_id: UUID,
    payload: dict,
    ctx: AuthContext,
) -> dict:
    app.dependency_overrides[authed_user] = lambda: ctx
    try:
        content = json.dumps(payload).encode()
        r = client.post(
            f"/api/v1/projects/{project_id}/wwf-ingredients/upload",
            files={"file": ("ingredients.json", io.BytesIO(content), "application/json")},
        )
    finally:
        app.dependency_overrides.pop(authed_user, None)
    return r


# ---------------------------------------------------------------------------
# Validator unit tests (pure, no HTTP)
# ---------------------------------------------------------------------------


class TestValidateWWFStep2Json:
    def _products_and_clf(
        self,
        *,
        is_own_brand: bool = True,
        is_composite: bool = True,
        weight_kg: Decimal = Decimal("0.4"),
    ) -> tuple[dict, dict]:
        project_id = uuid4()
        org_id = uuid4()
        ext_id = "P001"
        p = _wwf_composite_product(
            project_id, org_id, external_id=ext_id, is_own_brand=is_own_brand, weight_kg=weight_kg
        )
        if is_composite:
            clf = _composite_classification(p.id, is_own_brand=is_own_brand)
        else:
            clf = _non_composite_classification(p.id)
        return {ext_id: p}, {p.id: clf}

    def test_valid_fg1_ingredient_accepted(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        assert result.valid_product_count == 1
        assert result.error_count == 0
        pr = result.product_results[0]
        assert len(pr.valid_ingredients) == 1
        assert pr.valid_ingredients[0].food_group is WWFFoodGroup.FG1
        assert pr.valid_ingredients[0].fg1_subgroup is WWFFG1Subgroup.POULTRY

    def test_valid_fg2_ingredient_accepted(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG2", "subgroup": "cheese", "ingredient_weight_kg_per_item": 0.05}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg2_subgroup is WWFFG2Subgroup.CHEESE

    def test_valid_fg4_ingredient_accepted_no_subgroup(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.15}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].food_group is WWFFoodGroup.FG4
        assert pr.valid_ingredients[0].fg1_subgroup is None
        assert pr.valid_ingredients[0].fg2_subgroup is None

    def test_fg5_subgroup_accepted_but_ignored(self) -> None:
        """FG5 subgroup is accepted in input but not stored (no domain field)."""
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {
                        "food_group": "FG5",
                        "subgroup": "refined_grain",
                        "ingredient_weight_kg_per_item": 0.1,
                    }
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].food_group is WWFFoodGroup.FG5
        assert pr.valid_ingredients[0].fg1_subgroup is None
        assert pr.valid_ingredients[0].fg2_subgroup is None

    def test_unknown_product_rejected(self) -> None:
        products: dict = {}
        clfs: dict = {}
        raw = {
            "UNKNOWN_EXT": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        assert result.unknown_product_count == 1
        assert result.error_count == 1

    def test_branded_composite_gets_warning_not_error(self) -> None:
        products, clfs = self._products_and_clf(is_own_brand=False)
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        assert result.branded_composite_count == 1
        assert result.error_count == 0
        assert result.warning_count == 1
        pr = result.product_results[0]
        assert len(pr.valid_ingredients) == 0  # not stored for branded

    def test_non_composite_product_rejected(self) -> None:
        products, clfs = self._products_and_clf(is_composite=False)
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.2}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        assert result.non_composite_count == 1

    def test_invalid_food_group_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG8", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("food_group" in e.field for e in pr.errors)

    def test_fg7_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG7", "subgroup": "plant_based_snack", "ingredient_weight_kg_per_item": 0.05}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("FG7" in e.message for e in pr.errors)

    def test_invalid_fg1_subgroup_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "invalid_subgroup", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("subgroup" in e.field for e in pr.errors)

    def test_missing_fg1_subgroup_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid

    def test_missing_fg2_subgroup_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG2", "ingredient_weight_kg_per_item": 0.05}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid

    def test_negative_weight_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": -0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("greater than 0" in e.message for e in pr.errors)

    def test_zero_weight_rejected(self) -> None:
        products, clfs = self._products_and_clf()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid

    def test_residual_weight_calculated(self) -> None:
        products, clfs = self._products_and_clf(weight_kg=Decimal("0.4"))
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.07},
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.15},
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.total_attributed_weight_kg == Decimal("0.22")
        assert pr.residual_weight_kg == Decimal("0.4") - Decimal("0.22")

    def test_sum_exceeding_product_weight_is_warning(self) -> None:
        products, clfs = self._products_and_clf(weight_kg=Decimal("0.1"))
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.2}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid  # warning, not error
        assert result.warning_count > 0
        pr = result.product_results[0]
        assert any("exceeds product weight" in w for w in pr.warnings)

    def test_no_classification_rejected(self) -> None:
        project_id = uuid4()
        org_id = uuid4()
        p = _wwf_composite_product(project_id, org_id, external_id="P001")
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(
            raw,
            products_by_external_id={"P001": p},
            classifications={},
        )
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("classification" in e.field for e in pr.errors)

    def test_multiple_products_partial_errors(self) -> None:
        project_id = uuid4()
        org_id = uuid4()
        p1 = _wwf_composite_product(project_id, org_id, external_id="P001")
        p2 = _wwf_composite_product(project_id, org_id, external_id="P002")
        clf1 = _composite_classification(p1.id)
        clf2 = _composite_classification(p2.id)
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1}
                ]
            },
            "P002": {
                "ingredients": [
                    {"food_group": "FG1", "ingredient_weight_kg_per_item": 0.1}  # missing subgroup
                ]
            },
        }
        result = validate_wwf_step2_json(
            raw,
            products_by_external_id={"P001": p1, "P002": p2},
            classifications={p1.id: clf1, p2.id: clf2},
        )
        assert not result.is_valid
        assert result.error_count > 0
        assert result.valid_product_count == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestWWFStep2UploadEndpoint:
    def test_valid_file_returns_200_and_stored(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org, project, product = _setup_wwf_project(store)
        ctx = _altera_ctx(org.id)
        payload = {
            product.external_product_id: {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.07},
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.15},
                ]
            }
        }
        r = _upload_json(client, project.id, payload, ctx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] is True
        assert body["error_count"] == 0
        assert body["valid_product_count"] == 1

    def test_stored_ingredients_retrievable(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org, project, product = _setup_wwf_project(store)
        ctx = _altera_ctx(org.id)
        payload = {
            product.external_product_id: {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.07},
                ]
            }
        }
        _upload_json(client, project.id, payload, ctx)

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.get(f"/api/v1/projects/{project.id}/products/{product.id}/wwf-ingredients")
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body) == 1
        assert body[0]["food_group"] == "FG1"
        assert body[0]["fg1_subgroup"] == "poultry"
        assert body[0]["ingredient_weight_kg_per_item"] == "0.07"

    def test_unknown_product_returns_errors(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org, project, product = _setup_wwf_project(store)
        ctx = _altera_ctx(org.id)
        payload = {
            "DOES_NOT_EXIST": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        r = _upload_json(client, project.id, payload, ctx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] is False
        assert body["error_count"] > 0
        assert body["unknown_product_count"] == 1

    def test_branded_composite_warning(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        branded_product = _wwf_composite_product(project.id, org.id, is_own_brand=False)
        store.add_product(branded_product)
        clf = _composite_classification(branded_product.id, is_own_brand=False)
        store.upsert_wwf_classification(clf)

        ctx = _altera_ctx(org.id)
        payload = {
            branded_product.external_product_id: {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        r = _upload_json(client, project.id, payload, ctx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] is True  # warning only, no hard error
        assert body["branded_composite_count"] == 1
        assert body["warning_count"] > 0

    def test_invalid_json_returns_400(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org, project, product = _setup_wwf_project(store)
        ctx = _altera_ctx(org.id)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/wwf-ingredients/upload",
                files={"file": ("ingredients.json", b"not json at all", "application/json")},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)
        assert r.status_code == 400

    def test_non_wwf_project_returns_422(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org = _org(store)
        pt_project = store.create_project(
            name="PT only",
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        ctx = _altera_ctx(org.id)
        payload = {}
        r = _upload_json(client, pt_project.id, payload, ctx)
        assert r.status_code == 422

    def test_cross_org_access_blocked(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org_a = _org(store)
        org_b_id = uuid4()

        project_a = store.create_project(
            name="Org A Project",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org_a.id,
        )
        # Client from org_b trying to access org_a's project
        ctx_b = _client_ctx(org_b_id)
        payload = {}
        r = _upload_json(client, project_a.id, payload, ctx_b)
        assert r.status_code in (403, 404)

    def test_gms_client_can_upload_own_project(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """GMS clients can upload ingredient files for their own project."""
        org = Organisation(
            id=uuid4(),
            name="Retailer Org",
            slug="retailer-org",
            organisation_type=OrganisationType.GMS_CLIENT,
            created_at=_NOW,
        )
        user = UserProfile(
            user_id=store.default_user_id,
            email="client@retailer.example",
            display_name="Client User",
            organisation_id=org.id,
            role=ClientRole.CLIENT_OWNER,
            created_at=_NOW,
        )
        store.upsert_user(user)

        project = store.create_project(
            name="Client WWF",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        product = _wwf_composite_product(project.id, org.id)
        store.add_product(product)
        clf = _composite_classification(product.id)
        store.upsert_wwf_classification(clf)

        ctx = _client_ctx(org.id)
        payload = {
            product.external_product_id: {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.2}
                ]
            }
        }
        r = _upload_json(client, project.id, payload, ctx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] is True


# ---------------------------------------------------------------------------
# WWF calculation integration
# ---------------------------------------------------------------------------


class TestWWFCalculationWithStoredIngredients:
    def test_stored_ingredients_used_in_calculation(self) -> None:
        """Ingredients stored via upsert_wwf_ingredients_for_product feed into the run."""
        store = InMemoryStore()
        org, project, product = _setup_wwf_project(store)

        from altera_api.domain.wwf import WWFCompositeIngredient

        ingredients = [
            WWFCompositeIngredient(
                id=uuid4(),
                parent_product_id=product.id,
                food_group=WWFFoodGroup.FG1,
                fg1_subgroup=WWFFG1Subgroup.POULTRY,
                ingredient_weight_kg_per_item=Decimal("0.07"),
            ),
            WWFCompositeIngredient(
                id=uuid4(),
                parent_product_id=product.id,
                food_group=WWFFoodGroup.FG4,
                ingredient_weight_kg_per_item=Decimal("0.15"),
            ),
        ]
        store.upsert_wwf_ingredients_for_product(product.id, ingredients)

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.WWF,
            triggered_by=uuid4(),
        )
        # With ingredients the calculation should include FG1/FG4 contributions
        summary_payload = record.summary_payload
        fg_weights = {
            agg["food_group"]: Decimal(str(agg["weight_kg"]))
            for agg in summary_payload.get("per_food_group", [])
        }
        # FG1: 0.07 kg/item × 500 items = 35 kg
        assert fg_weights.get("FG1", Decimal("0")) > Decimal("0")

    def test_without_ingredients_no_step2_contribution(self) -> None:
        """Without stored ingredients, run produces Step 1 only (composite bucket)."""
        store = InMemoryStore()
        org, project, product = _setup_wwf_project(store)

        from altera_api.api.orchestrator import run_calculation

        record = run_calculation(
            store,
            project=project,
            methodology=Methodology.WWF,
            triggered_by=uuid4(),
        )
        summary_payload = record.summary_payload
        # Composite weight should be attributed to step 1 bucket
        assert Decimal(str(summary_payload["composites_total_weight_kg"])) > Decimal("0")
