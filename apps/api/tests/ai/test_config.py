"""Tests for AI provider configuration factory."""

from __future__ import annotations

import pytest

from altera_api.ai.config import _MockProvider, get_ai_provider
from altera_api.ai.openai_provider import OpenAIProvider
from altera_api.ai.provider import ClassifierProvider


class TestGetAiProvider:
    def test_returns_none_when_disabled_flag_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "false")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert get_ai_provider() is None

    def test_returns_none_when_provider_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "disabled")
        assert get_ai_provider() is None

    def test_returns_none_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALTERA_AI_CLASSIFIER_ENABLED", raising=False)
        monkeypatch.delenv("ALTERA_AI_PROVIDER", raising=False)
        assert get_ai_provider() is None

    def test_returns_openai_provider_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
        monkeypatch.setenv("ALTERA_OPENAI_MODEL", "gpt-4o")
        provider = get_ai_provider()
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o"

    def test_raises_when_openai_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            get_ai_provider()

    def test_returns_mock_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "mock")
        provider = get_ai_provider()
        assert isinstance(provider, _MockProvider)

    def test_raises_on_unknown_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "anthropic")
        with pytest.raises(ValueError, match="Unknown ALTERA_AI_PROVIDER"):
            get_ai_provider()

    def test_mock_provider_satisfies_protocol(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALTERA_AI_CLASSIFIER_ENABLED", "true")
        monkeypatch.setenv("ALTERA_AI_PROVIDER", "mock")
        provider = get_ai_provider()
        assert isinstance(provider, ClassifierProvider)
