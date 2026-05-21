"""OpenAI concrete provider.

Lazy-imports ``openai`` so it is not a hard dependency at import time —
installations without the package can still load the rest of the module.

Privacy contract: ``assert_payload_allowed`` is called before the HTTP
request so no forbidden field can leave the process.

Phase 34F adds ``batch_classify`` — one HTTP call for N products with
JSON mode forced, which is what gets us >95% coverage on ordinary
French retailer product names.
"""

from __future__ import annotations

import json

from altera_api.ai.batch_prompt import BatchClassifierPrompt
from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderError, ProviderResponse

_DEFAULT_MODEL = "gpt-4o-mini"


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
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0,
                max_tokens=256,
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

        # max_tokens needs to scale with batch size; ~40 tokens per
        # result row is a safe upper bound (id + pt_group + confidence
        # + short rationale + JSON punctuation). Capped at 8192 so we
        # never hit gpt-4o-mini's 16k output limit accidentally.
        max_tokens = min(8192, 256 + 40 * len(prompt.item_ids))

        try:
            import openai  # lazy import

            client = openai.OpenAI(api_key=self._api_key)
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0,
                max_tokens=max_tokens,
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
