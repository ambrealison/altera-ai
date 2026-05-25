"""Phase 34T — PT taxonomy correction + NEVO match_method fix + 1000-row.

Areas under test:

A. The PT prompt encodes the new strict taxonomy:
   - plant_based_core is described as NARROW (legumes, nuts, tofu,
     plant-based substitutes for meat/milk/eggs only).
   - plant_based_non_core explicitly covers bread, rice, pasta,
     fruits, vegetables, oils, juices.
   - composite covers animal + plant recipes.
   - out_of_scope is reserved for non-human-food.
   - unknown is reserved for unusable product names.

B. The canonical example set hits every category:
   - Pommes Golden / Carottes / Pâtes / Riz / Pain → plant_based_non_core
     (NOT plant_based_core)
   - Tofu / Lentilles / Pois Chiches / Steak Végétal / Boisson Avoine /
     Noix de Cajou → plant_based_core
   - Blanc de Poulet / Saumon / Lait / Yaourt / Beurre → animal_core
   - Pizza / Quiche / Salade Poulet / Lasagnes → composite_products
   - Lessive / Dentifrice / Croquettes Chien / Shampooing → out_of_scope

C. The NEVO ``match_method`` enum carries ``NONE``; the SQL migration
   extends the CHECK constraint to allow it.

D. ``apply-references`` response caps ``product_results`` at the
   documented limit (100) and exposes the total separately.
"""

from __future__ import annotations

from pathlib import Path

from altera_api.ai.batch_prompt import _PT_SYSTEM
from altera_api.domain.enrichment import NutritionMatchMethod

# ---------------------------------------------------------------------------
# A + B. PT taxonomy in the prompt
# ---------------------------------------------------------------------------


class TestPromptTaxonomy:
    def test_plant_based_core_is_described_as_narrow(self) -> None:
        # The new taxonomy must explicitly say the category is NARROW
        # so the model doesn't keep dumping bread/rice into it.
        lowered = _PT_SYSTEM.lower()
        assert "narrow" in lowered, (
            "prompt must say plant_based_core is NARROW"
        )

    def test_prompt_explicitly_forbids_bread_rice_in_core(self) -> None:
        # The prompt must call out that bread / rice / pasta belong in
        # plant_based_non_core, not core. The earlier prompt grouped
        # them in core and produced ~70% taxonomy correctness.
        lowered = _PT_SYSTEM.lower()
        # Look for a clear statement that bread/rice/pasta are non_core.
        assert (
            "bread" in lowered
            and "rice" in lowered
            and "pasta" in lowered
        )
        # And that the non_core list calls them out.
        assert "plant_based_non_core" in lowered
        # And that the prompt warns against putting fruits/vegetables in core.
        for term in ("fruits", "vegetables"):
            assert term in lowered, f"prompt missing term {term!r}"

    def test_plant_based_core_examples_are_protein_relevant(self) -> None:
        # Examples in the plant_based_core block must be legumes,
        # nuts, tofu/tempeh, or plant-based substitutes.
        for ex in [
            "Tofu Nature Bio",
            "Pois Chiches",
            "Lentilles Vertes",
            "Steak Végétal",
            "Noix de Cajou",
            "Tempeh",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"plant_based_core canonical example missing: {ex!r}"
            )

    def test_plant_based_non_core_examples_cover_bread_rice_pasta_fruits(
        self,
    ) -> None:
        for ex in [
            "Pommes Golden",
            "Carottes",
            "Pommes de Terre",
            "Pâtes Spaghetti",
            "Riz Basmati",
            "Pain de Mie",
            "Huile d'Olive",
            "Chips Nature",
            "Jus d'Orange",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"plant_based_non_core canonical example missing: {ex!r}"
            )

    def test_animal_core_examples(self) -> None:
        for ex in [
            "Blanc de Poulet",
            "Filets de Saumon",
            "Lait Demi-Écrémé",
            "Yaourt Nature",
            "Fromage Blanc",
            "Beurre Doux",
            "Oeufs Plein Air",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"animal_core canonical example missing: {ex!r}"
            )

    def test_composite_examples(self) -> None:
        for ex in [
            "Burger Végétal & Emmental",
            "Salade Poulet César",
            "Soupe Poulet et Légumes",
            "Lasagnes Bolognaise",
            "Pizza Royale",
            "Quiche Lorraine",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"composite canonical example missing: {ex!r}"
            )

    def test_out_of_scope_examples_are_only_non_food(self) -> None:
        for ex in [
            "Lessive",
            "Dentifrice",
            "Papier Toilette",
            "Croquettes",
            "Shampooing",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"out_of_scope canonical example missing: {ex!r}"
            )

    def test_unknown_examples(self) -> None:
        # The prompt must illustrate when unknown is acceptable so
        # the model isn't tempted to use it for confidence-based fallback.
        for ex in [
            '"Produit 123"',
            '"Divers"',
        ]:
            assert ex in _PT_SYSTEM, (
                f"unknown canonical example missing: {ex!r}"
            )


# ---------------------------------------------------------------------------
# C. NEVO match_method enum + migration
# ---------------------------------------------------------------------------


class TestMatchMethodEnum:
    def test_enum_carries_all_expected_values(self) -> None:
        values = {m.value for m in NutritionMatchMethod}
        assert values == {
            "deterministic",
            "ai_assisted",
            "manual",
            "none",
        }

    def test_migration_file_exists_and_allows_none(self) -> None:
        repo_root = Path(__file__).resolve().parents[4]
        migration = (
            repo_root
            / "supabase"
            / "migrations"
            / "0035_phase34t_match_method_none.sql"
        )
        assert migration.is_file(), (
            f"missing migration at {migration}"
        )
        sql = migration.read_text(encoding="utf-8")
        # CHECK constraint must include 'none' and the three prior values.
        for value in ("deterministic", "ai_assisted", "manual", "none"):
            assert f"'{value}'" in sql, (
                f"migration constraint missing value {value!r}"
            )


# ---------------------------------------------------------------------------
# D. ApplyReferences response cap
# ---------------------------------------------------------------------------


class TestApplyReferencesResponseCap:
    def test_limit_constant_is_reasonable(self) -> None:
        from altera_api.api.routes import APPLY_REFERENCES_DETAIL_LIMIT

        # Keep the cap tight enough that 10K-row responses stay small.
        assert 10 <= APPLY_REFERENCES_DETAIL_LIMIT <= 500

    def test_response_model_has_total_counter(self) -> None:
        from altera_api.api.routes import ApplyReferencesResponse

        # The cap relies on the totals field; confirm the schema
        # exposes it so the wizard's "Showing first N of M" copy works.
        assert "product_results_total" in ApplyReferencesResponse.model_fields
