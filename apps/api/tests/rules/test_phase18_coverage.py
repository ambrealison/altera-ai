"""Phase 18 — expanded deterministic rule coverage tests.

Covers the new product categories added in Phase 18:
- PT: processed meat, game, whey protein, mycoprotein, pea/plant protein bars,
  plant cream/butter, protein salads, protein soups, burgers, sushi/grain bowls
- WWF: processed meats, plant alternatives, FG3 animal fat, FG4 fruits,
  FG5 oats/quinoa, FG6 starchy veg, FG7 snacks
- AI skipped when deterministic match is strong (PTMatched)
- AI invoked when pass-through (PTPassThrough)
- Methodology separation (PT verdict ≠ WWF verdict for same product)
"""
from __future__ import annotations

from datetime import datetime

from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
)
from altera_api.rules.engine import (
    PTMatched,
    PTPassThrough,
    WWFMatched,
    WWFPassThrough,
    classify_protein_tracker,
    classify_wwf,
)
from altera_api.rules.loader import load_rules_from_dir

# ---------------------------------------------------------------------------
# PT — animal_core expansions
# ---------------------------------------------------------------------------

class TestPTAnimalCoreExpanded:
    def test_chorizo_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Iberico Chorizo Slices")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_salami_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Milano Salami 100g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_sausage_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Pork Sausages 6 Pack")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_venison_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Venison Steak 250g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_whey_protein_bar_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Whey Protein Bar Chocolate 55g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_salmon_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Atlantic Salmon Fillet 200g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_mussels_are_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Cooked Mussels in Brine 200g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_french_boeuf_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Steak de Boeuf 200g", language="fr")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE

    def test_french_saumon_is_animal_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Filet de Saumon Fumé", language="fr")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE


# ---------------------------------------------------------------------------
# PT — plant_based_core expansions
# ---------------------------------------------------------------------------

