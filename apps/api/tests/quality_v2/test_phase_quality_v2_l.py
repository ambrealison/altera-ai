"""Phase Quality-V2-L — targeted NEVO V2 coverage pass.

œ-ligature normalization, more safe FR retailer concepts, and explicit
policy abstains (beverages / cleaning / pet / ambiguous juice drinks /
generic sauce+soup stay review/abstain). All offline; V1 default;
embeddings off by default; no route imports V2/embeddings.
"""

from __future__ import annotations

import pytest

from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    _norm,
    concept_of,
    gate_candidate,
)
from altera_api.embeddings.text_builder import build_nevo_reference_text


def _g(product: str, candidate: str) -> bool:
    return gate_candidate(product, NevoCandidate("X", candidate)).accepted


# ---------------------------------------------------------------------------
# Part A — œ / æ ligature normalization.
# ---------------------------------------------------------------------------
class TestLigature:
    def test_oe_ligature_expands(self) -> None:
        assert _norm("Œufs") == " oeufs "
        assert _norm("bœuf") == " boeuf "
        assert _norm("Æble") == " aeble "

    def test_oeufs_ligature_resolves_to_egg(self) -> None:
        assert concept_of("Œufs Plein Air x12") == "egg"
        assert _g("Œufs Plein Air x12", "Egg whole chicken av raw")

    def test_plain_oeufs_still_works(self) -> None:
        assert concept_of("Oeufs Frais") == "egg"


# ---------------------------------------------------------------------------
# Part B — new concepts resolve.
# ---------------------------------------------------------------------------
class TestConcepts:
    @pytest.mark.parametrize(
        "product,concept",
        [
            ("Thon Entier au Naturel", "tuna"),
            ("Saumon Fumé Atlantique", "salmon"),
            ("Cabillaud Pané Citron", "cod"),
            ("Crevettes Décortiquées Cuites", "shrimp"),
            ("Lardons Fumés", "bacon"),
            ("Brioche Tranchée", "brioche"),
            ("Glace Vanille", "ice_cream"),
            ("Petits Pois Surgelés", "green_peas"),
            ("Épinards Branches Crème", "spinach"),
            ("Taboulé Oriental", "couscous"),
            ("Houmous Citron Confit", "hummus"),
        ],
    )
    def test_product_concept(self, product, concept) -> None:
        assert concept_of(product) == concept


class TestSafeMatches:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Thon Entier au Naturel", "Tuna in water tinned"),
            ("Saumon Fumé Atlantique", "Salmon smoked"),
            ("Cabillaud Pané Citron", "Cod fillet fried/simmered"),
            ("Crevettes Décortiquées Cuites", "Prawns cooked"),
            ("Crevettes Décortiquées Cuites", "Shrimps in water tinned"),
            ("Lardons Fumés", "Bacon"),
            ("Brioche Tranchée", "Brioche"),
            ("Glace Vanille", "Ice cream dairy vanilla flavoured"),
            ("Petits Pois Surgelés", "Peas green boiled"),
            ("Épinards Branches Crème", "Spinach creamed frozen boiled"),
            ("Taboulé Oriental", "Couscous boiled"),
            ("Houmous Citron Confit", "Hummus natural"),
        ],
    )
    def test_accepts_real_reference(self, product, candidate) -> None:
        assert _g(product, candidate)


# ---------------------------------------------------------------------------
# Traps stay rejected.
# ---------------------------------------------------------------------------
class TestTraps:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Crevettes Décortiquées Cuites", "Prawn crackers natural"),  # cracker
            ("Riz Basmati", "Japanese rice cracker mix w peanuts"),       # cracker
            ("Thé Glacé Pêche", "Ice cream dairy vanilla flavoured"),     # tea≠glace
            ("Lardons Fumés", "Sausage w smoked bacon-bits"),             # w joiner
        ],
    )
    def test_rejects_trap(self, product, candidate) -> None:
        assert not _g(product, candidate)


# ---------------------------------------------------------------------------
# Part C — policy abstains: beverages / cleaning / pet / ambiguous drinks.
# ---------------------------------------------------------------------------
class TestPolicyAbstains:
    @pytest.mark.parametrize(
        "product",
        [
            "Eau Pétillante Citron Vert",
            "Eau Minérale Naturelle",
            "Nectar Mangue Passion",
            "Liquide Vaisselle Citron",
            "Nettoyant Multi-Usages",
            "Essuie-Tout Décor",
            "Shampooing Anti-Pelliculaire",
            "Litière Chat Agglomérante",
            "Croquettes Chien Boeuf",
        ],
    )
    def test_no_concept_so_abstains(self, product) -> None:
        # These products resolve to no food concept → V2 abstains (safe);
        # they are never forced into a NEVO reference.
        assert concept_of(product) is None


# ---------------------------------------------------------------------------
# Part D — sauce/soup policy: resolve to a concept but only dish-noun NEVO
# references exist, so they stay review/abstain (never auto-accept a trap).
# ---------------------------------------------------------------------------
class TestSauceSoupPolicy:
    def test_tomato_sauce_does_not_accept_beans(self) -> None:
        assert not _g(
            "Sauce Tomate Basilic", "Beans white baked in tomato sauce canned"
        )
        # The real sauce reference is a dish-noun composite → not auto-accepted.
        assert not _g("Sauce Tomate Basilic", "Sauce tomato ready-to-eat jar")

    def test_soup_does_not_autoaccept_dish(self) -> None:
        assert concept_of("Soupe Potiron Châtaigne") == "soup"
        assert not _g("Soupe Potiron Châtaigne", "Soup clear w vegetables")

    def test_caesar_salad_does_not_accept_salad_dish(self) -> None:
        # A composite salad must not auto-accept a "Salad …" dish reference.
        assert not _g("Salade César Poulet", "Salad chicken")


# ---------------------------------------------------------------------------
# Reference-text aliases (retrieval help; no commercial fields).
# ---------------------------------------------------------------------------
class TestReferenceAliases:
    def test_hummus_reference_gets_houmous_alias(self) -> None:
        text = build_nevo_reference_text({"food_name_en": "Hummus natural"}).lower()
        assert "houmous" in text

    def test_tuna_reference_gets_thon_alias(self) -> None:
        text = build_nevo_reference_text(
            {"food_name_en": "Tuna in water tinned"}
        ).lower()
        assert "thon" in text
