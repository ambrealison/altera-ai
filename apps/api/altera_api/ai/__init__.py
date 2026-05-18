"""AI classifier wrapper.

The single most important rule (see
docs/classification/ai-inputs-policy.md): **commercial data is never sent
to an external LLM.** This package enforces that rule in code at four
layers:

1. ``ClassifierPromptInput`` accepts only the allow-listed fields.
2. The prompt builder accepts only ``ClassifierPromptInput``.
3. The outbound payload guard (``policy.assert_payload_allowed``) inspects
   any final dict before it leaves the process.
4. A CI test (``tests/ai/test_classifier.py``) re-asserts the contract.

This phase ships only the contract, the strict input dataclass, the
result schemas, the provider abstraction, deterministic fakes, and the
high-level orchestrator with retry. The real OpenAI provider lives in a
later phase.
"""

from __future__ import annotations

from altera_api.ai.classifier import (
    CLASSIFIER_PROMPT_VERSION,
    DEFAULT_CONFIDENCE_THRESHOLD,
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
    AIVerdict,
    classify_pt,
    classify_wwf,
)
from altera_api.ai.fakes import (
    EventuallyValidFakeProvider,
    FailingFakeProvider,
    KeywordFakeProvider,
    StaticFakeProvider,
)
from altera_api.ai.policy import (
    ALLOWED_PROMPT_FIELDS,
    CommercialDataBlockError,
    assert_payload_allowed,
)
from altera_api.ai.prompt_builder import ClassifierPrompt, build_classifier_prompt
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ClassifierProvider, ProviderError, ProviderResponse
from altera_api.ai.result_schema import (
    PTClassifierResult,
    ResultParseError,
    WWFClassifierResult,
    WWFFG2DairyClass,
    WWFFG2Kind,
    parse_classifier_response,
)

__all__ = [
    "AIAccepted",
    "AINeedsReviewLowConfidence",
    "AINeedsReviewParseFailed",
    "AIProviderError",
    "AIVerdict",
    "ALLOWED_PROMPT_FIELDS",
    "CLASSIFIER_PROMPT_VERSION",
    "ClassifierPrompt",
    "ClassifierPromptInput",
    "ClassifierProvider",
    "CommercialDataBlockError",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "EventuallyValidFakeProvider",
    "FailingFakeProvider",
    "KeywordFakeProvider",
    "PTClassifierResult",
    "ProviderError",
    "ProviderResponse",
    "ResultParseError",
    "StaticFakeProvider",
    "WWFClassifierResult",
    "WWFFG2DairyClass",
    "WWFFG2Kind",
    "assert_payload_allowed",
    "build_classifier_prompt",
    "classify_pt",
    "classify_wwf",
    "parse_classifier_response",
]