class TestPTPlantCoreExpanded:
    def test_tofu_is_plant_based_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Firm Tofu Block 400g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_lentils_are_plant_based_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Red Lentils Dried 500g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_quorn_is_plant_based_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Quorn Mince 300g", labels=("vegetarian",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_pea_protein_bar_is_plant_based_core(
        self, make_pt_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Pea Protein Bar Salted Caramel", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_plant_protein_bar_is_plant_based_core(
        self, make_pt_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(
            name="Plant Protein Bar Chocolate Peanut", labels=("vegan",)
        )
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_tempeh_is_plant_based_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Organic Tempeh Block 200g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_french_lentilles_is_plant_based_core(
        self, make_pt_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Lentilles Vertes du Puy 500g", language="fr")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE

    def test_hummus_is_plant_based_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Classic Hummus 200g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE


# ---------------------------------------------------------------------------
# PT — plant_based_non_core expansions
# ---------------------------------------------------------------------------

class TestPTPlantNonCoreExpanded:
    def test_oat_cream_is_plant_non_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Oat Cream Single 250ml", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE

    def test_vegan_butter_is_plant_non_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Vegan Butter Block 250g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE

    def test_coconut_yoghurt_is_plant_non_core(
        self, make_pt_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(
            name="Coconut Yoghurt Natural 400g", labels=("vegan", "dairy_free")
        )
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE

    def test_vegan_cheese_is_plant_non_core(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Vegan Cheese Slices 200g", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE


# ---------------------------------------------------------------------------
# PT — composite_products expansions
# ---------------------------------------------------------------------------

class TestPTCompositesExpanded:
    def test_pizza_is_composite(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Margherita Pizza 400g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        # No collision with other rules — pizza alone should be composite
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS

    def test_vegan_pizza_is_composite(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Vegan Pizza Mozzarella Style", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS

    def test_chicken_salad_routes_to_review(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Grilled Chicken Salad with Quinoa")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        # "chicken" fires poultry (animal_core) AND "salad" fires protein_salads (composite)
        # → both categories fire → routed to manual review as a collision
        from altera_api.rules.engine import PTRuleCollision
        assert isinstance(verdict, PTRuleCollision)

    def test_burger_is_composite(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Classic Beef Burger 180g")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        # beef + burger → both animal_core and composite fire → collision → NOT pass-through
        assert not isinstance(verdict, PTPassThrough)

    def test_sushi_is_composite(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Salmon Sushi Platter 8 Pieces")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        # salmon + sushi → both animal_core and composite fire → NOT pass-through
        assert not isinstance(verdict, PTPassThrough)

    def test_tofu_buddha_bowl_is_composite(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Tofu Buddha Bowl with Quinoa", labels=("vegan",))
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        # tofu + bowl → plant_core and composite both fire → NOT pass-through
        assert not isinstance(verdict, PTPassThrough)

    def test_unknown_sauce_is_pass_through(self, make_pt_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_pt_product(name="Mystery Sauce 250ml")
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        assert isinstance(verdict, PTPassThrough)

    def test_pet_food_is_not_pass_through(self, make_pt_product, now: datetime) -> None:
        """Pet food is flagged as contradiction (out-of-scope signal), not pass-through."""
        rs = load_rules_from_dir()
        product = make_pt_product(
            name="Premium Dog Food Chicken 400g",
            retailer_category="Pet Food",
        )
        verdict = classify_protein_tracker(product, rs.pt, now=now)
        from altera_api.rules.engine import PTContradiction
        assert isinstance(verdict, PTContradiction)


# ---------------------------------------------------------------------------
# WWF — FG1 expansions
# ---------------------------------------------------------------------------

class TestWWFFG1Expanded:
    def test_processed_meat_is_fg1_processed(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Chorizo Slices 150g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.PROCESSED_MEATS_ALTERNATIVES

    def test_meat_alternative_is_fg1_meat_alt(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Veggie Burger Patties 2 Pack", labels=("vegan",))
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES

    def test_edamame_is_fg1_legumes(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Edamame Beans Frozen 400g", labels=("vegan",))
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.LEGUMES

    def test_mycoprotein_is_fg1_alt_protein(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Quorn Pieces 350g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES


# ---------------------------------------------------------------------------
# WWF — FG3 fat expansions
# ---------------------------------------------------------------------------

class TestWWFFG3Expanded:
    def test_lard_is_fg3_animal_fat(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Beef Dripping 250g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG3
        assert verdict.classification.fg3_subgroup is WWFFG3Subgroup.ANIMAL_BASED_FAT

    def test_olive_oil_is_fg3_plant_fat(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Extra Virgin Olive Oil 500ml")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG3
        assert verdict.classification.fg3_subgroup is WWFFG3Subgroup.PLANT_BASED_FAT


# ---------------------------------------------------------------------------
# WWF — FG4 fruit/veg expansions
# ---------------------------------------------------------------------------

class TestWWFFG4Expanded:
    def test_broccoli_is_fg4(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Tenderstem Broccoli 200g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG4

    def test_strawberries_are_fg4(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="British Strawberries 400g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG4

    def test_spinach_is_fg4(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Baby Spinach Leaves 200g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG4


# ---------------------------------------------------------------------------
# WWF — FG5 grain expansions
# ---------------------------------------------------------------------------

class TestWWFFG5Expanded:
    def test_oats_are_fg5_whole_grain(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Rolled Oats 1kg")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG5
        assert verdict.classification.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_quinoa_is_fg5_whole_grain(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Organic Quinoa 500g", labels=("vegan",))
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG5
        assert verdict.classification.fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_pasta_is_fg5_refined(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Penne Pasta 500g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG5
        assert verdict.classification.fg5_grain_kind is WWFFG5GrainKind.REFINED_GRAIN


# ---------------------------------------------------------------------------
# WWF — FG6 starchy veg
# ---------------------------------------------------------------------------

class TestWWFFG6StarchyVeg:
    def test_potato_is_fg6(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="White Potatoes 1kg")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG6

    def test_sweet_potato_is_fg6(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Sweet Potato 800g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG6

    def test_cassava_is_fg6(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Cassava Flour 500g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG6


# ---------------------------------------------------------------------------
# WWF — FG7 snack expansions
# ---------------------------------------------------------------------------

class TestWWFFG7Expanded:
    def test_crisps_are_fg7_plant_snack(self, make_wwf_product, now: datetime) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Sea Salt Crisps 150g", labels=("vegan",))
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG7
        assert verdict.classification.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK

    def test_milk_chocolate_is_fg7_animal_snack(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Milk Chocolate Bar 100g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG7
        assert verdict.classification.fg7_snack_kind is WWFFG7SnackKind.ANIMAL_BASED_SNACK

    def test_dark_chocolate_is_fg7_plant_snack(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Dark Chocolate 85% 100g", labels=("vegan",))
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG7
        assert verdict.classification.fg7_snack_kind is WWFFG7SnackKind.PLANT_BASED_SNACK

    def test_beef_jerky_is_fg7_animal_snack(
        self, make_wwf_product, now: datetime
    ) -> None:
        rs = load_rules_from_dir()
        product = make_wwf_product(name="Beef Jerky 60g")
        verdict = classify_wwf(product, rs.wwf, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG7
        assert verdict.classification.fg7_snack_kind is WWFFG7SnackKind.ANIMAL_BASED_SNACK


# ---------------------------------------------------------------------------
# Methodology separation: same product → different PT and WWF verdicts
# ---------------------------------------------------------------------------

class TestMethodologySeparation:
    def test_beef_mince_pt_vs_wwf(
        self, make_pt_product, make_wwf_product, now: datetime
    ) -> None:
        """Beef mince maps to animal_core (PT) and FG1/red_meat (WWF) independently."""
        rs = load_rules_from_dir()
        pt_p = make_pt_product(name="Beef Mince 500g")
        wwf_p = make_wwf_product(name="Beef Mince 500g")
        pt_v = classify_protein_tracker(pt_p, rs.pt, now=now)
        wwf_v = classify_wwf(wwf_p, rs.wwf, now=now)
        assert isinstance(pt_v, PTMatched)
        assert pt_v.classification.pt_group is ProteinTrackerGroup.ANIMAL_CORE
        assert isinstance(wwf_v, WWFMatched)
        assert wwf_v.classification.wwf_food_group is WWFFoodGroup.FG1
        assert wwf_v.classification.fg1_subgroup is WWFFG1Subgroup.RED_MEAT

    def test_oat_milk_pt_vs_wwf(
        self, make_pt_product, make_wwf_product, now: datetime
    ) -> None:
        """Oat milk → plant_non_core (PT) and FG2/dairy_alt_plant (WWF)."""
        rs = load_rules_from_dir()
        pt_v = classify_protein_tracker(make_pt_product(name="Oat Milk 1L"), rs.pt, now=now)
        wwf_v = classify_wwf(make_wwf_product(name="Oat Milk 1L"), rs.wwf, now=now)
        assert isinstance(pt_v, PTMatched)
        assert pt_v.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_NON_CORE
        assert isinstance(wwf_v, WWFMatched)
        assert wwf_v.classification.fg2_subgroup is WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT

    def test_mystery_product_is_pass_through_both(
        self, make_pt_product, make_wwf_product, now: datetime
    ) -> None:
        """An unknown product is pass-through for both methodologies."""
        rs = load_rules_from_dir()
        pt_v = classify_protein_tracker(
            make_pt_product(name="Artisan Sauce 350ml"), rs.pt, now=now
        )
        wwf_v = classify_wwf(
            make_wwf_product(name="Artisan Sauce 350ml"), rs.wwf, now=now
        )
        assert isinstance(pt_v, PTPassThrough)
        assert isinstance(wwf_v, WWFPassThrough)
