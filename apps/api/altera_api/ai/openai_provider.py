"""OpenAI concrete provider.

Lazy-imports ``openai`` so it is not a hard dependency at import time —
installations without the package can still load the rest of the module.

Privacy contract: ``assert_payload_allowed`` is called before the HTTP
request so no forbidden field can leave the process.
"""

from __future__ import annotations

import json

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
                "content": json.dumps(prompt.product_card, ensure_ascii=False),
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
