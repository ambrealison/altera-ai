"""Phase 36E — NEVO matching precision redesign.

A 75-product audit on production data measured:

  * NEVO coverage   = 65 / 75 = 86.7%
  * Match precision = 33 / 65 = **50.8%**  ← false positives ate half

The dominant false positives all shared the same shape: the matcher
locked onto a SECONDARY ingredient or a phrase contained in the
candidate name, ignoring the principal noun of the product:

  - "Ratatouille à l'Huile d'Olive"  →  "Oil olive"            (×9)
  - "Ratatouille Cuisine Vapeur"     →  "Alpro Cuisine Light"  (×8)
  - "Ratatouille Filet"              →  "Herring fillet"       (×3)
  - "Ratatouille Ail & Persil"       →  "Garlic raw"           (×3)
  - "Lait"                           →  "Potatoes mashed with milk"
  - "Beurre"                         →  "Apple pie without butter"
  - "Lasagnes"                       →  "Pasta white boiled"

Phase 36E introduces a *product head* concept: a curated list of
principal nouns (ratatouille, lait, beurre, lasagnes, tofu, …) with
their semantic family. The fuzzy matcher now:

  A. requires the candidate to share the head's alias tokens, AND
  B. rejects "dish containing X" candidates when the head is a simple
     food (lait → "Potatoes mashed with milk" must NOT match), AND
  C. boosts head-matching candidates so a coincidental secondary
     token tie no longer steals the match.

The audit cases are encoded below as a frozen evaluation fixture.
Each case asserts the matcher returns either the correct entry, OR
``None`` (no_match). Auto-accepting the historical false positive is
never allowed.

Out of scope for this phase:
  * nutrition / NEVO table perf (Phase 36F-lite),
  * AI classification prompt / taxonomy (Phase 34Q / 34T),
  * upstream candidate-shortlist heuristics in
    ``nutrition_candidates.candidates_for_product`` (covered only at
    the level needed to back the regression tests).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.ai.nutrition_head import (
    COMPOSITE_KINDS,
    SIMPLE_FOOD_KINDS,
    extract_product_head,
    looks_like_composite,
)
from altera_api.domain.nevo import NevoEntry
from altera_api.enrichment.providers.nevo import NevoProvider

# ---------------------------------------------------------------------------
# Reference fixture: representative NEVO entries (good + bad candidates).
# ---------------------------------------------------------------------------


def _entry(name_en: str, group: str, protein: str = "5.0") -> NevoEntry:
    return NevoEntry(
        id=uuid4(),
        source_version="2025_v9.0",
        nevo_code=str(uuid4())[:8],
        food_name_nl="",
        food_name_en=name_en,
        food_group=group,
        quantity_basis="per 100g",
        protein_g_per_100g=Decimal(protein),
        plant_protein_g_per_100g=None,
        animal_protein_g_per_100g=None,
    )


@pytest.fixture(scope="module")
def provider() -> NevoProvider:
    entries: list[NevoEntry] = [
        # Good prepared-meal candidates the matcher SHOULD pick.
        _entry("Ratatouille prepared without meat", "Prepared dishes"),
        _entry("Lasagne bolognese", "Prepared dishes"),
        # Plant substitute / drink candidates.
        _entry("Tofu plain", "Plant proteins"),
        _entry("Oat drink unsweetened", "Plant beverages"),
        # Dairy single-foods (the head IS the food).
        _entry("Milk semi-skimmed", "Dairy"),
        _entry("Butter unsalted", "Dairy fats"),
        _entry("Quark plain", "Dairy"),
        # Simple ingredients the audit shows as false positives —
        # the matcher must REJECT these when the product head is a
        # composite/prepared meal.
        _entry("Oil olive", "Oils and fats"),
        _entry("Garlic raw", "Vegetables"),
        _entry("Herring fillet smoked", "Fish"),
        _entry("Alpro Cuisine Light", "Plant beverages"),
        _entry("Pasta white boiled", "Cereals"),
        # "Dish containing X" candidates — the matcher must REJECT
        # these when the product head is a simple food (lait, beurre…).
        _entry("Potatoes mashed with milk", "Prepared dishes"),
        _entry("Apple pie without butter", "Pastries"),
    ]
    return NevoProvider.from_entries(entries)


# ---------------------------------------------------------------------------
# A. Product-head extraction
# ---------------------------------------------------------------------------


class TestHeadExtraction:
    @pytest.mark.parametrize(
        ("name", "expected_raw", "expected_kind"),
        [
            ("Ratatouille Provençale à l'Huile d'Olive", "ratatouille", "prepared_meal"),
            ("Ratatouille Ail & Persil", "ratatouille", "prepared_meal"),
            ("Ratatouille Cuisine Vapeur", "ratatouille", "prepared_meal"),
            ("Ratatouille Filet", "ratatouille", "prepared_meal"),
            ("Lasagnes Bolognaise", "lasagnes", "prepared_meal"),
            ("Lait Demi-écrémé", "lait", "dairy_milk"),
            ("Beurre Doux", "beurre", "dairy_fat"),
            ("Tofu Nature Bio", "tofu", "plant_protein"),
            ("Steak Végétal Soja & Blé", "steak vegetal", "plant_substitute"),
            ("Boisson Avoine Bio", "boisson avoine", "plant_drink"),
            ("Fromage Blanc", "fromage blanc", "dairy_fresh_cheese"),
            # Plain "fromage" still picks the cheese kind because the
            # multi-word "fromage blanc" doesn't apply.
            ("Fromage Comté Affiné", "fromage", "dairy_cheese"),
        ],
    )
    def test_extracts_curated_head(
        self, name: str, expected_raw: str, expected_kind: str
    ) -> None:
        head = extract_product_head(name)
        assert head is not None, f"no head for {name!r}"
        assert head.raw == expected_raw
        assert head.kind == expected_kind

    def test_no_head_returns_none(self) -> None:
        # Brand-only / packaging-only / unknown-food names get None
        # so the matcher falls back to the token path with the
        # tighter threshold.
        assert extract_product_head("Marque Pack 6x125g Bio") is None

    def test_lait_doesnt_match_laitue(self) -> None:
        # Word-boundary regex must not let "lait" match "laitue".
        head = extract_product_head("Laitue Iceberg Bio")
        assert head is None or head.raw != "lait"


# ---------------------------------------------------------------------------
# B. Composite-pattern detection
# ---------------------------------------------------------------------------


class TestCompositePatternDetection:
    @pytest.mark.parametrize(
        "candidate",
        [
            "Potatoes mashed with milk",
            "Apple pie without butter",
            "Salad mixed with chicken",
            "Bread in olive oil",
            "Aardappels met melk",        # NL "with"
            "Soep zonder room",            # NL "without"
            "Cereal enriched with iron",
            "Yogurt supplemented",
        ],
    )
    def test_recognises_dish_containing(self, candidate: str) -> None:
        assert looks_like_composite(candidate)

    @pytest.mark.parametrize(
        "candidate",
        [
            "Ratatouille prepared",
            "Milk semi-skimmed",
            "Butter unsalted",
            "Lasagne bolognese",
            "Tofu plain",
            "Oat drink unsweetened",
        ],
    )
    def test_doesnt_flag_plain_foods(self, candidate: str) -> None:
        assert not looks_like_composite(candidate)


# ---------------------------------------------------------------------------
# C. Audit-case regression tests — the matcher MUST NOT auto-accept
#    the historical false positives.
# ---------------------------------------------------------------------------


def _matched_name(provider: NevoProvider, product: str) -> str | None:
    result = provider.match(food_name=product)
    if result is None:
        return None
    return result.entry.food_name_en


class TestAuditCasesRatatouille:
    """The ratatouille block was the largest false-positive cluster
    in the audit (32 / 65 = 49% of all FPs). The matcher must reject
    every secondary-ingredient candidate."""

    @pytest.mark.parametrize(
        "product",
        [
            "Ratatouille Provençale à l'Huile d'Olive",
            "Ratatouille Ail & Persil",
            "Ratatouille Cuisine Vapeur",
            "Ratatouille Filet",
        ],
    )
    def test_ratatouille_rejects_secondary_candidates(
        self, provider: NevoProvider, product: str
    ) -> None:
        matched = _matched_name(provider, product)
        # Accepting "Ratatouille prepared without meat" or returning
        # None are both correct outcomes; the historical FPs must
        # NEVER be returned.
        assert matched in (None, "Ratatouille prepared without meat"), (
            f"{product!r} matched bad candidate: {matched!r}"
        )

    def test_ratatouille_can_still_match_correct_candidate(
        self, provider: NevoProvider
    ) -> None:
        # A plain "Ratatouille" should still match the prepared dish.
        matched = _matched_name(provider, "Ratatouille")
        assert matched == "Ratatouille prepared without meat"


class TestAuditCasesLasagnes:
    def test_lasagnes_does_not_match_pasta_only(
        self, provider: NevoProvider
    ) -> None:
        matched = _matched_name(provider, "Lasagnes Bolognaise")
        # Must NEVER be the bare pasta candidate.
        assert matched != "Pasta white boiled", (
            "Lasagnes regressed to plain pasta"
        )
        assert matched in (None, "Lasagne bolognese")


class TestAuditCasesSimpleDairy:
    def test_lait_does_not_match_potatoes_mashed_with_milk(
        self, provider: NevoProvider
    ) -> None:
        matched = _matched_name(provider, "Lait Demi-écrémé")
        assert matched in (None, "Milk semi-skimmed")
        # Hard-no: it must not pick the composite dish.
        assert matched != "Potatoes mashed with milk"

    def test_beurre_does_not_match_apple_pie_without_butter(
        self, provider: NevoProvider
    ) -> None:
        matched = _matched_name(provider, "Beurre Doux")
        assert matched in (None, "Butter unsalted")
        assert matched != "Apple pie without butter"


class TestAuditCasesPlantFoods:
    def test_tofu_matches_tofu(self, provider: NevoProvider) -> None:
        matched = _matched_name(provider, "Tofu Nature Bio")
        assert matched == "Tofu plain"

    def test_boisson_avoine_matches_oat_drink(
        self, provider: NevoProvider
    ) -> None:
        matched = _matched_name(provider, "Boisson Avoine Bio")
        # Either Oat drink, or no_match — never Alpro Cuisine Light.
        assert matched in (None, "Oat drink unsweetened")
        assert matched != "Alpro Cuisine Light"


class TestAuditCasesFromage:
    def test_fromage_blanc_does_not_pick_random_dairy_dish(
        self, provider: NevoProvider
    ) -> None:
        matched = _matched_name(provider, "Fromage Blanc")
        # Quark is the closest match. Composite "Potatoes mashed with
        # milk" must never win.
        assert matched in (None, "Quark plain")
        assert matched != "Potatoes mashed with milk"


# ---------------------------------------------------------------------------
# D. Aggregate precision over the audit fixture
# ---------------------------------------------------------------------------


# Each tuple is (product_name, allowed_match_names_or_None).
# ``None`` in the second slot means "the matcher MAY return None
# (no_match) — that's allowed but a correct positive is preferred".
# Anything not in the allowlist counts as a false positive.
_AUDIT_FIXTURE: tuple[tuple[str, frozenset[str | None]], ...] = (
    ("Ratatouille Provençale à l'Huile d'Olive",
     frozenset({None, "Ratatouille prepared without meat"})),
    ("Ratatouille Ail & Persil",
     frozenset({None, "Ratatouille prepared without meat"})),
    ("Ratatouille Cuisine Vapeur",
     frozenset({None, "Ratatouille prepared without meat"})),
    ("Ratatouille Filet",
     frozenset({None, "Ratatouille prepared without meat"})),
    ("Ratatouille",
     frozenset({"Ratatouille prepared without meat"})),
    ("Lasagnes Bolognaise",
     frozenset({None, "Lasagne bolognese"})),
    ("Lait Demi-écrémé",
     frozenset({None, "Milk semi-skimmed"})),
    ("Beurre Doux",
     frozenset({None, "Butter unsalted"})),
    ("Tofu Nature Bio",
     frozenset({"Tofu plain"})),
    ("Boisson Avoine Bio",
     frozenset({None, "Oat drink unsweetened"})),
    ("Fromage Blanc",
     frozenset({None, "Quark plain"})),
)


class TestAggregateAuditPrecision:
    def test_no_false_positives_on_audit_fixture(
        self, provider: NevoProvider
    ) -> None:
        false_positives: list[tuple[str, str]] = []
        for product, allowed in _AUDIT_FIXTURE:
            matched = _matched_name(provider, product)
            if matched not in allowed:
                false_positives.append((product, matched or "<none>"))
        assert not false_positives, (
            "matcher regressed: "
            + "; ".join(f"{p!r} → {m!r}" for p, m in false_positives)
        )

    def test_precision_target_met(self, provider: NevoProvider) -> None:
        """Precision on matched lines must be >= 75% target."""
        matched_count = 0
        correct_count = 0
        for product, allowed in _AUDIT_FIXTURE:
            matched = _matched_name(provider, product)
            if matched is None:
                continue
            matched_count += 1
            if matched in allowed:
                correct_count += 1
        if matched_count == 0:
            pytest.skip("matcher returned no matches on fixture")
        precision = correct_count / matched_count
        assert precision >= 0.75, (
            f"precision {precision:.2%} below 75% target "
            f"({correct_count}/{matched_count})"
        )


# ---------------------------------------------------------------------------
# E. Sanity — taxonomy constants are wired correctly.
# ---------------------------------------------------------------------------


class TestKindTaxonomy:
    def test_simple_food_kinds_includes_dairy_and_egg(self) -> None:
        assert "dairy_milk" in SIMPLE_FOOD_KINDS
        assert "dairy_fat" in SIMPLE_FOOD_KINDS
        assert "egg" in SIMPLE_FOOD_KINDS

    def test_composite_kinds_includes_prepared_meal(self) -> None:
        assert "prepared_meal" in COMPOSITE_KINDS
        assert "plant_substitute" in COMPOSITE_KINDS

    def test_no_overlap_simple_vs_composite(self) -> None:
        # Simple foods and composite meals must be disjoint
        # categories — otherwise the matcher guards conflict.
        assert SIMPLE_FOOD_KINDS.isdisjoint(COMPOSITE_KINDS)
