"""End-to-end orchestrator tests covering each verdict branch."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
    classify_pt,
    classify_wwf,
)
from altera_api.ai.fakes import (
    EventuallyValidFakeProvider,
    FailingFakeProvider,
    KeywordFakeProvider,
    RaisingFakeProvider,
    ScriptedFakeProvider,
    StaticFakeProvider,
)
from altera_api.domain.common import ClassificationSource
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)


def _pt_response(group: str = "plant_based_core", confidence: float = 0.92) -> str:
    return json.dumps(
        {
            "methodology": "protein_tracker",
            "pt_group": group,
            "confidence": confidence,
            "rationale": "test",
        }
    )


def _wwf_response(food_group: str = "FG1", confidence: float = 0.92) -> str:
    return json.dumps(
        {
            "methodology": "wwf",
            "wwf_food_group": food_group,
            "wwf_is_composite": False,
            "wwf_fg1_subgroup": "red_meat",
            "confidence": confidence,
            "rationale": "test",
        }
    )


class TestHappyPath:
    def test_pt_accepted(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = StaticFakeProvider(raw_text=_pt_response())
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AIAccepted)
        assert isinstance(verdict.classification, ProteinTrackerProductClassification)
        assert verdict.classification.source is ClassificationSource.AI
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert verdict.classification.confidence == Decimal("0.92")
        assert verdict.classification.ai_model == "fake-static-v1"
        assert verdict.classification.ai_prompt_version == "classifier_v1"
        assert verdict.parse_failures == 0

    def test_wwf_accepted(self, wwf_product: NormalizedProduct, now: datetime) -> None:
        provider = StaticFakeProvider(raw_text=_wwf_response())
        verdict = classify_wwf(wwf_product, provider, now=now)
        assert isinstance(verdict, AIAccepted)
        assert isinstance(verdict.classification, WWFProductClassification)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.RED_MEAT


class TestBelowThreshold:
    def test_pt_below_threshold(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = StaticFakeProvider(raw_text=_pt_response(confidence=0.5))
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AINeedsReviewLowConfidence)
        assert verdict.threshold == Decimal("0.8")
        # The classification still exists.
        assert verdict.classification.confidence == Decimal("0.5")

    def test_custom_threshold_above(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = StaticFakeProvider(raw_text=_pt_response(confidence=0.5))
        verdict = classify_pt(pt_product, provider, now=now, threshold=Decimal("0.3"))
        assert isinstance(verdict, AIAccepted)


class TestParseFailureRetry:
    def test_retry_then_success(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = EventuallyValidFakeProvider(valid_text=_pt_response(), invalid_calls=1)
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AIAccepted)
        assert verdict.parse_failures == 1

    def test_two_failures_route_to_review(
        self, pt_product: NormalizedProduct, now: datetime
    ) -> None:
        provider = FailingFakeProvider()
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AINeedsReviewParseFailed)
        assert verdict.first_error
        assert verdict.second_error
        assert verdict.product_id == pt_product.id

    def test_methodology_mismatch_counts_as_parse_failure(
        self, pt_product: NormalizedProduct, now: datetime
    ) -> None:
        # The AI returns a WWF response for a PT prompt — should fail
        # twice and route to review.
        provider = StaticFakeProvider(raw_text=_wwf_response())
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AINeedsReviewParseFailed)


class TestProviderError:
    def test_surfaces_provider_error(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = RaisingFakeProvider(message="simulated 502")
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AIProviderError)
        assert "502" in verdict.message
        assert verdict.product_id == pt_product.id

    def test_provider_error_short_circuits_retry(
        self, pt_product: NormalizedProduct, now: datetime
    ) -> None:
        # Even with the parse-retry budget, a ProviderError on first call
        # surfaces immediately; the orchestrator does not retry through
        # provider failures.
        provider = ScriptedFakeProvider(responses=())  # exhausted on first call
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AIProviderError)


class TestKeywordIntegration:
    def test_keyword_driven_pt(self, pt_product: NormalizedProduct, now: datetime) -> None:
        provider = KeywordFakeProvider(
            rules={
                "Mystery": _pt_response(group="composite_products", confidence=0.85),
            },
            default=_pt_response(group="unknown", confidence=0.2),
        )
        verdict = classify_pt(pt_product, provider, now=now)
        assert isinstance(verdict, AIAccepted)
        assert verdict.classification.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS


class TestPolicyAtClassify:
    """The orchestrator must never let commercial data reach the provider."""

    def test_provider_only_sees_allowed_fields(
        self, pt_product: NormalizedProduct, now: datetime
    ) -> None:
        seen_payloads: list[dict[str, object]] = []

        from altera_api.ai.provider import ClassifierProvider, ProviderResponse

        class SpyProvider(ClassifierProvider):
            @property
            def model(self) -> str:
                return "spy-v1"

            def classify(self, prompt) -> ProviderResponse:
                seen_payloads.append(dict(prompt.product_card))
                return ProviderResponse(raw_text=_pt_response(), model="spy-v1")

        verdict = classify_pt(pt_product, SpyProvider(), now=now)
        assert isinstance(verdict, AIAccepted)
        assert len(seen_payloads) == 1
        payload = seen_payloads[0]
        forbidden = {
            "items_purchased",
            "items_sold",
            "weight_per_item_kg",
            "protein_pct",
            "plant_protein_pct",
            "animal_protein_pct",
            "revenue",
            "margin",
        }
        assert forbidden.isdisjoint(payload.keys())
