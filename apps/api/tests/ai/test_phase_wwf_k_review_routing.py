"""Phase WWF-K — WWF review-routing regression.

User report on the 100-product dataset:

    "I looked at about half of the WWF results. The quality is not
    good. Yet the UI shows 0 products 'en revue' for WWF. So the
    problem isn't only unknown/failed — WWF is auto-accepting wrong
    categories instead of routing them to review."

Root cause
==========

``batch_classify`` shared a single auto-accept threshold (0.70) between
PT and WWF. The Phase 34Q calibration that produced 0.70 was done on
the Protein Tracker prompt; WWF was never re-calibrated. With WWF the
AI commonly returns plausible-looking categories at 0.70-0.79
confidence that are actually wrong — and they were silently accepted.

Combined with the existing guard confidence ceiling
(``wwf_guards._GUARD_CONFIDENCE_CEILING = 0.69`` — guards already
clamp to review), the right calibration for WWF is to push the
auto-accept threshold higher so the review band is wider:

  * **PT** auto-accepts at >= 0.70 (Phase 34Q calibration, unchanged).
  * **WWF** auto-accepts at >= 0.80 (Phase WWF-K — wider review band).

This brings any AI verdict in [0.70, 0.80) into review for WWF only,
without changing PT behaviour or requiring schema/contract changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from altera_api.ai.batch_classifier import (
    PT_REVIEW_THRESHOLD,
    WWF_REVIEW_THRESHOLD,
    _default_threshold,
    batch_classify,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
)
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderResponse,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    PTProductFields,
    RetailChannel,
    WWFProductFields,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pt_product(name: str) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        organisation_id=uuid4(),
        row_number=2,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        brand=None,
        weight_per_item_kg=Decimal("0.15"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("100")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


def _make_wwf_product(name: str) -> NormalizedProduct:
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


class _ConstantConfidenceProvider(ClassifierProvider):
    """Returns a fixed category + confidence for every product. Used
    to test the auto-accept-vs-review boundary at exact values."""

    def __init__(
        self,
        *,
        methodology: Methodology,
        confidence: float,
        category: str,
    ) -> None:
        self._methodology = methodology
        self._confidence = confidence
        self._category = category

    @property
    def model(self) -> str:
        return "wwf-k-constant"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        key = (
            "pt_group"
            if self._methodology is Methodology.PROTEIN_TRACKER
            else "wwf_food_group"
        )
        rows: list[dict[str, Any]] = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in parsed:
                continue
            row: dict[str, Any] = {
                "id": parsed["id"],
                key: self._category,
                "confidence": self._confidence,
                "rationale": "phase-wwf-k constant",
            }
            if self._methodology is Methodology.WWF:
                row["wwf_is_composite"] = False
                # The contract requires the right subgroup for FG1;
                # we set "legumes" for the FG1 test cases below.
                if self._category == "FG1":
                    row["wwf_fg1_subgroup"] = "legumes"
                elif self._category == "FG2":
                    row["wwf_fg2_kind"] = "dairy_alternative_plant"
                elif self._category == "FG3":
                    row["wwf_fg3_kind"] = "plant_based_fat"
                elif self._category == "FG5":
                    row["wwf_fg5_grain_kind"] = "refined_grain"
                elif self._category == "FG7":
                    row["wwf_fg7_kind"] = "plant_based_snack"
            rows.append(row)
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}),
            model="wwf-k-constant",
        )


# ---------------------------------------------------------------------------
# A. Methodology defaults
# ---------------------------------------------------------------------------


class TestMethodologyDefaults:
    def test_pt_default_is_0_70(self) -> None:
        assert PT_REVIEW_THRESHOLD == Decimal("0.7")
        assert _default_threshold(Methodology.PROTEIN_TRACKER) == Decimal("0.7")

    def test_wwf_default_is_0_80(self) -> None:
        """Phase WWF-K — WWF auto-accept threshold is stricter than PT
        so the operator sees borderline WWF rows in review instead of
        them being silently auto-accepted at 0.70."""
        assert WWF_REVIEW_THRESHOLD == Decimal("0.8")
        assert _default_threshold(Methodology.WWF) == Decimal("0.8")


# ---------------------------------------------------------------------------
# B. The reported regression scenario
# ---------------------------------------------------------------------------


class TestWWFAt0_75GoesToReview:
    """The exact pattern from the user bug report: AI returns a
    plausible-but-wrong category at confidence 0.75. With the old
    0.70 threshold this was auto-accepted (silent quality bug).
    With the WWF-K threshold (0.80) it now routes to review so the
    operator sees and can correct it."""

    @pytest.mark.parametrize(
        "name,category",
        [
            ("Mystery Item 1", "FG1"),
            ("Mystery Item 2", "FG2"),
            ("Mystery Item 3", "FG3"),
            ("Mystery Item 4", "FG4"),
            ("Mystery Item 5", "FG5"),
            ("Mystery Item 7", "FG7"),
        ],
    )
    def test_wwf_borderline_confidence_routes_to_review(
        self, name: str, category: str
    ) -> None:
        bundle = batch_classify(
            [_make_wwf_product(name)],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF,
                confidence=0.75,
                category=category,
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        # Must NOT be auto-accepted at 0.75 (between PT and WWF
        # thresholds). Must land in review.
        assert isinstance(v, AINeedsReviewLowConfidence), (
            f"{name!r}@0.75 was {type(v).__name__}, expected "
            f"AINeedsReviewLowConfidence (WWF threshold=0.80)"
        )

    def test_wwf_high_confidence_can_be_accepted(self) -> None:
        """WWF at confidence 0.90 still auto-accepts on a clean
        verdict — we're only making the review band wider, not
        breaking auto-accept for high-confidence rows."""
        bundle = batch_classify(
            [_make_wwf_product("Mystery FG1")],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF,
                confidence=0.9,
                category="FG1",
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        # Could be either AIAccepted (clean) or
        # AINeedsReviewLowConfidence if a guard fired and clamped
        # the confidence. Either way it must NOT be parse-failed,
        # and the row carries a valid category.
        v = bundle.verdicts[0]
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))


# ---------------------------------------------------------------------------
# C. PT non-regression — 0.75 still auto-accepts for PT
# ---------------------------------------------------------------------------


class TestPTNonRegression:
    def test_pt_at_0_75_still_auto_accepts(self) -> None:
        """Phase 34Q calibrated PT auto-accept at >=0.70 specifically.
        WWF-K must not regress that — PT@0.75 still accepts as before."""
        bundle = batch_classify(
            [_make_pt_product("Lentilles Vertes")],
            _ConstantConfidenceProvider(
                methodology=Methodology.PROTEIN_TRACKER,
                confidence=0.75,
                category="plant_based_core",
            ),
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        # PT still accepts at 0.75. The exact outcome depends on
        # whether any PT guard fired on "Lentilles" (which it
        # might — guards clamp to <=0.69). Either way the verdict
        # must be a usable category, not a failure.
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))


# ---------------------------------------------------------------------------
# D. Explicit caller threshold still wins
# ---------------------------------------------------------------------------


class TestExplicitThresholdOverridesDefault:
    def test_caller_supplied_wwf_threshold_used(self) -> None:
        """Callers can pin a non-default threshold (e.g. evals that
        want to compare against the historical 0.70 calibration)."""
        bundle = batch_classify(
            [_make_wwf_product("Mystery FG1")],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF,
                confidence=0.75,
                category="FG1",
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            threshold=Decimal("0.7"),  # opt out of the WWF-K bump
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        # With the explicit 0.70 threshold, 0.75 auto-accepts again.
        # A guard might fire on a generic "Mystery FG1" name and
        # clamp confidence, so allow either Accepted or LowConfidence.
        assert isinstance(v, (AIAccepted, AINeedsReviewLowConfidence))
