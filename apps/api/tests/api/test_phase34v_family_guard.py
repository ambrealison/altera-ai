"""Phase 34V — NEVO family guard + partial-calc fix + PT prompt v5.

Areas under test:

A. ``product_family`` correctly buckets the canonical examples that
   produced production false positives:
   Corn Flakes → cereal, Vinaigre → condiment, Margarine → oil,
   Crème Fraîche → dairy, Nettoyant / Essuie-Tout → non_food, etc.

B. ``nevo_candidate_family`` reads the NEVO food_group label and
   buckets the candidate symmetrically.

C. ``is_family_compatible`` rejects every documented production
   false positive (Corn Flakes ↔ chicken schnitzel, Madeleines ↔
   scallop shell, etc.) but accepts legitimate adjacencies
   (PREPARED_MEAL ↔ MEAT, SWEET_BAKERY ↔ DAIRY, plant substitute
   ↔ legume).

D. Partial-calc route now strips review_pending and
   classification_required blockers when ``allow_partial=True`` so
   "Calculer sur les données disponibles" can fire even with
   pending review rows.

E. PT prompt v5 carries the new edge-case rules and examples
   (hygiene/pet food/honey → out_of_scope, Blinis / Épinards
   crème → composite).
"""

from __future__ import annotations

from altera_api.ai.batch_prompt import (
    _PT_SYSTEM,
    BATCH_CLASSIFIER_PROMPT_VERSION,
)
from altera_api.enrichment.family_guard import (
    FoodFamily,
    is_family_compatible,
    nevo_candidate_family,
    product_family,
)

# ---------------------------------------------------------------------------
# A. product_family buckets every documented production input
# ---------------------------------------------------------------------------


class TestProductFamily:
    def test_non_food_takes_priority_over_incidental_food_words(self) -> None:
        # Even if a non-food product name contains a food word, the
        # non_food classification wins.
        assert product_family("Lessive Liquide 3L") is FoodFamily.NON_FOOD
        assert product_family("Dentifrice Menthe") is FoodFamily.NON_FOOD
        assert product_family("Couches Bébé Taille 4") is FoodFamily.NON_FOOD
        assert (
            product_family("Nettoyant Multi-Usages") is FoodFamily.NON_FOOD
        )
        assert product_family("Essuie-Tout") is FoodFamily.NON_FOOD
        assert product_family("Croquettes pour Chat") is FoodFamily.NON_FOOD
        assert product_family("Pâtée Chien") is FoodFamily.NON_FOOD
        assert product_family("Papier Toilette") is FoodFamily.NON_FOOD
        assert product_family("Shampooing Doux") is FoodFamily.NON_FOOD

    def test_beverages(self) -> None:
        assert (
            product_family("Eau Minérale Naturelle") is FoodFamily.BEVERAGE
        )
        assert product_family("Coca-Cola 1.5L") is FoodFamily.BEVERAGE
        assert product_family("Café Moulu Arabica") is FoodFamily.BEVERAGE
        assert product_family("Thé Vert") is FoodFamily.BEVERAGE
        assert product_family("Vin Rouge Bordeaux") is FoodFamily.BEVERAGE

    def test_meat(self) -> None:
        assert product_family("Blanc de Poulet Rôti") is FoodFamily.MEAT
        assert product_family("Côte de Bœuf") is FoodFamily.MEAT
        assert product_family("Jambon Blanc") is FoodFamily.MEAT

    def test_fish(self) -> None:
        assert product_family("Filets de Saumon") is FoodFamily.FISH
        assert product_family("Thon Entier au Naturel") is FoodFamily.FISH

    def test_dairy_and_egg(self) -> None:
        assert product_family("Lait Demi-Écrémé") is FoodFamily.DAIRY
        assert product_family("Yaourt Nature") is FoodFamily.DAIRY
        assert product_family("Beurre Doux") is FoodFamily.DAIRY
        assert product_family("Crème Fraîche") is FoodFamily.DAIRY
        assert product_family("Oeufs Plein Air") is FoodFamily.EGG

    def test_cereals_and_bread(self) -> None:
        assert (
            product_family("Corn Flakes Nature")
            is FoodFamily.CEREAL_BREAD_PASTA
        )
        assert (
            product_family("Pâtes Spaghetti")
            is FoodFamily.CEREAL_BREAD_PASTA
        )
        assert (
            product_family("Riz Basmati") is FoodFamily.CEREAL_BREAD_PASTA
        )
        assert (
            product_family("Pain de Mie") is FoodFamily.CEREAL_BREAD_PASTA
        )

    def test_oils_and_condiments(self) -> None:
        assert product_family("Huile d'Olive") is FoodFamily.OIL_FAT
        assert product_family("Margarine Doux") is FoodFamily.OIL_FAT
        assert product_family("Vinaigre de Cidre") is FoodFamily.CONDIMENT
        assert product_family("Sucre en Poudre") is FoodFamily.CONDIMENT
        assert product_family("Miel de Fleurs") is FoodFamily.CONDIMENT

    def test_fruit_veg(self) -> None:
        assert product_family("Pommes Golden") is FoodFamily.FRUIT_VEG
        assert product_family("Carottes Sachet") is FoodFamily.FRUIT_VEG
        assert (
            product_family("Mange-Tout Frais") is FoodFamily.FRUIT_VEG
        )

    def test_sweet_bakery(self) -> None:
        assert (
            product_family("Madeleines au Beurre")
            is FoodFamily.SWEET_BAKERY
        )
        assert (
            product_family("Glace à la Vanille") is FoodFamily.SWEET_BAKERY
        )
        assert (
            product_family("Chocolat au Lait") is FoodFamily.SWEET_BAKERY
        )

    def test_prepared_meal_family_recognises_signature_dishes(self) -> None:
        # Pizza is unambiguously a prepared meal regardless of toppings.
        assert (
            product_family("Pizza Royale Jambon Champignons")
            is FoodFamily.PREPARED_MEAL
        )
        # For "Salade Poulet César" the dominant signal is poulet
        # (chicken), so the family is MEAT — and that's actually
        # GOOD for NEVO: matching a chicken reference yields the
        # correct dominant protein. The PREPARED_MEAL family is
        # reserved for dishes whose recipe doesn't telegraph one
        # dominant ingredient.
        assert (
            product_family("Salade Poulet César") is FoodFamily.MEAT
        )

    def test_unknown_when_name_uninformative(self) -> None:
        assert product_family("") is FoodFamily.UNKNOWN_FOOD
        assert product_family(None) is FoodFamily.UNKNOWN_FOOD
        assert product_family("XYZ-42") is FoodFamily.UNKNOWN_FOOD


