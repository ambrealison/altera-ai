"""Phase Quality-V2-M — final NEVO V2 real-catalog coverage + retrieval
query-alias injection.

All offline; V1 default; embeddings off by default; no route imports
V2/embeddings.
"""

from __future__ import annotations

import pytest

from altera_api.classification_v2.nevo_index import build_nevo_query_text
from altera_api.classification_v2.nevo_rules import (
    NevoCandidate,
    _head_concept,
    concept_of,
    concept_query_phrase,
    gate_candidate,
)


def _g(product: str, candidate: str) -> bool:
    return gate_candidate(product, NevoCandidate("X", candidate)).accepted


# ---------------------------------------------------------------------------
# Part A — new concepts resolve.
# ---------------------------------------------------------------------------
class TestConcepts:
    @pytest.mark.parametrize(
        "product,concept",
        [
            ("Tortillas Maïs Paprika 200g", "tortilla_crisps"),
            ("Tortillas Blé Nature x8", "tortilla_wrap"),
            ("Pâte à Tartiner Cacao Noisette 400g", "chocolate_hazelnut_spread"),
            ("Vinaigrette Balsamique 50cl", "vinaigrette"),
            ("Purée Mousseline Nature 4 sachets", "potato"),
            ("Madeleines Coquilles x18", "madeleine"),
            ("Moutarde à l'Ancienne 350g", "mustard"),
            ("Œufs Plein Air x12", "egg"),
        ],
    )
    def test_product_concept(self, product, concept) -> None:
        assert concept_of(product) == concept


class TestSafeMatches:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            ("Tortillas Maïs Paprika 200g", "Crisps tortilla unflavoured"),
            ("Tortillas Blé Nature x8", "Wrap/tortilla wheat white"),
            ("Pâte à Tartiner Cacao Noisette", "Spread chocolate hazelnut"),
            ("Vinaigrette Balsamique 50cl", "Salad dressing vinaigrette"),
            ("Purée Mousseline Nature", "Potato puree powder av"),
            ("Moutarde à l'Ancienne", "Mustard"),
            ("Œufs Plein Air x12", "Egg whole chicken av raw"),
        ],
    )
    def test_accepts_real_reference(self, product, candidate) -> None:
        assert _g(product, candidate)


# ---------------------------------------------------------------------------
# Self-product exception — a dish-noun candidate that IS the product matches,
# but a different-concept product (chocolate bar) does NOT match the spread.
# ---------------------------------------------------------------------------
class TestSelfProductException:
    def test_self_product_head_concepts(self) -> None:
        assert _head_concept("Spread chocolate hazelnut") == "chocolate_hazelnut_spread"
        assert _head_concept("Wrap/tortilla wheat white") == "tortilla_wrap"
        assert _head_concept("Salad dressing vinaigrette") == "vinaigrette"

    def test_chocolate_bar_does_not_match_spread(self) -> None:
        # "Spread chocolate dark" is NOT an allow-listed self-product → its
        # head stays None, so a dark-chocolate BAR never matches it.
        assert _head_concept("Spread chocolate dark") is None
        assert not _g("Chocolat Noir", "Spread chocolate hazelnut")

    def test_vinaigrette_avoids_plain_vinegar(self) -> None:
        # A dressing product resolves to vinaigrette (not vinegar) and does
        # not match a plain vinegar reference.
        assert concept_of("Vinaigrette Balsamique") == "vinaigrette"
        assert not _g("Vinaigrette Balsamique", "Vinegar Balsamic")


# ---------------------------------------------------------------------------
# Part B — concept query-alias injection (retrieval ranking only).
# ---------------------------------------------------------------------------
class TestQueryInjection:
    @pytest.mark.parametrize(
        "product,needle",
        [
            ("Petits Pois Surgelés", "green peas"),
            ("Cabillaud Pané Citron", "cod cabillaud fish"),
            ("Taboulé Oriental", "tabbouleh"),
            ("Pâte à Tartiner Cacao Noisette", "spread chocolate hazelnut"),
            ("Œufs Plein Air x12", "egg"),
        ],
    )
    def test_query_phrase(self, product, needle) -> None:
        assert needle in (concept_query_phrase(product) or "")

    def test_query_text_appends_concept_phrase(self) -> None:
        text = build_nevo_query_text({"product_name": "Petits Pois Surgelés"})
        assert "Concept: green peas" in text

    def test_query_text_still_excludes_commercial(self) -> None:
        from altera_api.embeddings.text_builder import ForbiddenEmbeddingField

        with pytest.raises(ForbiddenEmbeddingField):
            build_nevo_query_text({"product_name": "Petits Pois", "items_sold": 5})

    def test_no_concept_no_injection(self) -> None:
        text = build_nevo_query_text({"product_name": "Mystery Box XYZ"})
        assert "Concept:" not in text


# ---------------------------------------------------------------------------
# Part D — traps stay rejected.
# ---------------------------------------------------------------------------
class TestTraps:
    @pytest.mark.parametrize(
        "product,candidate",
        [
            # vinegar vs vinaigrette: a vinaigrette product never matches plain
            # vinegar (covered above); a vinegar product never matches a salad
            # dressing.
            ("Vinaigre de Cidre", "Salad dressing vinaigrette"),
            # cod product must not match lemon just because "citron" is present.
            ("Cabillaud Pané Citron", "Lemon juice"),
            ("Cabillaud Pané Citron", "Croissant butter"),
            # taboulé must not match mint/herbs.
            ("Taboulé Oriental Menthe", "Mint fresh"),
            # petits pois must not match baby biscuits.
            ("Petits Pois Extra Fins", "Biscuit Liga Baby 6-12 months"),
            # pâte à tartiner must not match plain cocoa / chocolate bar.
            ("Pâte à Tartiner Cacao Noisette", "Cocoa powder"),
            ("Pâte à Tartiner Cacao Noisette", "Chocolate dark"),
            # tortillas must not match unrelated corn/wheat ingredients.
            ("Tortillas Maïs Paprika", "Corn starch"),
            ("Tortillas Blé Nature", "Flour wheat white"),
        ],
    )
    def test_rejects_trap(self, product, candidate) -> None:
        assert not _g(product, candidate)


# ---------------------------------------------------------------------------
# Part C — policy abstains kept.
# ---------------------------------------------------------------------------
class TestPolicyAbstains:
    @pytest.mark.parametrize(
        "product",
        [
            "Eau Pétillante Citron Vert",
            "Nectar Mangue Passion",
            "Liquide Vaisselle Citron",
            "Litière Chat Agglomérante",
            "Shampooing Anti-Pelliculaire",
            "Pâtée Chien Boeuf Légumes",
        ],
    )
    def test_no_concept_so_abstains(self, product) -> None:
        assert concept_of(product) is None
        assert concept_query_phrase(product) is None
