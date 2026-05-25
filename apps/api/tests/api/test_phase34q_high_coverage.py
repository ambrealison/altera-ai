"""Phase 34Q — High-coverage AI food classification.

Areas under test:

A. The PT system prompt instructs the model that almost every row is
   food and out_of_scope/unknown are reserved for genuine edge cases.

B. The PT system prompt carries the critical examples that drove the
   Phase 34P false-out_of_scope rate (poulet végétal, burger végétal &
   emmental, salade poulet césar, chips nature, huile d'olive).

C. ``_looks_like_food`` detects obvious food product names.

D. A food-looking product wrongly returned as ``out_of_scope`` is
   converted to retry-worthy parse_failed (the food guard).

E. A batch with >10% out_of_scope or >10% unknown triggers a quality
   retry; >40% review_required also triggers.

F. Low-confidence rows still carry a category. They contribute to
   ``categorized_total`` but also to ``review_required_total`` —
   review is NOT the same as uncategorized.

G. Auto-accept threshold is 0.70 (lowered from 0.80 in Phase 34Q).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from altera_api.ai.batch_classifier import (
    OUT_OF_SCOPE_RATE_TRIGGER,
    REVIEW_RATE_TRIGGER,
    UNKNOWN_RATE_TRIGGER,
    _looks_like_food,
    batch_classify,
)
from altera_api.ai.batch_prompt import (
    _PT_SYSTEM,
    _WWF_SYSTEM,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
)
from altera_api.ai.provider import ClassifierProvider, ProviderResponse
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct, PTProductFields
from altera_api.domain.protein_tracker import ProteinTrackerGroup


def _make_product(name: str) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        row_number=1,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        weight_per_item_kg=Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("1")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A + B. Prompt content
# ---------------------------------------------------------------------------


class TestPromptContent:
    def test_pt_prompt_states_high_coverage_philosophy(self) -> None:
        # The CRITICAL rule must appear verbatim or close to it.
        assert "out_of_scope" in _PT_SYSTEM
        assert "clearly non-food" in _PT_SYSTEM.lower()
        assert "uncertainty" in _PT_SYSTEM.lower()
        # The model must be told plant-meat alternatives are NOT animal.
        assert "poulet végétal" in _PT_SYSTEM.lower() or (
            "poulet" in _PT_SYSTEM.lower() and "végétal" in _PT_SYSTEM.lower()
        )

    def test_pt_prompt_includes_phase34q_examples(self) -> None:
        # The critical false-out_of_scope examples must be in the prompt.
        examples = [
            "poulet végétal",
            "burger végétal",
            "salade poulet césar",
            "chips nature",
            "huile d'olive",
        ]
        lowered = _PT_SYSTEM.lower()
        for ex in examples:
            assert ex in lowered, f"prompt missing example: {ex!r}"

    def test_wwf_prompt_states_high_coverage_philosophy(self) -> None:
        assert "clearly non-food" in _WWF_SYSTEM.lower()
        assert "out_of_scope" in _WWF_SYSTEM


# ---------------------------------------------------------------------------
# C. _looks_like_food
# ---------------------------------------------------------------------------


class TestLooksLikeFood:
    def test_detects_obvious_food_names(self) -> None:
        for name in [
            "Pommes Golden",
            "Carottes Sachet 1kg",
            "Blanc de Poulet",
            "Filets de Saumon",
            "Tofu Nature",
            "Huile d'Olive",
            "Chips Nature",
            "Chocolat au Lait",
            "Yaourt Nature",
            "Boisson Avoine — Plain",
            "Pâtes Spaghetti",
        ]:
            assert _looks_like_food(name), f"should detect food: {name!r}"

    def test_non_food_products_do_not_match(self) -> None:
        # Eau Minérale Naturelle now does NOT match (we removed
        # "nature"/"naturel" from the strong-food list to avoid false
        # positives on water/condiments).
        for name in [
            "Lessive Liquide 3L",
            "Piles AA Lot de 4",
            "Eau Minérale Naturelle 1.5L",
            "",
            "SKU-42",
        ]:
            assert not _looks_like_food(name), f"false positive: {name!r}"


# ---------------------------------------------------------------------------
# Scripted provider for the orchestrator tests
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedBundleProvider(ClassifierProvider):
    """Returns whatever raw envelopes were scripted, one per call."""

    responses: list[str]
    model_name: str = "phase34q-fake"
    _idx: int = field(default=0, init=False)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: Any) -> ProviderResponse:  # pragma: no cover
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        idx = min(self._idx, len(self.responses) - 1)
        self._idx += 1
        return ProviderResponse(
            raw_text=self.responses[idx], model=self.model_name
        )


def _envelope(rows: list[dict[str, object]]) -> str:
    import json

    return json.dumps({"results": rows})


# ---------------------------------------------------------------------------
# D. Food-guard rejects out_of_scope on food-looking products
# ---------------------------------------------------------------------------


class TestFoodGuard:
    def test_food_product_wrongly_out_of_scope_is_retried(self) -> None:
        # The model returns out_of_scope for "Huile d'Olive" (a food).
        # The food guard converts that to parse_failed; the retry pass
        # then runs at retry_batch_size and (since the scripted fake
        # keeps saying out_of_scope) ultimately ends as parse_failed.
        # The point: the verdict is NOT silently accepted as
        # out_of_scope.
        prod = _make_product("Huile d'Olive Vierge Extra")
        env = _envelope(
            [
                {
                    "id": str(prod.id),
                    "pt_group": "out_of_scope",
                    "confidence": 0.95,
                    "rationale": "x",
                }
            ]
        )
        provider = _ScriptedBundleProvider(responses=[env, env, env, env])
        bundle = batch_classify(
            [prod],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=5,
            retry_batch_size=2,
        )
        # Final verdict is NOT AIAccepted with out_of_scope.
        v = bundle.verdicts[0]
        if isinstance(v, AIAccepted):
            assert v.classification.pt_group is not ProteinTrackerGroup.OUT_OF_SCOPE
        else:
            assert isinstance(v, AINeedsReviewParseFailed)

    def test_non_food_product_legitimately_out_of_scope_passes(self) -> None:
        prod = _make_product("Lessive Liquide 3L")
        env = _envelope(
            [
                {
                    "id": str(prod.id),
                    "pt_group": "out_of_scope",
                    "confidence": 0.97,
                    "rationale": "household",
                }
            ]
        )
        provider = _ScriptedBundleProvider(responses=[env])
        bundle = batch_classify(
            [prod],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=5,
            retry_batch_size=2,
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert isinstance(v, AIAccepted)
        assert v.classification.pt_group is ProteinTrackerGroup.OUT_OF_SCOPE


# ---------------------------------------------------------------------------
# E. Rate-based retry triggers
# ---------------------------------------------------------------------------


class TestRateTriggers:
    def test_triggers_exposed(self) -> None:
        assert OUT_OF_SCOPE_RATE_TRIGGER == 0.10
        assert UNKNOWN_RATE_TRIGGER == 0.10
        assert REVIEW_RATE_TRIGGER == 0.40

    def test_high_out_of_scope_rate_triggers_retry(self) -> None:
        # 10 non-food-looking products, model returns out_of_scope on
        # 30% of them with no food terms in names — but the quality
        # retry still kicks in because the rate exceeds the trigger,
        # and the retry pass marks them for retry. We can't easily
        # assert "retry happened" without the provider tracking call
        # count, but we CAN check the sample_errors stream.
        prods = [_make_product(f"X{i}") for i in range(10)]
        rows = []
        for i, p in enumerate(prods):
            cat = "out_of_scope" if i < 3 else "plant_based_core"
            rows.append(
                {
                    "id": str(p.id),
                    "pt_group": cat,
                    "confidence": 0.95,
                    "rationale": "x",
                }
            )
        env = _envelope(rows)
        provider = _ScriptedBundleProvider(responses=[env, env, env, env])
        bundle = batch_classify(
            prods,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=20,
            retry_batch_size=5,
        )
        # The quality_retry_trigger sample message must have been emitted.
        assert any(
            "quality_retry_trigger" in s for s in bundle.sample_errors
        ), f"trigger not fired; samples={bundle.sample_errors}"

    def test_low_rates_do_not_trigger_retry(self) -> None:
        prods = [_make_product(f"X{i}") for i in range(10)]
        rows = [
            {
                "id": str(p.id),
                "pt_group": "plant_based_core",
                "confidence": 0.95,
                "rationale": "x",
            }
            for p in prods
        ]
        provider = _ScriptedBundleProvider(responses=[_envelope(rows)])
        bundle = batch_classify(
            prods,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=20,
            retry_batch_size=5,
        )
        assert not any(
            "quality_retry_trigger" in s for s in bundle.sample_errors
        )
        # All accepted, no retries.
        assert all(isinstance(v, AIAccepted) for v in bundle.verdicts)


# ---------------------------------------------------------------------------
# F + G. Threshold + low-confidence still categorized
# ---------------------------------------------------------------------------


class TestThresholdAndLowConfidence:
    def test_threshold_is_seventy_percent(self) -> None:
        prod = _make_product("Carottes Sachet 1kg")
        # confidence 0.75 — above new 0.70 threshold, below old 0.80.
        env = _envelope(
            [
                {
                    "id": str(prod.id),
                    "pt_group": "plant_based_core",
                    "confidence": 0.75,
                    "rationale": "x",
                }
            ]
        )
        provider = _ScriptedBundleProvider(responses=[env])
        bundle = batch_classify(
            [prod],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=5,
            retry_batch_size=2,
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert isinstance(v, AIAccepted), (
            "0.75 should be auto-accepted under Phase 34Q threshold (0.70)"
        )

    def test_low_confidence_still_carries_a_category(self) -> None:
        prod = _make_product("Salade Composée Bizarre")
        env = _envelope(
            [
                {
                    "id": str(prod.id),
                    "pt_group": "composite_products",
                    "confidence": 0.55,  # below 0.70 → review
                    "rationale": "x",
                }
            ]
        )
        provider = _ScriptedBundleProvider(responses=[env])
        bundle = batch_classify(
            [prod],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            batch_size=5,
            retry_batch_size=2,
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        # Low-confidence is review — but it still has a concrete
        # category (composite_products), NOT unknown.
        assert isinstance(v, AINeedsReviewLowConfidence)
        assert (
            v.classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS
        )
