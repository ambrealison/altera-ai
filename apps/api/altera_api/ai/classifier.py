"""High-level classifier orchestrator.

Inputs: a :class:`NormalizedProduct` (from Phase 5) and a provider.
Outputs: a verdict — one of accepted / below-threshold / parse-failed /
provider-error.

Retry policy mirrors docs/classification/ai-classifier.md and
docs/classification/json-validation.md:

* Parse failure (JSON or schema) → retry exactly once with the same
  prompt.
* Second parse failure → ``AINeedsReviewParseFailed``.
* Provider errors (network / 5xx) are *not* retried here; the
  orchestrator surfaces them as ``AIProviderError``. The concrete
  provider may apply its own short backoff before bubbling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TypeAlias
from uuid import UUID

from altera_api.ai.prompt_builder import (
    CLASSIFIER_PROMPT_VERSION,
    build_classifier_prompt,
)
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ClassifierProvider, ProviderError
from altera_api.ai.result_schema import (
    PTClassifierResult,
    ResultParseError,
    WWFClassifierResult,
    parse_classifier_response,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import ProteinTrackerProductClassification
from altera_api.domain.wwf import WWFProductClassification

#: Default project-level confidence threshold (see docs/classification/ai-classifier.md).
DEFAULT_CONFIDENCE_THRESHOLD: Decimal = Decimal("0.8")


# ---------------------------------------------------------------------------
# Verdict types
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AIAccepted:
    """AI-sourced classification at or above the confidence threshold."""

    classification: ProteinTrackerProductClassification | WWFProductClassification
    raw_text: str
    parse_failures: int = 0


@dataclass(frozen=True)
class AINeedsReviewLowConfidence:
    """Valid classification but below the project threshold.

    The classification still exists and is recorded; the product is
    additionally enqueued for review with reason ``low_confidence``.
    """

    classification: ProteinTrackerProductClassification | WWFProductClassification
    raw_text: str
    threshold: Decimal


@dataclass(frozen=True)
class AINeedsReviewParseFailed:
    """Two parse failures in a row — route to manual review."""

    product_id: UUID
    methodology: Methodology
    first_error: str
    second_error: str


@dataclass(frozen=True)
class AIProviderError:
    """Provider-level error (network, 5xx). Caller's job to retry or alert."""

    product_id: UUID
    methodology: Methodology
    message: str


AIVerdict: TypeAlias = (
    AIAccepted
    | AINeedsReviewLowConfidence
    | AINeedsReviewParseFailed
    | AIProviderError
)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def _classify(
    product: NormalizedProduct,
    methodology: Methodology,
    provider: ClassifierProvider,
    *,
    now: datetime,
    threshold: Decimal,
    prompt_version: str,
) -> AIVerdict:
    prompt_input = ClassifierPromptInput.from_product(product)
    prompt = build_classifier_prompt(
        prompt_input, methodology, prompt_version=prompt_version
    )

    first_error: str | None = None
    for attempt in (1, 2):
        try:
            response = provider.classify(prompt)
        except ProviderError as exc:
            return AIProviderError(
                product_id=product.id,
                methodology=methodology,
                message=str(exc),
            )

        try:
            result = parse_classifier_response(response.raw_text, methodology)
        except ResultParseError as exc:
            if attempt == 1:
                first_error = str(exc)
                continue
            return AINeedsReviewParseFailed(
                product_id=product.id,
                methodology=methodology,
                first_error=first_error or "",
                second_error=str(exc),
            )

        classification = result.to_classification(
            product_id=product.id,
            ai_prompt_version=prompt.prompt_version,
            ai_model=response.model,
            now=now,
        )
        if classification.confidence < threshold:
            return AINeedsReviewLowConfidence(
                classification=classification,
                raw_text=response.raw_text,
                threshold=threshold,
            )
        return AIAccepted(
            classification=classification,
            raw_text=response.raw_text,
            parse_failures=attempt - 1,
        )

    # Unreachable — the loop returns or continues.
    raise AssertionError("classifier orchestrator fell through")


def classify_pt(
    product: NormalizedProduct,
    provider: ClassifierProvider,
    *,
    now: datetime,
    threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
) -> AIVerdict:
    """Classify one product under PT. See module docstring for verdict types."""
    return _classify(
        product,
        Methodology.PROTEIN_TRACKER,
        provider,
        now=now,
        threshold=threshold,
        prompt_version=prompt_version,
    )


def classify_wwf(
    product: NormalizedProduct,
    provider: ClassifierProvider,
    *,
    now: datetime,
    threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    prompt_version: str = CLASSIFIER_PROMPT_VERSION,
) -> AIVerdict:
    """Classify one product under WWF. See module docstring for verdict types."""
    return _classify(
        product,
        Methodology.WWF,
        provider,
        now=now,
        threshold=threshold,
        prompt_version=prompt_version,
    )


# Silence "unused" lints for the result types re-exported through __init__.
_ = (PTClassifierResult, WWFClassifierResult)
