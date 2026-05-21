"""Classifier provider abstraction.

A provider is anything that can take a :class:`ClassifierPrompt` and
return the raw text the model produced. Concrete OpenAI / Anthropic /
Bedrock implementations land in later phases; this module defines the
interface and the error contract.

Providers must not maintain mutable state between calls; the engine
above them treats each ``classify`` call as a pure function.

Phase 34F adds an optional ``batch_classify`` method. Providers that
implement it can classify N products in one HTTP call, which is
required to handle 10k–15k-row retailer CSVs without exhausting rate
limits or producing one-by-one parse failures.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from altera_api.ai.batch_prompt import BatchClassifierPrompt
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

    def supports_batch(self) -> bool:
        """Whether this provider can classify multiple products per call.

        Phase 34F — default ``False``. Providers that override
        :meth:`batch_classify` should also return ``True`` here so the
        orchestrator picks the batched path.
        """
        return False

    def batch_classify(
        self, prompt: BatchClassifierPrompt
    ) -> ProviderResponse:
        """Send a batched prompt and return the raw JSON response.

        Default implementation raises ``NotImplementedError`` — the
        orchestrator falls back to per-product :meth:`classify` calls
        when this is not implemented. Implementations must still run
        the privacy guard on each per-product payload BEFORE assembling
        the batched request (the :func:`build_batch_classifier_prompt`
        helper does this for them).
        """
        raise NotImplementedError(
            "this provider does not implement batch classification"
        )
