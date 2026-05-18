"""Phase 24B — WWF Step 2 hardening tests.

Covers:
- File-size and row-count limits
- JSON shape validation (non-dict entry, missing ingredients key,
  non-list ingredients, empty list)
- Re-upload semantics (full project replacement; invalid upload keeps old data)
- Duplicate ingredient detection (warning, not error)
- FG3 subgroup support (valid accepted, invalid rejected, missing warns)
- FG5 grain-kind support (valid accepted, invalid rejected)
- Step 1 preservation when Step 2 ingredients are applied
- FG2 dairy-equivalent on Step 2 ingredients
- Whole-diet plant/animal split shifts with Step 2
- Branded composite ingredients not applied at calculation layer
- Residual / unattributed weight
- Report coverage caveats: Step 2 applied count + branded Step 1 count
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
from altera_api.calculation.wwf import WWFRunVersions, calculate_wwf_run
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, RetailChannel, WWFProductFields
from altera_api.domain.wwf import (
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.ingestion.wwf_step2 import (
    MAX_STEP2_INGREDIENT_ROWS,
    validate_wwf_step2_json,
)
from altera_api.main import app

_NOW = datetime.now(UTC)

_WWF_VERSIONS = WWFRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _org(store: InMemoryStore, *, altera: bool = True) -> Organisation:
    org = Organisation(
        id=uuid4(),
        name="Test Org",
        slug="test-org",
        organisation_type=(
            OrganisationType.ALTERA_INTERNAL if altera else OrganisationType.GMS_CLIENT
        ),
        created_at=_NOW,
    )
    user = UserProfile(
        user_id=store.default_user_id,
        email="test@example.com",
        display_name="Test User",
        organisation_id=org.id,
        role=AlteraRole.ALTERA_ANALYST if altera else AlteraRole.ALTERA_ANALYST,
        created_at=_NOW,
    )
    store.upsert_user(user)
    return org


def _product(
    project_id: UUID,
    org_id: UUID,
    *,
    ext_id: str = "P001",
    is_own_brand: bool = True,
    weight_kg: Decimal = Decimal("0.5"),
    items_sold: Decimal = Decimal("100"),
) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        upload_id=uuid4(),
        project_id=project_id,
        organisation_id=org_id,
        row_number=1,
        external_product_id=ext_id,
        product_name=f"Product {ext_id}",
        weight_per_item_kg=weight_kg,
        methodologies_enabled=frozenset({Methodology.WWF}),
        wwf_fields=WWFProductFields(
            items_sold=items_sold,
            retail_channel=RetailChannel.FRESH,
            is_own_brand=is_own_brand,
        ),
        is_own_brand=is_own_brand,
        created_at=_NOW,
    )


def _composite_clf(product_id: UUID) -> WWFProductClassification:
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


def _whole_clf(product_id: UUID, fg: WWFFoodGroup = WWFFoodGroup.FG1) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=fg,
        wwf_is_composite=False,
        fg1_subgroup=WWFFG1Subgroup.POULTRY if fg is WWFFoodGroup.FG1 else None,
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


def _upload(client: TestClient, project_id: UUID, payload: dict, ctx: AuthContext) -> dict:
    app.dependency_overrides[authed_user] = lambda: ctx
    try:
        content = json.dumps(payload).encode()
        r = client.post(
            f"/api/v1/projects/{project_id}/wwf-ingredients/upload",
            files={"file": ("ing.json", io.BytesIO(content), "application/json")},
        )
    finally:
        app.dependency_overrides.pop(authed_user, None)
    return r


def _setup(store: InMemoryStore) -> tuple[Organisation, object, NormalizedProduct]:
    org = _org(store)
    project = store.create_project(
        name="WWF Project",
        methodologies_enabled=frozenset({Methodology.WWF}),
        reporting_period_label="2024",
        organisation_id=org.id,
    )
    product = _product(project.id, org.id)
    store.add_product(product)
    store.upsert_wwf_classification(_composite_clf(product.id))
    return org, project, product


# ===========================================================================
# 1. File-size and row-count limits
# ===========================================================================


class TestFileSizeAndRowLimits:
    def test_oversized_file_rejected(self, client: TestClient, store: InMemoryStore) -> None:
        org, project, product = _setup(store)
        ctx = _altera_ctx(org.id)

        # Build a payload that exceeds 50 MB
        big_payload = b"x" * (50 * 1024 * 1024 + 1)
        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/wwf-ingredients/upload",
                files={"file": ("big.json", io.BytesIO(big_payload), "application/json")},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 413, r.text
        assert "50 MB" in r.json()["detail"]

    def test_row_count_limit_constant(self) -> None:
        """The constant must match the CSV pipeline limit."""
        assert MAX_STEP2_INGREDIENT_ROWS == 200_000

    def test_excessive_row_count_rejected(self, client: TestClient, store: InMemoryStore) -> None:
        org, project, product = _setup(store)
        ctx = _altera_ctx(org.id)

        # Build a file with 200_001 ingredient rows by using a product that doesn't
        # exist — we only care that the count guard fires before validation.
        many_ingredients = [
            {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.001}
            for _ in range(200_001)
        ]
        payload = {"P_FAKE": {"ingredients": many_ingredients}}
        content = json.dumps(payload).encode()

        app.dependency_overrides[authed_user] = lambda: ctx
        try:
            r = client.post(
                f"/api/v1/projects/{project.id}/wwf-ingredients/upload",
                files={"file": ("rows.json", io.BytesIO(content), "application/json")},
            )
        finally:
            app.dependency_overrides.pop(authed_user, None)

        assert r.status_code == 422, r.text
        assert "200,000" in r.json()["detail"]


# ===========================================================================
# 2. JSON shape validation
# ===========================================================================


class TestJsonShapeValidation:
    def _products_and_clfs(self) -> tuple[dict, dict]:
        project_id = uuid4()
        org_id = uuid4()
        p = _product(project_id, org_id, ext_id="P001")
        return {"P001": p}, {p.id: _composite_clf(p.id)}

    def test_non_dict_product_entry_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": ["not", "an", "object"]}
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("must be a JSON object" in e.message for e in pr.errors)

    def test_missing_ingredients_key_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": {"food_group": "FG1"}}  # no 'ingredients' key
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("missing required 'ingredients' key" in e.message for e in pr.errors)

    def test_ingredients_not_list_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": {"ingredients": "FG1"}}  # string, not list
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("must be a list" in e.message for e in pr.errors)

    def test_empty_ingredients_list_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": {"ingredients": []}}
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("must not be empty" in e.message for e in pr.errors)

    def test_empty_list_not_counted_as_valid(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": {"ingredients": []}}
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.valid_product_count == 0

    def test_non_dict_ingredient_row_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {"P001": {"ingredients": ["not-a-dict"]}}
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("must be a JSON object" in e.message for e in pr.errors)


# ===========================================================================
# 3. Re-upload semantics
# ===========================================================================


class TestReuploadSemantics:
    def test_second_upload_replaces_first(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        """Products in first upload but absent from second upload have their
        ingredients cleared on success of the second upload."""
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        p_a = _product(project.id, org.id, ext_id="A")
        p_b = _product(project.id, org.id, ext_id="B")
        store.add_product(p_a)
        store.add_product(p_b)
        store.upsert_wwf_classification(_composite_clf(p_a.id))
        store.upsert_wwf_classification(_composite_clf(p_b.id))

        ctx = _altera_ctx(org.id)

        # First upload: A + B
        payload_1 = {
            "A": {"ingredients": [{"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1}]},
            "B": {"ingredients": [{"food_group": "FG4", "ingredient_weight_kg_per_item": 0.2}]},
        }
        r1 = _upload(client, project.id, payload_1, ctx)
        assert r1.status_code == 200
        assert r1.json()["stored"] is True
        assert r1.json()["replaced"] is False  # nothing existed before

        # Second upload: A only
        payload_2 = {
            "A": {"ingredients": [{"food_group": "FG4", "ingredient_weight_kg_per_item": 0.3}]},
        }
        r2 = _upload(client, project.id, payload_2, ctx)
        assert r2.status_code == 200
        assert r2.json()["stored"] is True
        assert r2.json()["replaced"] is True  # replaced previous data

        # B's ingredients should be gone
        assert store.get_wwf_ingredients_for_product(p_b.id) == []
        # A's new ingredient should be stored
        ings_a = store.get_wwf_ingredients_for_product(p_a.id)
        assert len(ings_a) == 1
        assert ings_a[0].ingredient_weight_kg_per_item == Decimal("0.3")

    def test_invalid_upload_does_not_clear_old_records(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        org, project, product = _setup(store)
        ctx = _altera_ctx(org.id)

        # First (valid) upload
        payload_1 = {
            product.external_product_id: {
                "ingredients": [{"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1}]
            }
        }
        r1 = _upload(client, project.id, payload_1, ctx)
        assert r1.json()["stored"] is True
        original_ings = store.get_wwf_ingredients_for_product(product.id)
        assert len(original_ings) == 1

        # Second (invalid) upload: unknown product causes error
        payload_2 = {"UNKNOWN": {"ingredients": [{"food_group": "FG4", "ingredient_weight_kg_per_item": 0.2}]}}
        r2 = _upload(client, project.id, payload_2, ctx)
        assert r2.json()["stored"] is False

        # Old records preserved
        assert store.get_wwf_ingredients_for_product(product.id) == original_ings


# ===========================================================================
# 4. Duplicate ingredient detection
# ===========================================================================


class TestDuplicateIngredientDetection:
    def _products_and_clfs(self) -> tuple[dict, dict]:
        project_id = uuid4()
        org_id = uuid4()
        p = _product(project_id, org_id, ext_id="P001")
        return {"P001": p}, {p.id: _composite_clf(p.id)}

    def test_duplicate_fg4_produces_warning_not_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.1},
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.05},
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid  # warning, not error
        pr = result.product_results[0]
        assert len(pr.valid_ingredients) == 2  # both stored
        assert any("duplicate" in w.lower() for w in pr.warnings)

    def test_duplicate_fg1_subgroup_produces_warning(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1},
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.05},
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert any("duplicate" in w.lower() for w in pr.warnings)

    def test_different_fg1_subgroups_no_warning(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.1},
                    {"food_group": "FG1", "subgroup": "red_meat", "ingredient_weight_kg_per_item": 0.05},
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert not any("duplicate" in w.lower() for w in pr.warnings)


# ===========================================================================
# 5. FG3 and FG5 ingredient dimensions
# ===========================================================================


class TestFG3FG5Support:
    def _products_and_clfs(self) -> tuple[dict, dict]:
        project_id = uuid4()
        org_id = uuid4()
        p = _product(project_id, org_id, ext_id="P001")
        return {"P001": p}, {p.id: _composite_clf(p.id)}

    def test_fg3_plant_based_fat_accepted(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG3", "subgroup": "plant_based_fat", "ingredient_weight_kg_per_item": 0.02}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg3_subgroup is WWFFG3Subgroup.PLANT_BASED_FAT

    def test_fg3_animal_based_fat_accepted(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG3", "subgroup": "animal_based_fat", "ingredient_weight_kg_per_item": 0.03}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg3_subgroup is WWFFG3Subgroup.ANIMAL_BASED_FAT

    def test_fg3_invalid_subgroup_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG3", "subgroup": "invalid_fat", "ingredient_weight_kg_per_item": 0.02}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid
        pr = result.product_results[0]
        assert any("subgroup" in e.field for e in pr.errors)

    def test_fg3_missing_subgroup_produces_warning(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG3", "ingredient_weight_kg_per_item": 0.02}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid  # warning not error
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg3_subgroup is None
        assert any("FG3" in w and "subgroup" in w for w in pr.warnings)

    def test_fg5_whole_grain_accepted(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG5", "subgroup": "whole_grain", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_fg5_refined_grain_accepted(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG5", "subgroup": "refined_grain", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        assert pr.valid_ingredients[0].fg5_grain_kind is WWFFG5GrainKind.REFINED_GRAIN

    def test_fg5_invalid_grain_kind_is_error(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG5", "subgroup": "sprouted", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert not result.is_valid

    def test_fg5_missing_grain_kind_no_warning(self) -> None:
        products, clfs = self._products_and_clfs()
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG5", "ingredient_weight_kg_per_item": 0.1}
                ]
            }
        }
        result = validate_wwf_step2_json(raw, products_by_external_id=products, classifications=clfs)
        assert result.is_valid
        pr = result.product_results[0]
        # No warning for missing FG5 grain kind (informational only)
        assert not any("FG5" in w for w in pr.warnings)


# ===========================================================================
# 6. Calculation correctness
# ===========================================================================


class TestCalculationCorrectness:
    def _build_run(
        self,
        store: InMemoryStore,
        *,
        with_ingredients: bool = False,
        fg2_ingredient: bool = False,
        fg3_plant: bool = False,
        branded_has_ingredients: bool = False,
    ) -> object:
        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        p_own = _product(project.id, org.id, ext_id="OWN", is_own_brand=True,
                         weight_kg=Decimal("0.5"), items_sold=Decimal("100"))
        store.add_product(p_own)
        store.upsert_wwf_classification(_composite_clf(p_own.id))

        ingredients: list[WWFCompositeIngredient] = []
        if with_ingredients:
            ingredients.append(
                WWFCompositeIngredient(
                    id=uuid4(),
                    parent_product_id=p_own.id,
                    food_group=WWFFoodGroup.FG1,
                    fg1_subgroup=WWFFG1Subgroup.POULTRY,
                    ingredient_weight_kg_per_item=Decimal("0.07"),
                )
            )
            ingredients.append(
                WWFCompositeIngredient(
                    id=uuid4(),
                    parent_product_id=p_own.id,
                    food_group=WWFFoodGroup.FG4,
                    ingredient_weight_kg_per_item=Decimal("0.15"),
                )
            )
        if fg2_ingredient:
            ingredients.append(
                WWFCompositeIngredient(
                    id=uuid4(),
                    parent_product_id=p_own.id,
                    food_group=WWFFoodGroup.FG2,
                    fg2_subgroup=WWFFG2Subgroup.CHEESE,
                    ingredient_weight_kg_per_item=Decimal("0.05"),
                )
            )
        if fg3_plant:
            ingredients.append(
                WWFCompositeIngredient(
                    id=uuid4(),
                    parent_product_id=p_own.id,
                    food_group=WWFFoodGroup.FG3,
                    fg3_subgroup=WWFFG3Subgroup.PLANT_BASED_FAT,
                    ingredient_weight_kg_per_item=Decimal("0.03"),
                )
            )

        if ingredients:
            store.upsert_wwf_ingredients_for_product(p_own.id, ingredients)

        # Branded composite (always present to test Step 1 isolation)
        p_branded = _product(project.id, org.id, ext_id="BRANDED", is_own_brand=False,
                              weight_kg=Decimal("0.4"), items_sold=Decimal("50"))
        store.add_product(p_branded)
        clf_branded = WWFProductClassification(
            product_id=p_branded.id,
            wwf_food_group=WWFFoodGroup.FG1,
            wwf_is_composite=True,
            fg1_subgroup=WWFFG1Subgroup.POULTRY,
            composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="r001",
            updated_at=_NOW,
        )
        store.upsert_wwf_classification(clf_branded)

        if branded_has_ingredients:
            # Even if branded has stored ingredients, they should NOT be applied
            store.upsert_wwf_ingredients_for_product(
                p_branded.id,
                [
                    WWFCompositeIngredient(
                        id=uuid4(),
                        parent_product_id=p_branded.id,
                        food_group=WWFFoodGroup.FG4,
                        ingredient_weight_kg_per_item=Decimal("0.2"),
                    )
                ],
            )

        products = store.list_products_for_project(project.id)
        clfs = {p.id: store.get_wwf_classification(p.id) for p in products}
        clfs = {k: v for k, v in clfs.items() if v is not None}
        ingredients_by_product = store.get_wwf_ingredients_by_project(project.id)

        return calculate_wwf_run(
            products,
            clfs,
            run_id=uuid4(),
            reporting_period_label="2024",
            versions=_WWF_VERSIONS,
            ingredients_by_product=ingredients_by_product or None,
        )

    def test_step1_bucket_preserved_with_step2(self) -> None:
        """composites_total_weight_kg must still reflect Step 1 bucket totals."""
        result = self._build_run(InMemoryStore(), with_ingredients=True)
        s = result.summary
        # OWN: 0.5 kg * 100 = 50 kg; BRANDED: 0.4 * 50 = 20 kg → total = 70 kg
        assert s.composites_total_weight_kg == Decimal("70")
        assert s.composites_meat_based_kg == Decimal("70")

    def test_step2_ingredients_contribute_to_fg_aggregates(self) -> None:
        """Own-brand ingredient weights must show up in per-FG aggregates."""
        result = self._build_run(InMemoryStore(), with_ingredients=True)
        s = result.summary
        fg1 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG1)
        fg4 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG4)
        # FG1: 0.07 * 100 = 7 kg from ingredients
        assert fg1.weight_kg == Decimal("7")
        # FG4: 0.15 * 100 = 15 kg
        assert fg4.weight_kg == Decimal("15")

    def test_fg2_dairy_equiv_applied_on_step2_ingredient(self) -> None:
        """Cheese ingredient (×10 factor) must flow into dairy-equiv totals."""
        result = self._build_run(InMemoryStore(), fg2_ingredient=True)
        s = result.summary
        fg2 = next(a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG2)
        # 0.05 kg * 100 items = 5 kg raw; 5 * 10 = 50 kg dairy equiv
        assert fg2.weight_kg == Decimal("5")
        assert fg2.weight_kg_dairy_equiv == Decimal("50")

    def test_whole_diet_plant_increases_with_fg4_ingredient(self) -> None:
        """FG4 plant ingredient must increase whole-diet plant weight."""
        result_no_ing = self._build_run(InMemoryStore(), with_ingredients=False)
        result_with_ing = self._build_run(InMemoryStore(), with_ingredients=True)
        # Without ingredients no FG agg contributions from the composite
        assert result_with_ing.summary.whole_diet_plant_weight_kg > (
            result_no_ing.summary.whole_diet_plant_weight_kg
        )

    def test_fg3_plant_fat_contributes_to_plant_total(self) -> None:
        """FG3 plant_based_fat ingredient must add to whole-diet plant weight."""
        result_plain = self._build_run(InMemoryStore(), fg2_ingredient=False, fg3_plant=False)
        result_fg3 = self._build_run(InMemoryStore(), fg3_plant=True)
        assert result_fg3.summary.whole_diet_plant_weight_kg > (
            result_plain.summary.whole_diet_plant_weight_kg
        )

    def test_branded_composite_ingredients_not_applied(self) -> None:
        """Branded composite's stored ingredients must not contribute to FG aggregates."""
        result_no_extra = self._build_run(InMemoryStore(), branded_has_ingredients=False)
        result_branded_ings = self._build_run(InMemoryStore(), branded_has_ingredients=True)
        # FG4 from own-brand is 0 in both (no FG4 ingredient on own-brand here)
        fg4_no_extra = next(
            a for a in result_no_extra.summary.per_food_group if a.food_group is WWFFoodGroup.FG4
        )
        fg4_branded = next(
            a for a in result_branded_ings.summary.per_food_group if a.food_group is WWFFoodGroup.FG4
        )
        assert fg4_no_extra.weight_kg == fg4_branded.weight_kg  # unchanged

    def test_residual_weight_in_product_result(self) -> None:
        """Residual = product_weight − attributed weight; positive is expected."""
        project_id = uuid4()
        org_id = uuid4()
        p = _product(project_id, org_id, weight_kg=Decimal("0.5"))
        raw = {
            "P001": {
                "ingredients": [
                    {"food_group": "FG1", "subgroup": "poultry", "ingredient_weight_kg_per_item": 0.07},
                    {"food_group": "FG4", "ingredient_weight_kg_per_item": 0.15},
                ]
            }
        }
        result = validate_wwf_step2_json(
            raw,
            products_by_external_id={"P001": p},
            classifications={p.id: _composite_clf(p.id)},
        )
        pr = result.product_results[0]
        assert pr.residual_weight_kg == Decimal("0.5") - Decimal("0.22")
        assert pr.residual_weight_kg > Decimal("0")


