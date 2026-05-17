from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
)
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.review.routing import route_pt_verdict, route_wwf_verdict
from altera_api.rules.engine import (
    PTMatched,
    PTPassThrough,
    PTRuleCollision,
    WWFPassThrough,
    WWFRuleCollision,
)


def _pt_classification(now: datetime, product_id: UUID) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product_id,
        pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
        source=ClassificationSource.AI,
        confidence=Decimal("0.5"),
        ai_prompt_version="v1",
        ai_model="m",
        updated_at=now,
    )


def _wwf_classification(now: datetime, product_id: UUID) -> WWFProductClassification:
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=WWFFoodGroup.FG1,
        wwf_is_composite=False,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        source=ClassificationSource.AI,
        confidence=Decimal("0.5"),
        ai_prompt_version="v1",
        ai_model="m",
        updated_at=now,
    )


class TestRoutePT:
    def test_rule_collision_routed(
        self, pt_product_id: UUID, now: datetime
    ) -> None:
        verdict = PTRuleCollision(
            product_id=pt_product_id,
            conflicting_rule_ids=("r.a", "r.b"),
            conflicting_categories=(
                ProteinTrackerGroup.PLANT_BASED_CORE,
                ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ),
        )
        item = route_pt_verdict(verdict, now=now)
        assert item is not None
        assert item.reason is ManualReviewQueueReason.RULE_COLLISION
        assert item.status is ManualReviewStatus.IN_QUEUE
        assert item.methodology is Methodology.PROTEIN_TRACKER

    def test_low_confidence_routed(
        self, pt_product_id: UUID, now: datetime
    ) -> None:
        ai = AINeedsReviewLowConfidence(
            classification=_pt_classification(now, pt_product_id),
            raw_text="{}",
            threshold=Decimal("0.8"),
        )
        item = route_pt_verdict(ai_verdict=ai, now=now)
        assert item is not None
        assert item.reason is ManualReviewQueueReason.LOW_CONFIDENCE
        assert item.product_id == pt_product_id

    def test_parse_failed_routed(
        self, pt_product_id: UUID, now: datetime
    ) -> None:
        ai = AINeedsReviewParseFailed(
            product_id=pt_product_id,
            methodology=Methodology.PROTEIN_TRACKER,
            first_error="bad",
            second_error="bad",
        )
        item = route_pt_verdict(ai_verdict=ai, now=now)
        assert item is not None
        assert item.reason is ManualReviewQueueReason.AI_PARSE_FAILED

    def test_matched_not_routed(self, pt_product_id: UUID, now: datetime) -> None:
        matched = PTMatched(
            classification=_pt_classification(now, pt_product_id).model_copy(
                update={
                    "source": ClassificationSource.DETERMINISTIC,
                    "confidence": Decimal("1"),
                    "rule_id": "pt.r",
                    "ai_prompt_version": None,
                    "ai_model": None,
                }
            ),
            fired_rule_ids=("pt.r",),
        )
        assert route_pt_verdict(matched, now=now) is None

    def test_pass_through_not_routed(self, pt_product_id: UUID, now: datetime) -> None:
        assert route_pt_verdict(PTPassThrough(product_id=pt_product_id), now=now) is None

    def test_ai_accepted_not_routed(self, pt_product_id: UUID, now: datetime) -> None:
        accepted = AIAccepted(
            classification=_pt_classification(now, pt_product_id),
            raw_text="{}",
        )
        assert route_pt_verdict(ai_verdict=accepted, now=now) is None

    def test_provider_error_not_routed(
        self, pt_product_id: UUID, now: datetime
    ) -> None:
        err = AIProviderError(
            product_id=pt_product_id,
            methodology=Methodology.PROTEIN_TRACKER,
            message="502",
        )
        assert route_pt_verdict(ai_verdict=err, now=now) is None


class TestRouteWWF:
    def test_rule_collision_routed(
        self, wwf_product_id: UUID, now: datetime
    ) -> None:
        verdict = WWFRuleCollision(
            product_id=wwf_product_id,
            conflicting_rule_ids=("a", "b"),
        )
        item = route_wwf_verdict(verdict, now=now)
        assert item is not None
        assert item.reason is ManualReviewQueueReason.RULE_COLLISION
        assert item.methodology is Methodology.WWF

    def test_low_confidence_routed(
        self, wwf_product_id: UUID, now: datetime
    ) -> None:
        ai = AINeedsReviewLowConfidence(
            classification=_wwf_classification(now, wwf_product_id),
            raw_text="{}",
            threshold=Decimal("0.8"),
        )
        item = route_wwf_verdict(ai_verdict=ai, now=now)
        assert item is not None
        assert item.reason is ManualReviewQueueReason.LOW_CONFIDENCE
        assert item.product_id == wwf_product_id

    def test_pass_through_not_routed(self, wwf_product_id: UUID, now: datetime) -> None:
        assert route_wwf_verdict(WWFPassThrough(product_id=wwf_product_id), now=now) is None
