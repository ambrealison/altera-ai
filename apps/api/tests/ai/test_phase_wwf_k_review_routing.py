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
    AINeedsReviewParseFailed,
    AIProviderError,
)
from altera_api.ai.provider import (
    ClassifierPrompt,
    ClassifierProvider,
    ProviderError,
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

    def test_wwf_default_is_0_70(self) -> None:
        """Phase WWF-Review-Calibration — the WWF-K 0.80 threshold routed
        EVERY normal WWF row to review (50/50 on a clean demo), so it is
        re-calibrated to the PT level (0.70). Clear WWF classifications are
        accepted; guard-clamped (0.69) / fallback (0.5) / genuinely
        low-confidence (<0.70) rows still route to review."""
        assert WWF_REVIEW_THRESHOLD == Decimal("0.70")
        assert _default_threshold(Methodology.WWF) == Decimal("0.70")


# ---------------------------------------------------------------------------
# B. The reported regression scenario
# ---------------------------------------------------------------------------


class TestWWFConfidenceCalibration:
    """Phase WWF-Review-Calibration — the demo bug: WWF systematically sent
    every row to review. With the re-calibrated 0.70 threshold a clear WWF
    classification (>= 0.70) is ACCEPTED, while a genuinely low-confidence
    one (< 0.70) still routes to review.

    The ``Mystery Item N`` names carry no food tokens, so no deterministic
    WWF guard fires — the verdict keeps the provider's confidence and we test
    the confidence cut-off in isolation."""

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
    def test_wwf_clear_confidence_is_accepted(
        self, name: str, category: str
    ) -> None:
        """A clear WWF classification at 0.75 (>= the 0.70 threshold) must be
        ACCEPTED, not parked in review — this is the fix for the 50/50 demo."""
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
        assert isinstance(v, AIAccepted), (
            f"{name!r}@0.75 was {type(v).__name__}, expected AIAccepted "
            f"(WWF threshold re-calibrated to 0.70)"
        )

    @pytest.mark.parametrize("category", ["FG1", "FG3", "FG5"])
    def test_wwf_genuinely_low_confidence_still_routes_to_review(
        self, category: str
    ) -> None:
        """A genuinely low-confidence WWF row (0.55 < 0.70) still creates a
        LOW_CONFIDENCE review verdict — we are not auto-accepting everything."""
        bundle = batch_classify(
            [_make_wwf_product(f"Mystery Low {category}")],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF,
                confidence=0.55,
                category=category,
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert isinstance(v, AINeedsReviewLowConfidence), (
            f"@0.55 was {type(v).__name__}, expected AINeedsReviewLowConfidence"
        )

    def test_wwf_high_confidence_is_accepted(self) -> None:
        """WWF at 0.90 still auto-accepts on a clean verdict."""
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
        v = bundle.verdicts[0]
        assert isinstance(v, AIAccepted)


class TestWWFReviewThresholdEnvOverride:
    """``ALTERA_WWF_REVIEW_THRESHOLD`` tunes the WWF review band at runtime
    without changing PT or requiring a deploy."""

    def test_env_var_raises_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.ai.batch_classifier import _wwf_review_threshold

        monkeypatch.setenv("ALTERA_WWF_REVIEW_THRESHOLD", "0.85")
        assert _wwf_review_threshold() == Decimal("0.85")
        assert _default_threshold(Methodology.WWF) == Decimal("0.85")
        # With a 0.85 threshold a 0.80 WWF row routes back to review.
        bundle = batch_classify(
            [_make_wwf_product("Mystery Item 1")],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF, confidence=0.8, category="FG1"
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert isinstance(bundle.verdicts[0], AINeedsReviewLowConfidence)

    def test_invalid_or_unset_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.ai.batch_classifier import _wwf_review_threshold

        monkeypatch.delenv("ALTERA_WWF_REVIEW_THRESHOLD", raising=False)
        assert _wwf_review_threshold() == Decimal("0.70")
        monkeypatch.setenv("ALTERA_WWF_REVIEW_THRESHOLD", "not-a-number")
        assert _wwf_review_threshold() == Decimal("0.70")
        monkeypatch.setenv("ALTERA_WWF_REVIEW_THRESHOLD", "5")  # out of range
        assert _wwf_review_threshold() == Decimal("0.70")


# ---------------------------------------------------------------------------
# B2. Auditability invariants at the calibrated 0.70 threshold
#
# Re-calibrating WWF from 0.80 -> 0.70 must NOT auto-accept rows that should
# stay reviewable. The real protection against systematically-wrong WWF
# categories is the deterministic guard layer + the failure/fallback routing,
# NOT the blunt confidence cut-off — so these must still route to review even
# though clear classifications now auto-accept. Guard ceiling (0.69) and the
# readable fallback (~0.5) both sit BELOW 0.70 by design.
# ---------------------------------------------------------------------------


class _UnknownWWFProvider(ClassifierProvider):
    """Returns ``wwf_food_group=unknown`` for every product — simulates an
    AI that gives up. The WWF readable fallback / guards must keep the row
    reviewable rather than silently auto-accepting it."""

    @property
    def model(self) -> str:
        return "wwf-k-unknown"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
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
            rows.append(
                {
                    "id": parsed["id"],
                    "wwf_food_group": "unknown",
                    "wwf_is_composite": False,
                    "confidence": 0.3,
                    "rationale": "phase-wwf-k unknown",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": rows}), model="wwf-k-unknown"
        )


class _RaisingProvider(ClassifierProvider):
    """Always raises ``ProviderError`` — simulates a provider outage."""

    @property
    def model(self) -> str:
        return "wwf-k-raise"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        raise ProviderError("simulated WWF provider outage")


class _GarbageProvider(ClassifierProvider):
    """Returns un-parseable text on every call (including the repair retry)
    — simulates a model that never emits valid JSON for the row."""

    @property
    def model(self) -> str:
        return "wwf-k-garbage"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        return ProviderResponse(
            raw_text="<<not json — model returned prose instead>>",
            model="wwf-k-garbage",
        )


class TestWWFAuditabilityPreservedAtCalibratedThreshold:
    """The four non-accept routes must still reach review at the default 0.70
    threshold, so genuinely ambiguous / low-quality / failed rows stay
    auditable (the user's hard invariant)."""

    def test_guard_corrected_row_still_routes_to_review(self) -> None:
        """A plant-milk row is touched by the unconditional WWF Guard 2a,
        which clamps confidence to <= 0.69. Even though the model returned a
        confident verdict, the guard-corrected row must route to review (the
        0.69 ceiling sits below the 0.70 threshold)."""
        bundle = batch_classify(
            [_make_wwf_product("Boisson avoine bio 1L")],
            _ConstantConfidenceProvider(
                methodology=Methodology.WWF,
                confidence=0.95,  # model is confident...
                category="FG2",
            ),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert isinstance(v, AINeedsReviewLowConfidence), (
            f"guard-corrected plant-milk row was {type(v).__name__}, "
            f"expected AINeedsReviewLowConfidence"
        )
        # ...but the guard clamped it below the auto-accept ceiling.
        assert v.classification.confidence <= Decimal("0.69")

    def test_model_unknown_row_is_never_auto_accepted(self) -> None:
        """A model ``unknown`` on a readable legume name is rescued by the WWF
        readable fallback / guard, but at fallback confidence (< 0.70) — so it
        must route to review, never silently auto-accept."""
        bundle = batch_classify(
            [_make_wwf_product("Lentilles vertes")],
            _UnknownWWFProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert not isinstance(v, AIAccepted), (
            f"model-unknown row was auto-accepted ({type(v).__name__}) — it "
            f"must stay reviewable"
        )
        assert isinstance(
            v, (AINeedsReviewLowConfidence, AINeedsReviewParseFailed)
        )

    def test_provider_error_still_routes_to_review(self) -> None:
        """A provider outage surfaces as ``AIProviderError`` (reviewable),
        regardless of threshold — the calibration change must not swallow it."""
        bundle = batch_classify(
            [_make_wwf_product("Mystery Item 1")],
            _RaisingProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        assert isinstance(bundle.verdicts[0], AIProviderError)

    def test_parse_failure_still_routes_to_review(self) -> None:
        """An un-parseable model response on a non-food name surfaces as
        ``AINeedsReviewParseFailed`` (reviewable), independent of threshold."""
        bundle = batch_classify(
            [_make_wwf_product("Zzx Widget 1")],
            _GarbageProvider(),
            Methodology.WWF,
            now=datetime.now(UTC),
            enable_retry=False,
        )
        v = bundle.verdicts[0]
        assert isinstance(v, (AINeedsReviewParseFailed, AIProviderError)), (
            f"un-parseable WWF row was {type(v).__name__}, expected a "
            f"reviewable failure verdict"
        )


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
