"""Deterministic fake providers for tests and local development.

These never make HTTP calls. They are the test-side counterpart to the
real (OpenAI / Anthropic / …) providers that land in a later phase.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderError, ProviderResponse


@dataclass
class StaticFakeProvider(ClassifierProvider):
    """Always returns the same raw response.

    Useful for unit-testing the parser and the orchestrator's
    happy-path / below-threshold / parse-failure branches.
    """

    raw_text: str
    model_name: str = "fake-static-v1"

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        return ProviderResponse(raw_text=self.raw_text, model=self.model_name)


@dataclass
class KeywordFakeProvider(ClassifierProvider):
    """Picks a canned response based on a keyword in ``product_name``.

    Falls back to ``default`` if nothing matches. Matching is
    case-insensitive substring.
    """

    rules: dict[str, str]
    default: str
    model_name: str = "fake-keyword-v1"

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        name = str(prompt.product_card.get("product_name", "")).lower()
        for needle, response in self.rules.items():
            if needle.lower() in name:
                return ProviderResponse(raw_text=response, model=self.model_name)
        return ProviderResponse(raw_text=self.default, model=self.model_name)


@dataclass
class FailingFakeProvider(ClassifierProvider):
    """Always returns invalid JSON. Exercises the parse-failure path."""

    raw_text: str = "not json {{"
    model_name: str = "fake-failing-v1"

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        return ProviderResponse(raw_text=self.raw_text, model=self.model_name)


@dataclass
class EventuallyValidFakeProvider(ClassifierProvider):
    """First call returns invalid JSON; later calls return ``valid_text``.

    Exercises the retry-exactly-once path.
    """

    valid_text: str
    invalid_calls: int = 1
    invalid_text: str = "not json {{"
    model_name: str = "fake-eventually-valid-v1"
    _calls: int = field(default=0, init=False, repr=False)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        self._calls += 1
        raw = self.invalid_text if self._calls <= self.invalid_calls else self.valid_text
        return ProviderResponse(raw_text=raw, model=self.model_name)


@dataclass
class RaisingFakeProvider(ClassifierProvider):
    """Raises a :class:`ProviderError` on every call. Exercises the
    provider-error verdict path."""

    message: str = "simulated 429 rate-limit"
    model_name: str = "fake-raising-v1"
    raise_factory: Callable[[str], Exception] = ProviderError

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise self.raise_factory(self.message)


@dataclass
class ScriptedFakeProvider(ClassifierProvider):
    """Yields a pre-scripted sequence of raw responses."""

    responses: tuple[str, ...]
    model_name: str = "fake-scripted-v1"
    _iter: Iterator[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_iter", iter(self.responses))

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        assert_payload_allowed(prompt.product_card)
        try:
            raw = next(self._iter)
        except StopIteration as exc:
            raise ProviderError("scripted responses exhausted") from exc
        return ProviderResponse(raw_text=raw, model=self.model_name)
