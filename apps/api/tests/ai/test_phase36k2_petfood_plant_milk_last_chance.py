"""Phase 36K2 — petfood in-scope, plant-milk substitute, last-chance fallback.

A second 100-product audit (after Phase 36K shipped) still showed
readable products landing as final ``Inconnu``. Three root causes:

  1. ``Croquettes Chat`` / ``Pâtée Chien`` were being routed to
     ``out_of_scope`` by the non_food guard — but petfood is IN
     scope per product rule.

  2. ``Boisson Amande Sans Sucres`` was being routed to
     ``out_of_scope`` by the beverage guard — but plant-milk
     substitutes are protein-rich and belong in
     ``plant_based_core``.

  3. Rows that failed to parse (id missing from batch response,
     unsupported category, food_guard fired on unknown) emitted
     ``AINeedsReviewParseFailed`` directly without trying the
     Phase 36K readable fallback again. The wizard surfaces those
     as ``Inconnu``.

Phase 36K2 fixes all three:

  A. New ``petfood_*`` guard rules — generic petfood routes to
     composite, petfood + animal token to animal_core, petfood +
     plant-protein anchor to plant_based_core. Same logic in the
     readable fallback.

  B. New ``plant_milk_substitute_core`` guard — runs BEFORE the
     beverage_out_of_scope check.

  C. New ``_emit_failed_or_fallback`` helper in batch_classifier:
     every site that would emit ``AINeedsReviewParseFailed`` now
     tries the readable fallback first. If a rule fires, the row
     becomes ``AINeedsReviewLowConfidence`` with the fallback
     category at confidence 0.5.

This module asserts the final-invariant: NO readable name lands as
``AINeedsReviewParseFailed`` after Phase 36K2 when a fallback
family matches.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from altera_api.ai.batch_classifier import batch_classify
from altera_api.ai.classifier import (
    AIAccepted,
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
from altera_api.domain.common import (
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
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


def _cls(group: ProteinTrackerGroup) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=uuid4(),
        pt_group=group,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase36k2-test",
        ai_model="phase36k2-fake",
        updated_at=datetime.now(UTC),
    )


def _apply(
    name: str, group: ProteinTrackerGroup
) -> tuple[ProteinTrackerGroup, str | None]:
    override = apply_pt_guards(name, _cls(group))
    if override is None:
        return group, None
    return override.new_classification.pt_group, override.rule


# ---------------------------------------------------------------------------
# A. Petfood is in-scope.
# ---------------------------------------------------------------------------


class TestPetfoodInScope:
    @pytest.mark.parametrize(
        ("name", "expected_group", "expected_rule"),
        [
            ("Croquettes Chat", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "petfood_generic_composite"),
            ("Pâtée Chien", ProteinTrackerGroup.COMPOSITE_PRODUCTS,
             "petfood_generic_composite"),
            ("Croquettes Chat Saumon", ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Croquettes Chien Poulet", ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Pâtée Chat Thon", ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Pâtée Chien Bœuf", ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Friandises Chien Poulet", ProteinTrackerGroup.ANIMAL_CORE,
             "petfood_animal_simple"),
            ("Croquettes Chien Tofu",
             ProteinTrackerGroup.PLANT_BASED_CORE,
             "petfood_plant_protein_core"),
        ],
    )
    def test_petfood_routed_correctly(
        self,
        name: str,
        expected_group: ProteinTrackerGroup,
        expected_rule: str,
    ) -> None:
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is expected_group, (
            f"{name!r}: expected {expected_group}, got {group} "
            f"(rule={rule})"
        )
        assert rule == expected_rule

    def test_petfood_never_out_of_scope(self) -> None:
        for name in (
            "Croquettes Chat",
            "Pâtée Chien",
            "Croquettes Chat Saumon",
            "Pâtée Chien Bœuf",
            "Friandises Chien Poulet",
        ):
            group, _ = _apply(name, ProteinTrackerGroup.OUT_OF_SCOPE)
            assert group is not ProteinTrackerGroup.OUT_OF_SCOPE, (
                f"{name!r} should not be out_of_scope (petfood is "
                f"in scope per product rule)"
            )


class TestPetAccessoriesOutOfScope:
    @pytest.mark.parametrize(
        "name",
        [
            "Litière Chat",
            "Jouet Chien Corde",
            "Sacs Déjections Chien",
            "Harnais Chien Taille M",
            "Gamelle Inox Chien",
        ],
    )
    def test_pet_accessory_routed_to_oos(self, name: str) -> None:
        group, rule = _apply(
            name, ProteinTrackerGroup.PLANT_BASED_NON_CORE
        )
        assert group is ProteinTrackerGroup.OUT_OF_SCOPE
        assert rule == "pet_accessory_out_of_scope"


# ---------------------------------------------------------------------------
# B. Plant-milk substitute drinks → plant_based_core.
# ---------------------------------------------------------------------------


class TestPlantMilkSubstitute:
    @pytest.mark.parametrize(
        "name",
        [
            "Boisson Amande Sans Sucres",
            "Boisson Avoine Bio",
            "Boisson Soja Vanille",
            "Boisson Riz Bio",
            "Boisson Noisette",
            "Lait de Soja",
            "Lait d'Amande Sans Sucre",
            "Lait d'Avoine Barista",
            "Lait Végétal Coco",
        ],
    )
    def test_routed_to_plant_core(self, name: str) -> None:
        # Model said out_of_scope (mis-routed as a generic beverage).
        group, rule = _apply(name, ProteinTrackerGroup.OUT_OF_SCOPE)
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert rule == "plant_milk_substitute_core"

    def test_fruit_drink_not_misclassified_as_plant_milk(self) -> None:
        # A fruit drink ("Boisson Fruitée") must NOT trigger the
        # plant-milk guard.
        group, rule = _apply(
            "Boisson Fruitée Fruits Rouges",
            ProteinTrackerGroup.OUT_OF_SCOPE,
        )
        # Either fruit_drink_non_core fires (catches the unknown→
        # non_core case), or rule is unrelated to plant_milk.
        assert rule != "plant_milk_substitute_core"


# ---------------------------------------------------------------------------
# C. Last-chance fallback: no readable product ends as parse-failed.
# ---------------------------------------------------------------------------


class _MissingIdProvider(ClassifierProvider):
    """Returns an empty results envelope so every row triggers the
    "id missing from batched response" parse-failed path. With
    Phase 36K2 those rows must instead route to the readable
    fallback when a rule fires."""

    @property
    def model(self) -> str:
        return "phase36k2-missing"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        return ProviderResponse(
            raw_text=json.dumps({"results": []}),
            model="phase36k2-missing",
        )


class TestLastChanceFallback:
    def test_missing_id_readable_food_falls_back(self) -> None:
        """Phase 36K2 — when the model omits a row entirely (the
        legacy "id missing from batched response" parse-failed
        path), readable names with a fallback family match must
        end as AINeedsReviewLowConfidence, NOT
        AINeedsReviewParseFailed."""
        provider = _MissingIdProvider()
        readable_names = [
            "Lentilles Vertes",          # plant_core
            "Saumon Atlantique",         # animal_simple
            "Croquettes Chat",           # petfood composite
            "Boisson Amande",            # plant_milk
            "Vinaigrette Balsamique",    # plant_condiment
            "Miel de Fleurs",            # sweetener
            "Confiture Abricot",         # sweet_spread
            "Sablés Noisette",           # bakery_composite
            "Cassoulet Provençale",      # animal_composite
        ]
        bundle = batch_classify(
            [_make_product(n) for n in readable_names],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # All readable names should land as LowConfidence (fallback
        # category) — none should be parse-failed.
        failed = [
            v
            for v in bundle.verdicts
            if isinstance(v, AINeedsReviewParseFailed)
        ]
        assert not failed, (
            f"{len(failed)} readable names ended up as parse-failed: "
            f"{[type(v).__name__ for v in bundle.verdicts]}"
        )
        # And the sample errors mention the last-chance fallback.
        assert any(
            "readable_fallback_last_chance" in e
            for e in bundle.sample_errors
        )

    def test_truly_unusable_name_still_parse_failed(self) -> None:
        provider = _MissingIdProvider()
        bundle = batch_classify(
            [_make_product("Produit")],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # "Produit" is in _UNUSABLE_NAME_TOKENS so the fallback
        # returns None — legacy parse-failed path fires.
        assert isinstance(
            bundle.verdicts[0], AINeedsReviewParseFailed
        )


# ---------------------------------------------------------------------------
# D. End-to-end: brief's specific failing cases.
# ---------------------------------------------------------------------------


class TestBriefObservedFailures:
    @pytest.mark.parametrize(
        ("name", "must_not_be_unknown_or_failed"),
        [
            ("Vinaigrette Balsamique", True),
            ("Miel de Fleurs Liquide", True),
            ("Confiture Abricot Intense", True),
            ("Papier Toilette", True),
            ("Dentifrice", True),
            ("Couches Bébé", True),
            ("Croquettes Chat", True),
            ("Pâtée Chien", True),
            ("Boisson Amande Sans Sucres", True),
            ("Biscuits Apéritif Romarin", True),
        ],
    )
    def test_no_readable_lands_unknown_or_failed_via_fallback(
        self,
        name: str,
        must_not_be_unknown_or_failed: bool,
    ) -> None:
        # Direct fallback unit test — the fallback returns a usable
        # tuple for each of these names.
        result = classify_readable_fallback(name)
        assert result is not None, (
            f"{name!r}: readable_fallback returned None"
        )
        group, rule = result
        assert group is not ProteinTrackerGroup.UNKNOWN, (
            f"{name!r} should not be UNKNOWN; got rule={rule}"
        )


# ---------------------------------------------------------------------------
# E. Non-regression for plain plant milk (lait de coco was sometimes
# treated as out_of_scope by the beverage guard).
# ---------------------------------------------------------------------------


class TestPlainPlantMilkNonRegression:
    def test_lait_de_coco_goes_to_plant_core(self) -> None:
        group, rule = _apply(
            "Lait de Coco Bio", ProteinTrackerGroup.OUT_OF_SCOPE
        )
        assert group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert rule == "plant_milk_substitute_core"


# ---------------------------------------------------------------------------
# F. AIAccepted still flows for clean cases.
# ---------------------------------------------------------------------------


class _CleanProvider(ClassifierProvider):
    @property
    def model(self) -> str:
        return "phase36k2-clean"

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
                    "rationale": "phase36k2 fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="phase36k2-clean",
        )


class TestAcceptedNonRegression:
    def test_clean_classification_still_accepted(self) -> None:
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
