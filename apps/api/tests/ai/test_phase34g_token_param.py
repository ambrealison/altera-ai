"""Phase 34G — Token-parameter compatibility for the OpenAI provider.

Newer OpenAI models (o1, o3, parts of the 2024-08-06+ gpt-4o family)
reject ``max_tokens`` with::

    BadRequestError: Unsupported parameter: 'max_tokens' is not
    supported with this model. Use 'max_completion_tokens' instead.

The provider must:

1. Send ``max_completion_tokens`` by default.
2. On a "max_tokens is not supported" 400, retry once with the older
   ``max_tokens`` parameter so deployments still pinned to older
   models keep working.
3. Cache the working parameter name per model so a 15k-row classify
   run only pays the detection cost on the first call.
4. Preserve all other behaviour (privacy guard, JSON mode, model
   stamped on the response).

The tests below drive the provider with an injected fake client that
mimics the SDK's ``client.chat.completions.create`` surface, so we
exercise the real code path without an OpenAI key or network.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from altera_api.ai import openai_provider as op
from altera_api.ai.batch_prompt import build_batch_classifier_prompt
from altera_api.ai.openai_provider import (
    OpenAIProvider,
    _create_chat_completion,
    _unsupported_max_tokens,
)
from altera_api.ai.prompt_builder import build_classifier_prompt
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ProviderError
from altera_api.domain.common import Methodology

# ---------------------------------------------------------------------------
# Fake SDK objects: mimic the minimal surface the provider touches.
# ---------------------------------------------------------------------------


class _FakeUnsupportedParameterError(Exception):
    """Mimics ``openai.BadRequestError`` for the unsupported_parameter case.

    The provider only inspects the stringified exception, so any
    Exception subclass with the right message text is sufficient.
    """

    def __init__(self, param: str) -> None:
        super().__init__(
            f"Error code: 400 - Unsupported parameter: '{param}' is not "
            "supported with this model. Use 'max_completion_tokens' instead."
        )


@dataclass
class _FakeCompletions:
    """Records each create() call and emits a scripted response.

    ``reject_max_tokens`` controls whether the fake mimics the newer
    model's 400. Default False = legacy behaviour where both names
    work (then we don't need a retry).
    """

    reject_max_tokens: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = '{"results": []}'
    model_echo: str = "gpt-test"

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.reject_max_tokens and "max_tokens" in kwargs:
            raise _FakeUnsupportedParameterError("max_tokens")
        # Build a response object with the .choices[0].message.content
        # path the provider reads.
        choice = type(
            "C",
            (),
            {
                "message": type(
                    "M", (), {"content": self.response_text}
                )()
            },
        )()
        return type("R", (), {"choices": [choice]})()


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeClient:
    chat: _FakeChat


def _new_client(reject_max_tokens: bool = False) -> _FakeClient:
    return _FakeClient(
        chat=_FakeChat(completions=_FakeCompletions(reject_max_tokens=reject_max_tokens))
    )


@pytest.fixture(autouse=True)
def _reset_token_param_cache() -> None:
    op._TOKEN_PARAM_CACHE.clear()
    yield
    op._TOKEN_PARAM_CACHE.clear()


# ---------------------------------------------------------------------------
# _unsupported_max_tokens: detection
# ---------------------------------------------------------------------------


class TestUnsupportedMaxTokensDetection:
    def test_matches_the_real_error_message(self) -> None:
        exc = Exception(
            "Error code: 400 - {'error': {'message': \"Unsupported "
            "parameter: 'max_tokens' is not supported with this model. "
            "Use 'max_completion_tokens' instead.\", 'type': "
            "'invalid_request_error', 'param': 'max_tokens', 'code': "
            "'unsupported_parameter'}}"
        )
        assert _unsupported_max_tokens(exc) is True

    def test_does_not_match_unrelated_errors(self) -> None:
        assert _unsupported_max_tokens(Exception("rate limit")) is False
        assert _unsupported_max_tokens(Exception("auth failed")) is False
        assert _unsupported_max_tokens(Exception("network timeout")) is False


# ---------------------------------------------------------------------------
# _create_chat_completion: parameter selection + retry
# ---------------------------------------------------------------------------


class TestCreateChatCompletion:
    def test_default_call_uses_max_completion_tokens(self) -> None:
        client = _new_client(reject_max_tokens=False)
        _create_chat_completion(
            client,
            model="gpt-test",
            messages=[{"role": "user", "content": "x"}],
            max_output_tokens=256,
            response_format={"type": "json_object"},
        )
        assert len(client.chat.completions.calls) == 1
        call = client.chat.completions.calls[0]
        assert "max_completion_tokens" in call
        assert call["max_completion_tokens"] == 256
        assert "max_tokens" not in call

    def test_retries_once_with_max_tokens_on_unsupported_400(self) -> None:
        client = _new_client(reject_max_tokens=True)
        # The fake rejects max_tokens specifically; this is the inverse
        # of the production failure mode (modern models reject
        # max_tokens, legacy models accept either). Flip the rejection
        # to model the production case: reject max_completion_tokens.
        # Build a fake that rejects whichever parameter we tell it to.
        client = _FakeClient(
            chat=_FakeChat(
                completions=_RejectingCompletions(
                    reject="max_completion_tokens",
                )
            )
        )
        _create_chat_completion(
            client,
            model="gpt-modern",
            messages=[{"role": "user", "content": "x"}],
            max_output_tokens=512,
            response_format={"type": "json_object"},
        )
        calls = client.chat.completions.calls
        assert len(calls) == 2
        # First attempt tried max_completion_tokens; the server
        # rejected it; the retry used max_tokens (the legacy name).
        assert "max_completion_tokens" in calls[0]
        assert "max_tokens" in calls[1]
        assert "max_completion_tokens" not in calls[1]

    def test_retries_once_with_max_completion_tokens_on_unsupported_400(
        self,
    ) -> None:
        # Production case: modern model rejects max_tokens.
        client = _FakeClient(
            chat=_FakeChat(
                completions=_RejectingCompletions(reject="max_tokens"),
            )
        )
        # Seed the cache so the FIRST attempt is max_tokens (the legacy
        # behaviour from before this fix). The retry must escalate to
        # max_completion_tokens.
        op._TOKEN_PARAM_CACHE["gpt-modern"] = "max_tokens"
        _create_chat_completion(
            client,
            model="gpt-modern",
            messages=[{"role": "user", "content": "x"}],
            max_output_tokens=512,
            response_format={"type": "json_object"},
        )
        calls = client.chat.completions.calls
        assert len(calls) == 2
        assert "max_tokens" in calls[0]
        assert "max_completion_tokens" in calls[1]

    def test_caches_working_param_across_calls(self) -> None:
        client = _FakeClient(
            chat=_FakeChat(
                completions=_RejectingCompletions(reject="max_completion_tokens"),
            )
        )
        # First call: 2 attempts (max_completion_tokens fails, max_tokens wins).
        _create_chat_completion(
            client,
            model="gpt-legacy",
            messages=[{"role": "user", "content": "x"}],
            max_output_tokens=256,
            response_format={"type": "json_object"},
        )
        assert len(client.chat.completions.calls) == 2
        # Second call: only 1 attempt now that the cache remembers
        # max_tokens worked for "gpt-legacy".
        _create_chat_completion(
            client,
            model="gpt-legacy",
            messages=[{"role": "user", "content": "y"}],
            max_output_tokens=256,
            response_format={"type": "json_object"},
        )
        assert len(client.chat.completions.calls) == 3
        # The third call must use max_tokens directly.
        assert "max_tokens" in client.chat.completions.calls[2]
        assert "max_completion_tokens" not in client.chat.completions.calls[2]

    def test_non_unsupported_error_bubbles_immediately(self) -> None:
        # Auth errors, rate limits, networking — must NOT retry.
        @dataclass
        class _AuthFailingCompletions:
            calls: list[dict[str, Any]] = field(default_factory=list)

            def create(self, **kwargs: Any) -> Any:
                self.calls.append(kwargs)
                raise Exception("Error code: 401 - invalid API key")

        client = _FakeClient(chat=_FakeChat(completions=_AuthFailingCompletions()))
        with pytest.raises(Exception, match="401"):
            _create_chat_completion(
                client,
                model="gpt-test",
                messages=[{"role": "user", "content": "x"}],
                max_output_tokens=256,
                response_format={"type": "json_object"},
            )
        # Only one attempt — no retry on auth errors.
        assert len(client.chat.completions.calls) == 1


@dataclass
class _RejectingCompletions:
    """Fake completions that rejects one specific token-param name."""

    reject: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    response_text: str = '{"ok": true}'

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.reject in kwargs:
            raise _FakeUnsupportedParameterError(self.reject)
        choice = type(
            "C",
            (),
            {"message": type("M", (), {"content": self.response_text})()},
        )()
        return type("R", (), {"choices": [choice]})()


# ---------------------------------------------------------------------------
# OpenAIProvider end-to-end
# ---------------------------------------------------------------------------


class TestOpenAIProviderEnd2End:
    def test_classify_sends_max_completion_tokens(self) -> None:
        client = _new_client(reject_max_tokens=False)
        client.chat.completions.response_text = (
            '{"methodology": "protein_tracker", "pt_group": "plant_based_core",'
            ' "confidence": 0.98, "rationale": "obvious"}'
        )
        provider = OpenAIProvider(api_key="sk-test", model="gpt-test")
        prompt = build_classifier_prompt(
            ClassifierPromptInput(product_name="Tofu Nature Bio"),
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai_module(client):
            response = provider.classify(prompt)
        call = client.chat.completions.calls[0]
        assert "max_completion_tokens" in call
        assert "max_tokens" not in call
        assert response.model == "gpt-test"

    def test_batch_classify_sends_max_completion_tokens(self) -> None:
        client = _new_client(reject_max_tokens=False)
        client.chat.completions.response_text = '{"results": []}'
        provider = OpenAIProvider(api_key="sk-test", model="gpt-test")
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Pommes Golden"))],
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai_module(client):
            provider.batch_classify(prompt)
        call = client.chat.completions.calls[0]
        assert "max_completion_tokens" in call
        assert "max_tokens" not in call

    def test_batch_classify_retries_with_max_completion_tokens(self) -> None:
        # Reproduce the staging failure mode: server rejects max_tokens.
        # With the fix in place this never happens on the first call
        # (we send max_completion_tokens), but the retry path must
        # survive the inverse case if the cache ever points at the
        # wrong name.
        op._TOKEN_PARAM_CACHE["gpt-modern"] = "max_tokens"
        client = _FakeClient(
            chat=_FakeChat(
                completions=_RejectingCompletions(reject="max_tokens"),
            )
        )
        provider = OpenAIProvider(api_key="sk-test", model="gpt-modern")
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Saumon"))],
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai_module(client):
            response = provider.batch_classify(prompt)
        calls = client.chat.completions.calls
        assert len(calls) == 2
        assert "max_tokens" in calls[0]
        assert "max_completion_tokens" in calls[1]
        # The response still came back correctly.
        assert response.model == "gpt-modern"

    def test_provider_error_wraps_non_recoverable_failures(self) -> None:
        # If the very same error keeps firing on both attempts, the
        # provider should surface it as ProviderError (not a bare
        # exception leaking out of the openai SDK).
        @dataclass
        class _AlwaysFailing:
            calls: list[dict[str, Any]] = field(default_factory=list)

            def create(self, **kwargs: Any) -> Any:
                self.calls.append(kwargs)
                # The unsupported-parameter retry will exhaust order,
                # the second attempt also rejects — same error name on
                # both attempts (different param). Both bubble up.
                raise _FakeUnsupportedParameterError(
                    kwargs.get("max_completion_tokens")
                    and "max_completion_tokens"
                    or "max_tokens"
                )

        client = _FakeClient(chat=_FakeChat(completions=_AlwaysFailing()))
        provider = OpenAIProvider(api_key="sk-test", model="gpt-stubborn")
        prompt = build_classifier_prompt(
            ClassifierPromptInput(product_name="x"),
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai_module(client), pytest.raises(ProviderError):
            provider.classify(prompt)


@dataclass
class _FakeOpenAIModule:
    """Mimics ``import openai`` returning an OpenAI() client.

    The provider does a lazy ``import openai`` inside each method then
    calls ``openai.OpenAI(api_key=...)``. We swap the real module into
    ``sys.modules['openai']`` for the duration of the test so the lazy
    import inside the provider picks up this fake.
    """

    client: _FakeClient

    def OpenAI(self, *, api_key: str) -> _FakeClient:  # noqa: N802 — SDK-named
        assert api_key  # provider must forward the key
        return self.client


@contextmanager
def _patch_openai_module(client: _FakeClient) -> Iterator[None]:
    fake = _FakeOpenAIModule(client)
    original = sys.modules.get("openai")
    sys.modules["openai"] = fake  # type: ignore[assignment]
    try:
        yield
    finally:
        if original is None:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = original
