"""OpenAI concrete provider.

Lazy-imports ``openai`` so it is not a hard dependency at import time —
installations without the package can still load the rest of the module.

Privacy contract: ``assert_payload_allowed`` is called before the HTTP
request so no forbidden field can leave the process.

Phase 34F adds ``batch_classify`` — one HTTP call for N products with
JSON mode forced, which is what gets us >95% coverage on ordinary
French retailer product names.

Phase 34G — token-parameter compatibility shim. Newer OpenAI models
(o1, o3, the 2024-08-06+ gpt-4o family on some endpoints) reject
``max_tokens`` and require ``max_completion_tokens``; older models
accept either. We send ``max_completion_tokens`` by default and fall
back to ``max_tokens`` only when the server rejects the newer name —
that way the provider works against the full model matrix without
extra configuration.
"""

from __future__ import annotations

import json
from typing import Any

from altera_api.ai.batch_prompt import BatchClassifierPrompt
from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderError, ProviderResponse

_DEFAULT_MODEL = "gpt-4o-mini"

# Cache the working token-parameter name per process. The first
# successful call sets this so subsequent calls do not retry the
# unsupported form. Reset is per-process: a Render redeploy or a model
# config change re-runs the detection on the first call.
_TOKEN_PARAM_CACHE: dict[str, str] = {}


def _unsupported_max_tokens(exc: BaseException) -> bool:
    """Detect OpenAI's "(max_tokens|max_completion_tokens) is not supported" 400.

    The SDK raises ``openai.BadRequestError`` with a body whose
    ``error.code`` is ``unsupported_parameter`` and ``error.message``
    names whichever parameter the model rejected. We catch BOTH
    directions:

    * Production case — modern model rejects ``max_tokens`` and asks
      for ``max_completion_tokens``.
    * Inverse case — older model rejects ``max_completion_tokens`` (this
      can happen when ``_TOKEN_PARAM_CACHE`` is wrong for the model,
      e.g. after a deploy that downgrades the configured model).

    Stringifying the exception always includes the upstream message,
    so checking the lowered text is robust across SDK versions
    (the structured-body layout varies between 1.x and 2.x).
    """
    msg = str(exc).lower()
    if "unsupported" not in msg:
        return False
    # Note: "max_tokens" is NOT a substring of "max_completion_tokens"
    # (the "_completion_" infix separates them), so we have to check
    # both names explicitly. This avoids returning True for unrelated
    # 400s that happen to mention "max_completion_tokens" somewhere
    # else in the message.
    return "max_tokens" in msg or "max_completion_tokens" in msg


def _create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_output_tokens: int,
    response_format: dict[str, str],
    temperature: float = 0,
) -> Any:
    """Call ``client.chat.completions.create`` with token-param fallback.

    Tries ``max_completion_tokens`` first (the newer name). If the
    server replies with the specific "max_tokens is not supported"
    BadRequest, retry once with ``max_tokens``. The successful name is
    cached per-model so a 15k-row classify run only pays the detection
    cost on the first call.
    """
    cached = _TOKEN_PARAM_CACHE.get(model)
    order: list[str]
    if cached == "max_tokens":
        order = ["max_tokens", "max_completion_tokens"]
    else:
        # Prefer the modern name by default. Even when cached is
        # "max_completion_tokens" we keep the fallback in case the
        # backend hot-swaps the model behind the scenes.
        order = ["max_completion_tokens", "max_tokens"]

    last_exc: BaseException | None = None
    for attempt, name in enumerate(order):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format,
            name: max_output_tokens,
        }
        try:
            result = client.chat.completions.create(**kwargs)
            _TOKEN_PARAM_CACHE[model] = name
            return result
        except Exception as exc:  # noqa: BLE001 — OpenAI raises various types
            last_exc = exc
            # Only retry on the specific "unsupported parameter" 400.
            # Any other error (auth, rate-limit, network) is fatal.
            if attempt == 0 and _unsupported_max_tokens(exc):
                continue
            # Either it's the second attempt, or it's a different
            # error class — bubble up to the caller, who wraps it in
            # ProviderError with the OpenAI message intact.
            raise

    # Defensive — the loop always either returns or raises above.
    assert last_exc is not None
    raise last_exc


class OpenAIProvider(ClassifierProvider):
    """Sends classification prompts to OpenAI chat completions."""

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        # Privacy guard — must run before any bytes leave this process.
        assert_payload_allowed(prompt.product_card)

        messages = [
            {
                "role": "system",
                "content": f"{prompt.system_instructions}\n\n{prompt.methodology_card}",
            },
            {
                "role": "user",
                # Phase 34F — single-product path now also includes an
                # explicit instruction line in the user message; the
                # bare json.dumps that shipped in earlier phases was
                # the dominant source of parse-failure verdicts.
                "content": (
                    "Classify the following product. Return strict JSON only.\n"
                    + json.dumps(prompt.product_card, ensure_ascii=False)
                ),
            },
        ]

        try:
            import openai  # lazy import — not a hard dependency

            client = openai.OpenAI(api_key=self._api_key)
            response = _create_chat_completion(
                client,
                model=self._model,
                messages=messages,
                max_output_tokens=256,
                response_format={"type": "json_object"},
            )
            raw_text: str = response.choices[0].message.content or ""
            return ProviderResponse(raw_text=raw_text, model=self._model)
        except ImportError as exc:
            raise ProviderError(
                "openai package is not installed; "
                "add it to your dependencies or use ALTERA_AI_PROVIDER=disabled"
            ) from exc
        except Exception as exc:
            # Catch openai.APIError and any other transient errors.
            raise ProviderError(f"OpenAI provider error: {type(exc).__name__}: {exc}") from exc

    # ------------------------------------------------------------------
    # Phase 34F — batched classification path
    # ------------------------------------------------------------------
    def supports_batch(self) -> bool:
        return True

    def batch_classify(
        self, prompt: BatchClassifierPrompt
    ) -> ProviderResponse:
        # Privacy guard already ran inside ``build_batch_classifier_prompt``
        # for every per-product payload. The batched user message is a
        # concatenation of those validated payloads — no additional
        # forbidden field can appear after that point.
        messages = [
            {"role": "system", "content": prompt.system_message},
            {"role": "user", "content": prompt.user_message},
        ]

        # Output tokens scale with batch size; ~40 tokens per result row
        # is a safe upper bound (id + pt_group + confidence + short
        # rationale + JSON punctuation). Capped at 8192.
        max_output_tokens = min(8192, 256 + 40 * len(prompt.item_ids))

        try:
            import openai  # lazy import

            client = openai.OpenAI(api_key=self._api_key)
            response = _create_chat_completion(
                client,
                model=self._model,
                messages=messages,
                max_output_tokens=max_output_tokens,
                response_format={"type": "json_object"},
            )
            raw_text: str = response.choices[0].message.content or ""
            return ProviderResponse(raw_text=raw_text, model=self._model)
        except ImportError as exc:
            raise ProviderError(
                "openai package is not installed; "
                "add it to your dependencies or use ALTERA_AI_PROVIDER=disabled"
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"OpenAI provider error: {type(exc).__name__}: {exc}"
            ) from exc