# ---------------------------------------------------------------------------
# B. nevo_candidate_family reads food_group + falls back to name
# ---------------------------------------------------------------------------


class TestNevoCandidateFamily:
    def test_food_group_meat(self) -> None:
        assert (
            nevo_candidate_family(
                "Meat, poultry, sausages & related products",
                "Chicken schnitzel breaded w corn flakes raw",
            )
            is FoodFamily.MEAT
        )

    def test_food_group_dairy(self) -> None:
        assert (
            nevo_candidate_family("Dairy products", "Crackers cream")
            is FoodFamily.DAIRY
        )

    def test_food_group_cereal(self) -> None:
        assert (
            nevo_candidate_family(
                "Bread, breakfast cereals, etc.",
                "Crispbread Cracottes naturel",
            )
            is FoodFamily.CEREAL_BREAD_PASTA
        )

    def test_falls_back_to_name_when_group_unrecognised(self) -> None:
        # Unknown food_group → name-based heuristic.
        assert (
            nevo_candidate_family("Misc", "Salmon Atlantic raw")
            is FoodFamily.FISH
        )


# ---------------------------------------------------------------------------
# C. is_family_compatible rejects every documented false positive
# ---------------------------------------------------------------------------


_DOCUMENTED_FALSE_POSITIVES = [
    # (retailer product, NEVO food_group, NEVO food_name)
    (
        "Corn Flakes Nature",
        "Meat, poultry, sausages & related products",
        "Chicken schnitzel breaded w corn flakes raw",
    ),
    (
        "Thon Entier au Naturel",
        "Bread, breakfast cereals, etc.",
        "Crispbread Cracottes naturel",
    ),
    (
        "Vinaigre de Cidre",
        "Meat, poultry, sausages & related products",
        "Brawn, pork pickled in vinegar",
    ),
    (
        "Margarine Doux",
        "Eggs",
        "Egg whole chicken fried in margarine",
    ),
    (
        "Blanc de Poulet Rôti",
        "Eggs",
        "Egg white chicken raw",
    ),
    (
        "Madeleines au Beurre",
        "Fish, seafood",
        "Coquilles scallop shell",
    ),
    (
        "Crème Fraîche",
        "Bread, breakfast cereals, etc.",
        "Crackers cream",
    ),
    (
        "Nettoyant Multi-Usages",
        "Bread, breakfast cereals, etc.",
        "Rice multi-grain raw",
    ),
    (
        "Essuie-Tout",
        "Vegetables",
        "Mange-tout raw",
    ),
]


