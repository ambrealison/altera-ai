"""AI classifier configuration and provider factory.

Environment variables:

- ``ALTERA_AI_CLASSIFIER_ENABLED`` (bool, default false): master switch
  for category classification. When false, ``get_ai_provider()`` always
  returns None regardless of other settings.
- ``ALTERA_AI_PROVIDER`` (str, default "disabled"): ``openai`` | ``mock``
  | ``disabled``.
- ``OPENAI_API_KEY`` (str): required when ``ALTERA_AI_PROVIDER=openai``.
- ``ALTERA_OPENAI_MODEL`` (str, default "gpt-4o-mini"): classifier model.

Phase 33I-AI — nutrition-matching variant:

- ``AI_NUTRITION_MATCHING_ENABLED`` (bool, default false): master switch
  for AI-assisted NEVO/CIQUAL reference matching. Independent of the
  classifier flag — you can have classification AI on and nutrition AI
  off (or vice versa).
- ``OPENAI_NUTRITION_MODEL`` (str, optional): model used for nutrition
  matching. When unset, ``get_nutrition_ai_provider()`` falls back to
  ``ALTERA_OPENAI_MODEL`` so deployments that pin one model see no
  surprises.

Tests set ``ALTERA_AI_PROVIDER=mock`` to get a deterministic fake provider
without making HTTP calls. Production sets ``ALTERA_AI_PROVIDER=openai``
and supplies a real key.
"""

from __future__ import annotations

import json

from pydantic_settings import BaseSettings

from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderResponse
from altera_api.domain.common import Methodology


class AISettings(BaseSettings):
    altera_ai_classifier_enabled: bool = False
    altera_ai_provider: str = "disabled"
    openai_api_key: str | None = None
    altera_openai_model: str = "gpt-4o-mini"
    # Phase 33I-AI — nutrition-matching variant.
    ai_nutrition_matching_enabled: bool = False
    openai_nutrition_model: str | None = None


class _MockProvider(ClassifierProvider):
    """Methodology-aware deterministic mock for local dev and smoke tests."""

    @property
    def model(self) -> str:
        return "mock-provider-v1"

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        if prompt.methodology is Methodology.PROTEIN_TRACKER:
            raw = json.dumps(
                {
                    "methodology": "protein_tracker",
                    "pt_group": "plant_based_core",
                    "confidence": 0.9,
                    "rationale": "mock provider: accepted",
                }
            )
        else:
            raw = json.dumps(
                {
                    "methodology": "wwf",
                    "wwf_food_group": "FG4",
                    "wwf_is_composite": False,
                    "confidence": 0.9,
                    "rationale": "mock provider: accepted",
                }
            )
        return ProviderResponse(raw_text=raw, model=self.model)


def get_ai_provider() -> ClassifierProvider | None:
    """Return a configured provider, or None when AI is disabled.

    Called once per job execution — not a singleton, intentionally.
    """
    settings = AISettings()

    if not settings.altera_ai_classifier_enabled:
        return None

    provider_name = settings.altera_ai_provider.lower()

    if provider_name == "disabled":
        return None

    if provider_name == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when ALTERA_AI_PROVIDER=openai")
        from altera_api.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.altera_openai_model,
        )

    if provider_name == "mock":
        return _MockProvider()

    raise ValueError(
        f"Unknown ALTERA_AI_PROVIDER={settings.altera_ai_provider!r}. "
        "Valid values: openai, mock, disabled."
    )


def get_nutrition_ai_provider() -> ClassifierProvider | None:
    """Return a configured provider for AI-assisted nutrition matching.

    Gated independently from the classifier. Returns ``None`` (graceful
    fallback to deterministic-only matching) whenever any of the
    following is true:
      * ``AI_NUTRITION_MATCHING_ENABLED`` is false
      * ``ALTERA_AI_PROVIDER`` is "disabled"
      * ``ALTERA_AI_PROVIDER=openai`` but ``OPENAI_API_KEY`` is empty

    The returned provider implements the same ``ClassifierProvider``
    interface (``classify(prompt) → ProviderResponse``); the nutrition
    matcher passes a *matching* prompt rather than a classification
    prompt. The model used is ``OPENAI_NUTRITION_MODEL`` if set,
    otherwise the same ``ALTERA_OPENAI_MODEL`` the classifier uses.
    """
    settings = AISettings()
    if not settings.ai_nutrition_matching_enabled:
        return None
    provider_name = settings.altera_ai_provider.lower()
    if provider_name == "disabled":
        return None
    if provider_name == "openai":
        if not settings.openai_api_key:
            return None
        from altera_api.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_nutrition_model or settings.altera_openai_model,
        )
    if provider_name == "mock":
        return _MockProvider()
    raise ValueError(
        f"Unknown ALTERA_AI_PROVIDER={settings.altera_ai_provider!r}. "
        "Valid values: openai, mock, disabled."
    )
