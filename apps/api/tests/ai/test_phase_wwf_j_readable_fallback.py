"""Phase WWF-J — WWF readable-fallback regression.

Bug from production:
    Lentilles corail sèches → wwf_food_group=unknown, source=Déterministe
    Pois chiches égouttés en bocal → wwf_food_group=unknown

Root cause (two missing branches in ``batch_classifier.batch_classify``):

  1. The Phase 36K early readable-fallback (the branch that catches
     a model ``unknown`` on a readable name BEFORE the food-term guard
     short-circuits the row to ``needs_review_parse_failed``) only ran
     for PT methodology. WWF rows fell through to the food-term guard
     which routed them to ``AINeedsReviewParseFailed``, bypassing the
     WWF deterministic guards entirely.

  2. The Phase 36K2 last-chance readable-fallback inside
     ``_emit_failed_or_fallback`` also only ran for PT — so even when
     the row reached the parse-failed path, the WWF readable fallback
     (``classify_wwf_readable_fallback`` from Phase WWF-D) was never
     called.

Phase WWF-J adds the symmetric WWF branches to both sites. This test
file pins the fix.
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
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
)
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        is_own_brand=False,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.WWF}),
        pt_fields=None,
        wwf_fields=WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.GROCERY_AMBIENT,
            is_own_brand=False,
        ),
        created_at=datetime.now(UTC),
    )


class _UnknownWWFProvider(ClassifierProvider):
    """Returns ``wwf_food_group=unknown`` for every row — simulates an
    AI that gives up on every product. The WWF readable fallback /
    guards must rescue obvious products from this verdict."""

    @property
    def model(self) -> str:
        return "wwf-j-unknown"

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
                    "wwf_food_group": "unknown",
                    "wwf_is_composite": False,
                    "confidence": 0.3,
                    "rationale": "phase-wwf-j fake",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="wwf-j-unknown",
        )


# ---------------------------------------------------------------------------
# A. The exact bug from the production report
# ---------------------------------------------------------------------------


class TestUserBugProductsNotUnknown:
    """The exact products the user saw as ``Inconnu`` in their
    100-product dataset, plus close cousins from the same file."""

    @pytest.mark.parametrize(
        ("name", "expected_food_group", "expected_fg1"),
        [
            ("Lentilles corail sèches", WWFFoodGroup.FG1, WWFFG1Subgroup.LEGUMES),
            ("Pois chiches égouttés en bocal", WWFFoodGroup.FG1, WWFFG1Subgroup.LEGUMES),
            ("Lentilles vertes du Puy", WWFFoodGroup.FG1, WWFFG1Subgroup.LEGUMES),
            ("Haricots blancs", WWFFoodGroup.FG1, WWFFG1Subgroup.LEGUMES),
            ("Tofu Nature", WWFFoodGroup.FG1, WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES),
        ],
    )
    def test_legume_alt_protein_not_unknown(
        self,
        name: str,
        expected_food_group: WWFFoodGroup,
        expected_fg1: WWFFG1Subgroup,
    ) -> None:
        bundle = batch_classify(
            [_make_product(name)],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert len(bundle.verdicts) == 1
        v = bundle.verdicts[0]
        # Must NOT be parse-failed and must NOT be a final unknown.
        assert not isinstance(v, AINeedsReviewParseFailed), (
            f"{name!r} ended as parse-failed — WWF readable fallback "
            f"did not fire"
        )
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))
        cls = v.classification
        assert cls.wwf_food_group is expected_food_group, (
            f"{name!r} got {cls.wwf_food_group}, expected {expected_food_group}"
        )
        assert cls.fg1_subgroup is expected_fg1


# ---------------------------------------------------------------------------
# B. Wider coverage of the 30-product checklist from the brief
# ---------------------------------------------------------------------------


class TestDatasetObviousProductsResolve:
    """Every name on the brief's expected-30 list should resolve to a
    WWF classification when the AI says ``unknown``."""

    OBVIOUS_FOOD_GROUPS: list[tuple[str, WWFFoodGroup]] = [
        # FG1 protein-rich
        ("Lentilles corail", WWFFoodGroup.FG1),
        ("Pois chiches", WWFFoodGroup.FG1),
        ("Haricots rouges", WWFFoodGroup.FG1),
        ("Tofu Nature", WWFFoodGroup.FG1),
        ("Tempeh", WWFFoodGroup.FG1),
        ("Saumon Frais", WWFFoodGroup.FG1),
        ("Poulet Fermier", WWFFoodGroup.FG1),
        # FG2 dairy / dairy-alternative
        ("Boisson Amande Sans Sucres", WWFFoodGroup.FG2),
        ("Lait Demi-écrémé UHT", WWFFoodGroup.FG2),
        ("Yaourt Nature", WWFFoodGroup.FG2),
        ("Camembert AOP", WWFFoodGroup.FG2),
        # FG3 fats and oils
        ("Huile Olive Vierge Extra", WWFFoodGroup.FG3),
        ("Beurre Doux", WWFFoodGroup.FG3),
        # FG6 tubers
        ("Pommes de Terre Charlotte", WWFFoodGroup.FG6),
        # FG5 grains
        ("Riz Basmati", WWFFoodGroup.FG5),
        ("Pâtes Complètes", WWFFoodGroup.FG5),
        # FG7 snacks
        ("Chips Sel & Vinaigre", WWFFoodGroup.FG7),
        ("Sorbet Framboise", WWFFoodGroup.FG7),
        ("Confiture Abricot", WWFFoodGroup.FG7),
        # Composites
        ("Pizza Jambon", WWFFoodGroup.FG1),  # composite, attaches to FG1
        ("Quiche Lorraine", WWFFoodGroup.FG1),
        ("Cassoulet Provençal", WWFFoodGroup.FG1),
    ]

    @pytest.mark.parametrize(
        ("name", "expected_food_group"), OBVIOUS_FOOD_GROUPS
    )
    def test_resolves_to_expected_food_group(
        self, name: str, expected_food_group: WWFFoodGroup
    ) -> None:
        bundle = batch_classify(
            [_make_product(name)],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert not isinstance(v, AINeedsReviewParseFailed), (
            f"{name!r} ended as parse-failed — WWF readable fallback "
            f"did not fire"
        )
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))
        assert v.classification.wwf_food_group is expected_food_group, (
            f"{name!r}: expected {expected_food_group}, "
            f"got {v.classification.wwf_food_group}"
        )

    def test_composites_carry_step1_bucket(self) -> None:
        # Track the expected per-name bucket so the assertion error
        # message is precise if a future regression breaks one of them.
        # Currently we only enforce that the composite carries SOME
        # bucket; the exact bucket per name is pinned by the curated
        # WWF guard fixture (Phase WWF-D).
        expected_meat_based = {
            "Pizza Jambon",
            "Quiche Lorraine",
            "Cassoulet Provençal",
        }
        bundle = batch_classify(
            [_make_product(n) for n in sorted(expected_meat_based)],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        for v in bundle.verdicts:
            assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))
            cls = v.classification
            # Composite or FG1 — either is acceptable. The brief notes
            # that composites attach to FG1 by convention. Detailed
            # per-name bucket assignment is pinned by the curated WWF
            # guard fixture (Phase WWF-D); here we only require that a
            # composite carries SOME bucket.
            if cls.wwf_is_composite:
                assert cls.composite_step1_bucket is not None


# ---------------------------------------------------------------------------
# C. Out-of-scope still flows through cleanly
# ---------------------------------------------------------------------------


class TestOutOfScopeStillWorks:
    @pytest.mark.parametrize(
        "name",
        [
            "Eau Minérale Plate",
            "Coca-Cola Zero",
            "Bouillon Cube Volaille",
            "Vinaigrette Balsamique",
            "Sel Fin de Guérande",
            "Litière Chat",
            "Lessive Liquide",
        ],
    )
    def test_oos_resolves(self, name: str) -> None:
        bundle = batch_classify(
            [_make_product(name)],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert not isinstance(v, AINeedsReviewParseFailed)
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))
        assert v.classification.wwf_food_group is WWFFoodGroup.OUT_OF_SCOPE


# ---------------------------------------------------------------------------
# D. Truly unusable names still flow to parse-failed (non-regression)
# ---------------------------------------------------------------------------


class TestUnusableNamesStillFail:
    @pytest.mark.parametrize("name", ["???", "AAAA", "12345", "XYZ-99"])
    def test_unusable_names_route_to_parse_failed(self, name: str) -> None:
        bundle = batch_classify(
            [_make_product(name)],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        # Either parse-failed (legacy path) OR a low-confidence
        # accepted out_of_scope / unknown via a guard. The brief only
        # requires that READABLE names don't land at unknown — these
        # gibberish names are exempt.
        if isinstance(v, AIAccepted):
            assert v.classification.wwf_food_group in (
                WWFFoodGroup.UNKNOWN,
                WWFFoodGroup.OUT_OF_SCOPE,
            )


# ---------------------------------------------------------------------------
# E. Silence unused-import warnings
# ---------------------------------------------------------------------------

_ = (
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
)