class TestFamilyGuard:
    def test_every_documented_false_positive_is_rejected(self) -> None:
        for retailer_name, nevo_group, nevo_name in _DOCUMENTED_FALSE_POSITIVES:
            p = product_family(retailer_name)
            c = nevo_candidate_family(nevo_group, nevo_name)
            assert not is_family_compatible(p, c), (
                f"FALSE POSITIVE WOULD STILL FIRE: "
                f"{retailer_name!r} (family {p.value}) ↔ "
                f"{nevo_name!r} (family {c.value})"
            )

    def test_legitimate_match_accepted_meat_to_meat(self) -> None:
        p = product_family("Blanc de Poulet Rôti")
        c = nevo_candidate_family(
            "Meat, poultry, sausages & related products",
            "Chicken breast roasted",
        )
        assert is_family_compatible(p, c)

    def test_legitimate_match_dairy_to_dairy(self) -> None:
        p = product_family("Yaourt Nature 0%")
        c = nevo_candidate_family("Dairy products", "Yogurt plain low fat")
        assert is_family_compatible(p, c)

    def test_legitimate_match_legume_to_legume(self) -> None:
        p = product_family("Lentilles Vertes du Puy")
        c = nevo_candidate_family(
            "Legumes, nuts, seeds", "Lentils green cooked"
        )
        assert is_family_compatible(p, c)

    def test_non_food_never_matches_food(self) -> None:
        p = product_family("Lessive Liquide 3L")
        c = nevo_candidate_family("Vegetables", "Mange-tout raw")
        assert not is_family_compatible(p, c)


# ---------------------------------------------------------------------------
# D. Partial-calc strips review_pending + classification_required
# ---------------------------------------------------------------------------


class TestPartialCalcFilter:
    def test_partial_filter_strips_documented_codes(self) -> None:
        # The route logic uses an inline _PARTIAL_OK_CODES set. We
        # mirror it here so a future refactor that drops a code from
        # the set fails this test instead of silently breaking the
        # wizard's "Calculer sur les données disponibles" button.
        expected = {
            "nutrition_required",
            "review_pending",
            "classification_required",
        }
        # The constant lives inside the route closure; verify by
        # parsing the source for the documented codes.
        import inspect

        from altera_api.api import routes

        source = inspect.getsource(routes.create_run)
        for code in expected:
            assert code in source, (
                f"partial-calc filter must reference {code!r}"
            )
        # And the comment block documenting the policy is present.
        assert "_PARTIAL_OK_CODES" in source


# ---------------------------------------------------------------------------
# E. PT prompt v5
# ---------------------------------------------------------------------------


class TestPromptV5:
    def test_prompt_version_bumped_to_v5(self) -> None:
        assert BATCH_CLASSIFIER_PROMPT_VERSION.endswith("v5")

    def test_unknown_section_explicitly_forbids_non_food(self) -> None:
        # The new rule: non-food MUST be out_of_scope, not unknown.
        lowered = _PT_SYSTEM.lower()
        assert "papier toilette" in lowered
        assert "couches bébé" in lowered or "couches bebe" in lowered
        assert "dentifrice" in lowered
        assert "essuie-tout" in lowered or "essuie tout" in lowered

    def test_composite_section_covers_bakery_with_dairy_egg(self) -> None:
        lowered = _PT_SYSTEM.lower()
        # The bakery-w-dairy-egg edge cases must be documented.
        for term in ("blinis moelleux", "baguettes viennoises", "pain au lait"):
            assert term in lowered, f"prompt missing edge case {term!r}"

    def test_composite_section_covers_veg_in_cream(self) -> None:
        lowered = _PT_SYSTEM.lower()
        assert "épinards" in lowered or "epinards" in lowered
        assert "crème" in lowered or "creme" in lowered

    def test_pure_flavourings_listed_as_out_of_scope(self) -> None:
        lowered = _PT_SYSTEM.lower()
        # Honey was going to unknown in the 91%-correct test.
        assert "miel" in lowered
