"""Classifier provider abstraction.

A provider is anything that can take a :class:`ClassifierPrompt` and
return the raw text the model produced. Concrete OpenAI / Anthropic /
Bedrock implementations land in later phases; this module defines the
interface and the error contract.

Providers must not maintain mutable state between calls; the engine
above them treats each ``classify`` call as a pure function.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from altera_api.ai.prompt_builder import ClassifierPrompt


@dataclass(frozen=True)
class ProviderResponse:
    """What a provider returns for one classify call.

    ``raw_text`` is the *exact* body the model produced. The result
    parser is permissive about Markdown fences and surrounding
    whitespace but strict about JSON shape.
    """

    raw_text: str
    model: str


class ProviderError(RuntimeError):
    """Transient provider-level failure (network, rate limit, 5xx).

    The high-level classifier treats this as retryable — separate from
    the parse-failure retry budget. The actual backoff lives in the
    concrete provider; the orchestrator just sees a ``ProviderError``.
    """


class ClassifierProvider(ABC):
    """Stateless classifier provider."""

    @property
    @abstractmethod
    def model(self) -> str:
        """The model identifier stamped on every AI-sourced classification."""

    @abstractmethod
    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        """Send the prompt to the model and return the raw response.

        Implementations must:
        - Never log the prompt's product-card section.
        - Run the outbound payload through ``assert_payload_allowed``
          before the HTTP call.
        - Raise :class:`ProviderError` for transient network/server
          failures, not generic exceptions.
        """
