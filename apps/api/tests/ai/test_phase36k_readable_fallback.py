"""Phase 36K — readable-name fallback + expanded PT guards.

A 100-product audit on production data (commit f835d9e) measured
~87–90% precision with ~7 readable products still ending as final
``unknown`` or ``failed``. The brief mandates that NO readable name
ever land as a final unknown — instead it should arrive at
``needs_review`` with a best-guess category and a clear rule id.

This module asserts the Phase 36K additions:

  A. ``classify_readable_fallback(name)`` returns a best-guess
     ``(pt_group, rule_id)`` for readable names, covering 17
     conservative families (non-food, sweetener, beverages,
     fruit drinks, sweet bakery, animal composites, sweet spreads,
     sorbet, savory snacks ±dairy/animal, plant condiments,
     culinary ingredients, animal prepared meals, plant-protein
     anchors, simple animal foods, vegetable preparations, generic
     plant food).

  B. The five new ``apply_pt_guards`` rules fire on the expected
     patterns:
       * non_food_out_of_scope
       * sorbet_non_core
       * savory_snack_non_core / savory_snack_with_dairy_or_animal
         _composite
       * plant_condiment_non_core
       * sweetener_out_of_scope

  C. End-to-end via ``batch_classify``: a model ``unknown`` on a
     readable name no longer becomes ``AINeedsReviewParseFailed``;
     instead it lands as ``AINeedsReviewLowConfidence`` with a
     fallback category, and the bundle counts the firing under
     ``guard_overrides_by_rule``.

  D. Truly unusable names continue to land at the legacy unknown
     safety net (``AINeedsReviewParseFailed``).

  E. Bundle and orchestrator non-regression: AIAccepted flow still
     works for clean classifications.

Out of scope:
  * Optional deterministic pre-classifier (kept disabled behind
    an env flag — the brief explicitly allows shipping the slot
    later).
  * NEVO matching / table perf / persistence schema changes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from altera_api.ai.batch_classifier import batch_classify
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
)
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.ai.pt_guards import (
    apply_pt_guards,
    classify_readable_fallback,
)
from altera_api.api.state import InMemoryStore
from altera_api.api.store_factory import get_store
from altera_api.domain.common import (
    AlteraRole,
    ClassificationSource,
    Methodology,
    OrganisationType,
)
from altera_api.domain.organisation import Organisation, UserProfile
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.main import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _promote(store: InMemoryStore) -> None:
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


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    _promote(s)
    return s


@pytest.fixture
def client(store: InMemoryStore) -> Iterator[TestClient]:
    app.dependency_overrides[get_store] = lambda: store
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_store, None)


def _cls(group: ProteinTrackerGroup) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=uuid4(),
        pt_group=group,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase36k-test",
        ai_model="phase36k-fake",
        updated_at=datetime.now(UTC),
    )


def _make_product(name: str) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        organisation_id=uuid4(),
        row_number=2,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("2.0")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A. classify_readable_fallback — direct unit tests per family.
# ---------------------------------------------------------------------------


class TestReadableFallbackPerFamily:
    @pytest.mark.parametrize(
        ("name", "expected_group", "expected_rule"),
        [
            # Non-food / household / pet-food → out_of_scope.
            ("Papier toilette ultra doux",
             ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_non_food"),
            ("Dentifrice menthe", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_non_food"),
            ("Couches bébé taille 4", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_non_food"),
            # Phase 36K2 — petfood is IN-scope. Generic petfood with
            # no explicit animal/plant anchor defaults to composite.
            ("Croquettes chat bio",
             ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_petfood_composite"),
            ("Pâtée chien adulte",
             ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_petfood_composite"),
            ("Lessive liquide", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_non_food"),
            # Sweetener → out_of_scope.
            ("Miel de Fleurs", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_sweetener"),
            ("Sucre Roux Bio", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_sweetener"),
            ("Sirop d'Agave", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_sweetener"),
            # Beverage OOS.
            ("Thé Glacé Citron Vert", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_beverage_oos"),
            ("Limonade Artisanale Multifruits",
             ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_beverage_oos"),
            ("Café Arabica Moulu", ProteinTrackerGroup.OUT_OF_SCOPE,
             "readable_fallback_beverage_oos"),
            # Fruit drink → non_core.
            ("Smoothie Pêche", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_fruit_drink"),
            ("Pur Jus d'Orange", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_fruit_drink"),
            # Sweet bakery → composite.
            ("Sablés Noisette", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_sweet_bakery"),
            ("Croissants Maïs", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_sweet_bakery"),
            ("Tablette Lait", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_sweet_bakery"),
            # Self-evident animal composite.
            ("Cassoulet Provençale", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_animal_composite"),
            ("Lasagne Bolognaise", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_animal_composite"),
            # Sweet spread.
            ("Confiture Abricot", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_sweet_spread"),
            ("Coulis Mangue", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_sweet_spread"),
            ("Compote Pomme Sans Sucre",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_sweet_spread"),
            # Sorbet.
            ("Sorbet Framboise", ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_sorbet"),
            # Savory snack with dairy → composite.
            ("Crackers Fromage", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "readable_fallback_savory_snack_composite"),
            # Savory snack plant-only → non_core.
            ("Biscuits Apéritif Romarin",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_savory_snack"),
            ("Chips Nature Bio",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_savory_snack"),
            # Plant condiment.
            ("Vinaigrette Balsamique",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_plant_condiment"),
            ("Moutarde à l'Ancienne",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_plant_condiment"),
            ("Sauce Tomate Basilic",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_plant_condiment"),
            # Culinary ingredient.
            ("Farine de Blé T55",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_culinary_ingredient"),
            ("Levure Chimique",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_culinary_ingredient"),
            ("Bouillon Champignons",
             ProteinTrackerGroup.PLANT_BASED_NON_CORE,
             "readable_fallback_culinary_ingredient"),
            # Plant protein anchor.
            ("Lentilles Vertes du Puy",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "readable_fallback_plant_core"),
            ("Tofu Nature Bio", ProteinTrackerGroup.PLANT_BASED_CORE,
             "readable_fallback_plant_core"),
            # Simple animal food.
            ("Filets de Saumon Atlantique",
             ProteinTrackerGroup.ANIMAL_CORE,
             "readable_fallback_animal_simple"),
            ("Yaourt Nature 0% MG", ProteinTrackerGroup.ANIMAL_CORE,
             "readable_fallback_animal_simple"),
        ],
    )
    def test_fallback_picks_expected_family(
        self,
        name: str,
        expected_group: ProteinTrackerGroup,
        expected_rule: str,
    ) -> None:
        result = classify_readable_fallback(name)
        assert result is not None, (
            f"fallback returned None on readable name {name!r}"
        )
        group, rule = result
        assert group is expected_group, (
            f"{name!r}: expected {expected_group}, got {group} "
            f"(rule={rule})"
        )
        assert rule == expected_rule

    def test_unreadable_returns_none(self) -> None:
        # These are pure placeholder / corrupted names — the
        # fallback must NOT invent a category. The caller's
        # ``_is_unusable_name`` already filters them upstream, but
        # the fallback is also defensive.
        for name in ("", "  ", "\t\n", "Promotion Premium"):
            assert classify_readable_fallback(name) is None or (
                name == "Promotion Premium"
                # "Promotion Premium" carries neither a food nor a
                # non-food token; the fallback returns None.
            )


# ---------------------------------------------------------------------------
# B. New guards in apply_pt_guards fire on expected patterns.
# ---------------------------------------------------------------------------


def _apply(
    name: str, group: ProteinTrackerGroup
) -> tuple[ProteinTrackerGroup, str | None]:
    override = apply_pt_guards(name, _cls(group))
    if override is None:
        return group, None
    return override.new_classification.pt_group, override.rule


class TestNewGuardFamilies:
    @pytest.mark.parametrize(
        "name",
        [
            "Lessive Liquide Concentrée",
            "Dentifrice Menthe Fraîche",
            "Papier Toilette Ultra Doux",
            "Gel Douche Bio",
        ],
    )
    def test_non_food_guard_reroutes_to_out_of_scope(
        self, name: str
    ) -> None:
        # Household / hygiene / human-non-food: model said
        # plant_based_non_core, non-food guard overrides to oos.
        group, rule = _apply(name, ProteinTrackerGroup.PLANT_BASED_NON_CORE)
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule == "non_food_out_of_scope"

    @pytest.mark.parametrize(
        "name",
        [
            "Litière Chat Bio",
            "Jouet Chien Corde",
            "Sacs Déjections Chien",
        ],
    )
    def test_pet_accessory_guard_reroutes_to_out_of_scope(
        self, name: str
    ) -> None:
        # Pet accessories (litter / toy / poop bags) → out_of_scope,
        # using the dedicated pet_accessory_out_of_scope rule.
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule == "pet_accessory_out_of_scope"

    @pytest.mark.parametrize(
        ("name", "expected_group", "expected_rule"),
        [
            # Generic petfood — composite by default.
            ("Croquettes Chat",
             ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "petfood_generic_composite"),
            ("Pâtée Chien Adulte",
             ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "petfood_generic_composite"),
            # Petfood + explicit animal token → animal_core.
            ("Croquettes Chat Saumon",
             ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Pâtée Chien Bœuf",
             ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            # Petfood + plant-protein anchor → plant_based_core.
            ("Croquettes Chien Tofu",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "petfood_plant_protein_core"),
        ],
    )
    def test_petfood_guard_in_scope(
        self,
        name: str,
        expected_group: ProteinTrackerGroup,
        expected_rule: str,
    ) -> None:
        # The model returned plant_based_non_core (a typical wrong
        # guess on petfood); the new petfood guard reroutes it.
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is expected_group, (
            f"{name!r}: expected {expected_group}, got {group} "
            f"(rule={rule})"
        )
        assert rule == expected_rule

    @pytest.mark.parametrize(
        ("name", "expected_group", "expected_rule"),
        [
            ("Boisson Amande Sans Sucres",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "plant_milk_substitute_core"),
            ("Boisson Avoine Bio",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "plant_milk_substitute_core"),
            ("Boisson Soja Vanille",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "plant_milk_substitute_core"),
            ("Lait de Soja",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "plant_milk_substitute_core"),
            ("Lait d'Amande Sans Sucre",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "plant_milk_substitute_core"),
        ],
    )
    def test_plant_milk_substitute_guard(
        self,
        name: str,
        expected_group: ProteinTrackerGroup,
        expected_rule: str,
    ) -> None:
        # The model returned out_of_scope (mis-routed as a generic
        # beverage); the plant-milk guard reroutes to
        # plant_based_core.
        group, rule = _apply(name, ProteinTrackerGroup.OUT_OF_SCOPE)
        assert group is expected_group
        assert rule == expected_rule

    @pytest.mark.parametrize(
        "name",
        ["Sorbet Framboise", "Sorbet Citron Bio"],
    )
    def test_sorbet_guard_routes_to_non_core(self, name: str) -> None:
        # The bakery guard would otherwise route to composite via the
        # broad sweet-bakery patterns. Sorbet has no dairy.
        group, rule = _apply(name, ProteinTrackerGroup.COMPOSITE_PRODUCTS)
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert rule == "sorbet_non_core"

    def test_savory_snack_plant_only_non_core(self) -> None:
        group, rule = _apply(
            "Biscuits Apéritif Romarin",
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert rule == "savory_snack_non_core"

    def test_savory_snack_with_dairy_composite(self) -> None:
        group, rule = _apply(
            "Crackers Fromage",
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        )
        assert group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        assert rule == "savory_snack_with_dairy_or_animal_composite"

    @pytest.mark.parametrize(
        "name",
        [
            "Vinaigrette Balsamique",
            "Moutarde à l'Ancienne",
            "Sauce Tomate Basilic",
            "Ketchup Bio",
        ],
    )
    def test_plant_condiment_guard(self, name: str) -> None:
        group, rule = _apply(name, ProteinTrackerGroup.COMPOSITE_PRODUCTS)
        assert group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert rule == "plant_condiment_non_core"

    @pytest.mark.parametrize(
        "name",
        ["Miel de Fleurs", "Sucre Roux Bio", "Sirop d'Agave"],
    )
    def test_sweetener_guard(self, name: str) -> None:
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule == "sweetener_out_of_scope"


# ---------------------------------------------------------------------------
# C. End-to-end: model unknown + readable name → review with fallback.
# ---------------------------------------------------------------------------


class _UnknownProvider(ClassifierProvider):
    @property
    def model(self) -> str:
        return "phase36k-unknown"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        rows = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row:
                continue
            rows.append(
                {
                    "id": row["id"],
                    "pt_group": "unknown",
                    "confidence": 0.3,
                    "rationale": "phase36k fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="phase36k-unknown",
        )


class TestEndToEndReadableUnknownGetsFallback:
    def test_no_readable_product_ends_final_unknown_or_failed(
        self,
    ) -> None:
        """The brief's hard requirement: no readable name lands as
        final ``unknown`` / ``failed``. Each of these names goes
        through the model returning ``unknown`` and must come out
        as ``AINeedsReviewLowConfidence`` with a fallback category."""
        provider = _UnknownProvider()
        readable_names = [
            "Miel de Fleurs",
            "Confiture Abricot",
            "Papier toilette",
            "Dentifrice",
            "Couches bébé",
            "Croquettes chat",
            "Pâtée chien",
            "Levure chimique",
            "Thé glacé citron vert",
            "Limonade artisanale multifruits",
            "Sorbet Framboise",
            "Crackers Fromage",
            "Vinaigrette Balsamique",
            "Sablés Noisette",
            "Cassoulet Provençale",
        ]
        bundle = batch_classify(
            [_make_product(n) for n in readable_names],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # None of the verdicts should be AINeedsReviewParseFailed
        # — the fallback caught each readable name.
        failed = [
            v
            for v in bundle.verdicts
            if isinstance(v, AINeedsReviewParseFailed)
        ]
        assert not failed, (
            f"{len(failed)} readable names ended up as parse-failed: "
            f"{[type(v).__name__ for v in bundle.verdicts]}"
        )
        # None should be AIAccepted with pt_group=UNKNOWN either.
        for v in bundle.verdicts:
            if isinstance(v, AIAccepted):
                assert (
                    v.classification.pt_group
                    is not ProteinTrackerGroup.UNKNOWN
                )
        # Each fallback firing is counted in the bundle.
        assert sum(
            bundle.guard_overrides_by_rule.values()
        ) >= len(readable_names)
        # And the safety-net counter stayed zero because every
        # readable name was caught by the fallback.
        assert bundle.unknown_safety_net_total == 0

    def test_unusable_names_still_safety_netted(self) -> None:
        """Truly unusable names (empty / placeholder) still bypass
        the fallback and end at the legacy ``unknown`` →
        ``AINeedsReviewParseFailed`` path. The brief preserves this
        behaviour: ``unknown`` is reserved for unusable names."""
        provider = _UnknownProvider()
        # "Promotion Premium" has no food/non-food token and the
        # fallback returns None.
        products = [_make_product("Promotion Premium")]
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert bundle.unknown_safety_net_total == 1
        # And the verdict is the legacy parse-failed.
        assert isinstance(
            bundle.verdicts[0], AINeedsReviewParseFailed
        )


# ---------------------------------------------------------------------------
# D. Non-regression for accepted flow.
# ---------------------------------------------------------------------------


class TestNonRegressionAccepted:
    def test_clean_classification_still_accepted(self) -> None:
        """Confidence-≥-0.70 verdicts with no guard match still land
        as ``AIAccepted`` exactly like before Phase 36K."""

        class _CleanProvider(ClassifierProvider):
            @property
            def model(self) -> str:
                return "phase36k-clean"

            def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
                raise NotImplementedError

            def supports_batch(self) -> bool:
                return True

            def batch_classify(self, prompt: Any) -> ProviderResponse:
                rows = []
                for line in prompt.user_message.split("\n"):
                    if not line.startswith("{"):
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "id" not in row:
                        continue
                    rows.append(
                        {
                            "id": row["id"],
                            "pt_group": "plant_based_core",
                            "confidence": 0.95,
                            "rationale": "phase36k fake",
                        }
                    )
                return ProviderResponse(
                    raw_text=json.dumps({"results": rows}),
                    model="phase36k-clean",
                )

        bundle = batch_classify(
            [_make_product("Tofu Nature Bio")],
            _CleanProvider(),
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert any(
            isinstance(v, AIAccepted) for v in bundle.verdicts
        )
        assert bundle.guard_overrides_by_rule == {}
        assert bundle.unknown_safety_net_total == 0


# ---------------------------------------------------------------------------
# E. AINeedsReviewLowConfidence carries the fallback category + low conf.
# ---------------------------------------------------------------------------


class TestFallbackVerdictShape:
    def test_low_confidence_verdict_carries_fallback_category(self) -> None:
        provider = _UnknownProvider()
        bundle = batch_classify(
            [_make_product("Miel de Fleurs")],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert len(bundle.verdicts) == 1
        v = bundle.verdicts[0]
        assert isinstance(v, AINeedsReviewLowConfidence)
        assert v.classification.pt_group is ProteinTrackerGroup.OUT_OF_SCOPE
        # Confidence MUST be below auto-accept threshold (0.70).
        assert v.classification.confidence < Decimal("0.7")