# ===========================================================================
# 7. Report coverage caveats
# ===========================================================================


class TestCoverageCaveats:
    def _run_with_step2(
        self,
        store: InMemoryStore,
        *,
        include_branded: bool = True,
    ) -> tuple[object, object, object]:
        """Set up a project + run and return (store, project, run_record)."""
        from altera_api.api.orchestrator import run_calculation

        org = _org(store)
        project = store.create_project(
            name="p",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        p_own = _product(project.id, org.id, ext_id="OWN", is_own_brand=True)
        store.add_product(p_own)
        store.upsert_wwf_classification(_composite_clf(p_own.id))
        store.upsert_wwf_ingredients_for_product(
            p_own.id,
            [
                WWFCompositeIngredient(
                    id=uuid4(),
                    parent_product_id=p_own.id,
                    food_group=WWFFoodGroup.FG4,
                    ingredient_weight_kg_per_item=Decimal("0.2"),
                )
            ],
        )

        if include_branded:
            p_branded = _product(project.id, org.id, ext_id="BRANDED", is_own_brand=False)
            store.add_product(p_branded)
            store.upsert_wwf_classification(
                WWFProductClassification(
                    product_id=p_branded.id,
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_is_composite=True,
                    fg1_subgroup=WWFFG1Subgroup.POULTRY,
                    composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                    source=ClassificationSource.DETERMINISTIC,
                    confidence=Decimal("1"),
                    rule_id="r001",
                    updated_at=_NOW,
                )
            )

        record = run_calculation(
            store, project=project, methodology=Methodology.WWF, triggered_by=uuid4()
        )
        return store, project, record

    def test_step2_applied_count_in_caveats(self) -> None:
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        s, project, record = self._run_with_step2(store, include_branded=False)
        coverage = build_coverage_section(store, record, project)
        assert any("Step 2 ingredient attribution" in c for c in coverage.caveats)
        assert any("1 own-brand composite" in c for c in coverage.caveats)

    def test_branded_step1_count_in_caveats(self) -> None:
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        s, project, record = self._run_with_step2(store, include_branded=True)
        coverage = build_coverage_section(store, record, project)
        assert any("branded composite" in c and "Step 1" in c for c in coverage.caveats)

    def test_step1_caveat_still_present(self) -> None:
        """The Step 1 composite caveat must always appear when composites exist."""
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        s, project, record = self._run_with_step2(store, include_branded=True)
        coverage = build_coverage_section(store, record, project)
        assert any("Step 1" in c and "classified" in c for c in coverage.caveats)

    def test_no_commercial_data_in_caveats(self) -> None:
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        s, project, record = self._run_with_step2(store, include_branded=True)
        coverage = build_coverage_section(store, record, project)
        for caveat in coverage.caveats:
            for sensitive in ("revenue", "margin", "supplier", "contract", "profit"):
                assert sensitive not in caveat.lower()

    def test_no_step2_caveat_without_stored_ingredients(self) -> None:
        """When no Step 2 data is stored, the Step 2 caveat must be absent."""
        from altera_api.api.orchestrator import run_calculation
        from altera_api.exports.coverage import build_coverage_section

        store = InMemoryStore()
        org = _org(store)
        project = store.create_project(
            name="plain",
            methodologies_enabled=frozenset({Methodology.WWF}),
            reporting_period_label="2024",
            organisation_id=org.id,
        )
        p = _product(project.id, org.id, is_own_brand=True)
        store.add_product(p)
        store.upsert_wwf_classification(_composite_clf(p.id))

        record = run_calculation(
            store, project=project, methodology=Methodology.WWF, triggered_by=uuid4()
        )
        coverage = build_coverage_section(store, record, project)
        assert not any("Step 2 ingredient attribution" in c for c in coverage.caveats)
