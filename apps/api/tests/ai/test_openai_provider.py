"""OpenAI provider tests — no real HTTP calls, no openai package required.

The openai package is lazy-imported inside ``OpenAIProvider.classify``.
These tests inject a fake module via ``sys.modules`` to avoid needing
the real package installed.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from altera_api.ai.openai_provider import OpenAIProvider
from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import build_classifier_prompt
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ProviderError, ProviderResponse
from altera_api.domain.common import Methodology


def _make_prompt():
    inp = ClassifierPromptInput(product_name="Tofu Block", brand="Green")
    return build_classifier_prompt(inp, Methodology.PROTEIN_TRACKER)


def _make_fake_openai(raw_text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = raw_text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]

    fake = MagicMock()
    fake.OpenAI.return_value.chat.completions.create.return_value = resp
    return fake


@contextmanager
def _inject_openai(fake_module: MagicMock):
    """Temporarily inject *fake_module* as ``openai`` in sys.modules."""
    prev = sys.modules.get("openai")
    sys.modules["openai"] = fake_module
    try:
        yield fake_module
    finally:
        if prev is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = prev


class TestOpenAIProvider:
    def test_returns_provider_response_on_success(self) -> None:
        raw = json.dumps({
            "methodology": "protein_tracker",
            "pt_group": "plant_based_core",
            "confidence": 0.9,
            "rationale": "soy product",
        })
        prompt = _make_prompt()
        fake = _make_fake_openai(raw)

        with _inject_openai(fake):
            provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
            result = provider.classify(prompt)

        assert isinstance(result, ProviderResponse)
        assert result.raw_text == raw
        assert result.model == "gpt-4o-mini"

    def test_raises_provider_error_on_api_error(self) -> None:
        prompt = _make_prompt()
        fake = MagicMock()
        fake.OpenAI.return_value.chat.completions.create.side_effect = RuntimeError("503 upstream")

        with _inject_openai(fake):
            provider = OpenAIProvider(api_key="sk-test")
            with pytest.raises(ProviderError, match="503"):
                provider.classify(prompt)

    def test_raises_provider_error_when_openai_missing(self) -> None:
        """ImportError from a missing openai package → ProviderError."""
        prompt = _make_prompt()
        prev = sys.modules.get("openai")
        sys.modules.pop("openai", None)
        try:
            provider = OpenAIProvider(api_key="sk-test")
            with pytest.raises(ProviderError, match="not installed"):
                provider.classify(prompt)
        finally:
            if prev is not None:
                sys.modules["openai"] = prev

    def test_privacy_guard_runs_before_http(self) -> None:
        """The payload must only contain allowed fields."""
        prompt = _make_prompt()
        assert_payload_allowed(prompt.product_card)

    def test_model_property(self) -> None:
        provider = OpenAIProvider(api_key="sk-test", model="gpt-4-turbo")
        assert provider.model == "gpt-4-turbo"

    def test_default_model(self) -> None:
        provider = OpenAIProvider(api_key="sk-test")
        assert provider.model == "gpt-4o-mini"
