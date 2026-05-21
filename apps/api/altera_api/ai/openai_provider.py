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

# Phase 34H — cache which response_format variant each model accepts.
# Values: "json_schema" (preferred — strict JSON enforced server-side)
# or "json_object" (older models / endpoints that don't support
# json_schema).
_RESPONSE_FORMAT_CACHE: dict[str, str] = {}

# Phase 34J — cache whether ``client.beta.chat.completions.parse`` with
# a Pydantic response_format works for this model. ``True`` (default
# when absent) → try the typed parse path first; ``False`` → skip it
# and use the legacy json_schema/json_object route. Populated on the
# first call that hits a response_format-rejection error.
_TYPED_PARSE_CACHE: dict[str, bool] = {}


def _use_typed_parse(model: str) -> bool:
    """Whether to attempt the OpenAI typed-parse path for ``model``.

    Defaults to True for any model we haven't seen a rejection from.
    The provider flips this to False per-model the first time the
    typed call returns an "Invalid response_format" or "json_schema
    not supported" 400.
    """
    return _TYPED_PARSE_CACHE.get(model, True)


def _typed_batch_parse(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_output_tokens: int,
) -> str:
    """Call client.beta.chat.completions.parse with our Pydantic schema
    and return a JSON string the downstream parser can consume.

    The SDK validates the model's output against the Pydantic schema
    (server-side strict-mode Structured Outputs) and parses the JSON
    into Python objects, then we re-serialise back to text so the
    batch_classifier orchestrator keeps a single uniform parse path.
    This indirection costs ~1ms but lets the rest of the code stay
    text-based, which keeps the test fakes simple.
    """
    from altera_api.ai.batch_schema import BatchClassificationResponse

    # Token-parameter compatibility: try max_completion_tokens first,
    # fall back to max_tokens on the specific 400 (mirrors Phase 34G).
    cached_tp = _TOKEN_PARAM_CACHE.get(model)
    token_order = (
        ["max_tokens", "max_completion_tokens"]
        if cached_tp == "max_tokens"
        else ["max_completion_tokens", "max_tokens"]
    )
    last_exc: BaseException | None = None
    for attempt, token_name in enumerate(token_order):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "response_format": BatchClassificationResponse,
            token_name: max_output_tokens,
        }
        try:
            response = client.beta.chat.completions.parse(**kwargs)
            _TOKEN_PARAM_CACHE[model] = token_name
            parsed = response.choices[0].message.parsed
            if parsed is None:
                # Refusal or empty completion — surface as text so the
                # tolerant parser can run its checks.
                raw = response.choices[0].message.content or ""
                if not raw:
                    raise RuntimeError("typed parse returned no content")
                return raw
            return parsed.model_dump_json()
        except Exception as exc:  # noqa: BLE001 — OpenAI raises various types
            last_exc = exc
            if attempt == 0 and _unsupported_max_tokens(exc):
                continue
            raise
    assert last_exc is not None
    raise last_exc


#: JSON Schema used when the model supports Structured Outputs. Mirrors
#: the documented batched response: {"results": [{id, pt_group,
#: confidence, rationale}, ...]}. We do not enumerate enum values in
#: the schema (OpenAI's json_schema strict mode rejects schemas with
#: long enum unions on some models); the parser normalises French
#: labels on the way back instead.
_PT_BATCH_JSON_SCHEMA: dict[str, Any] = {
    "name": "altera_pt_batch_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "pt_group": {"type": "string"},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "pt_group", "confidence", "rationale"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}

_WWF_BATCH_JSON_SCHEMA: dict[str, Any] = {
    "name": "altera_wwf_batch_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "wwf_food_group": {"type": "string"},
                        "wwf_is_composite": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "wwf_food_group",
                        "wwf_is_composite",
                        "confidence",
                        "rationale",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}


def _is_response_format_rejection(exc: BaseException) -> bool:
    """Detect a 400 indicating the model rejected the json_schema
    response_format. The exact wording varies across models, so we
    check for a small handful of keywords.

    Examples encountered:
    * "Invalid value: 'json_schema'."
    * "response_format' of type 'json_schema' is not supported with
       this model."
    * "Unsupported value: 'response_format.type'"
    """
    msg = str(exc).lower()
    if "json_schema" not in msg and "response_format" not in msg:
        return False
    return (
        "unsupported" in msg
        or "invalid value" in msg
        or "not supported" in msg
    )


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
    response_format: dict[str, Any],
    response_format_fallback: dict[str, Any] | None = None,
    temperature: float = 0,
) -> Any:
    """Call ``client.chat.completions.create`` with two compatibility
    fallbacks layered together:

    1. **Token-parameter** — prefer ``max_completion_tokens`` (the
       modern name). On a "max_tokens is not supported" 400, retry once
       with ``max_tokens``. Cached per model.
    2. **Response-format** — if ``response_format_fallback`` is set
       (typically a json_object form for models that don't support
       json_schema strict mode), and the server returns a 400 that
       rejects the primary ``response_format``, retry once with the
       fallback. Cached per model.

    The two retries do not stack on the same call: a token-parameter
    error retries with the OTHER token name (same response_format), and
    a response-format error retries with the FALLBACK response_format
    (same token name). At most two HTTP attempts per call.
    """
    # Pick the response_format up-front based on the per-model cache so
    # we don't try json_schema on a model we already know rejects it.
    cached_rf = _RESPONSE_FORMAT_CACHE.get(model)
    if (
        cached_rf == "json_object"
        and response_format_fallback is not None
        and response_format.get("type") == "json_schema"
    ):
        rf_to_use = response_format_fallback
        rf_used_is_fallback = True
    else:
        rf_to_use = response_format
        rf_used_is_fallback = False

    cached_tp = _TOKEN_PARAM_CACHE.get(model)
    token_order: list[str]
    if cached_tp == "max_tokens":
        token_order = ["max_tokens", "max_completion_tokens"]
    else:
        token_order = ["max_completion_tokens", "max_tokens"]

    last_exc: BaseException | None = None
    for token_attempt, token_name in enumerate(token_order):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": rf_to_use,
            token_name: max_output_tokens,
        }
        try:
            result = client.chat.completions.create(**kwargs)
            _TOKEN_PARAM_CACHE[model] = token_name
            _RESPONSE_FORMAT_CACHE[model] = (
                "json_object" if rf_used_is_fallback else rf_to_use.get("type", "json_object")
            )
            return result
        except Exception as exc:  # noqa: BLE001 — OpenAI raises various types
            last_exc = exc
            # Token-name retry.
            if token_attempt == 0 and _unsupported_max_tokens(exc):
                continue
            # Response-format retry — only when we have a fallback and
            # we haven't already swapped to it.
            if (
                response_format_fallback is not None
                and not rf_used_is_fallback
                and _is_response_format_rejection(exc)
            ):
                _RESPONSE_FORMAT_CACHE[model] = "json_object"
                rf_to_use = response_format_fallback
                rf_used_is_fallback = True
                # Retry from the current token-attempt with the new
                # response_format. We do NOT bump token_attempt because
                # this is an orthogonal retry; cap the loop manually.
                try:
                    kwargs["response_format"] = rf_to_use
                    result = client.chat.completions.create(**kwargs)
                    _TOKEN_PARAM_CACHE[model] = token_name
                    return result
                except Exception as exc2:  # noqa: BLE001
                    last_exc = exc2
                    # If THIS one is a token-name error, fall through
                    # to the next iteration of the outer loop.
                    if token_attempt == 0 and _unsupported_max_tokens(exc2):
                        continue
                    raise
            raise

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

            # Phase 34J — prefer client.beta.chat.completions.parse with
            # a Pydantic response_format. This routes through OpenAI's
            # Structured Outputs strict-schema path AND parses the
            # response into Python objects on the way back, eliminating
            # the free-text JSON failure modes (missing commas between
            # fields, dropped quotes) that the earlier chat.completions
            # .create path suffered from on long batches.
            #
            # Only PT batches use the typed path for now — WWF stays on
            # the legacy json_schema/json_object route because its
            # multi-subgroup schema is more permissive and the typed
            # model is correspondingly narrower.
            from altera_api.domain.common import Methodology as _M

            if prompt.methodology is _M.PROTEIN_TRACKER and _use_typed_parse(
                self._model
            ):
                try:
                    parsed_text = _typed_batch_parse(
                        client,
                        model=self._model,
                        messages=messages,
                        max_output_tokens=max_output_tokens,
                    )
                    return ProviderResponse(
                        raw_text=parsed_text, model=self._model
                    )
                except Exception as exc:  # noqa: BLE001
                    # If the .parse() route fails for any reason
                    # (model rejects schema, SDK version mismatch, etc.)
                    # remember that for this model and fall through to
                    # the legacy json_schema/json_object path. We only
                    # record the negative case so a subsequent run that
                    # configures a parse-capable model retries cleanly.
                    if _is_response_format_rejection(exc):
                        _TYPED_PARSE_CACHE[self._model] = False

            schema = _PT_BATCH_JSON_SCHEMA if prompt.methodology is _M.PROTEIN_TRACKER else _WWF_BATCH_JSON_SCHEMA
            primary_rf: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": schema,
            }
            fallback_rf: dict[str, Any] = {"type": "json_object"}
            response = _create_chat_completion(
                client,
                model=self._model,
                messages=messages,
                max_output_tokens=max_output_tokens,
                response_format=primary_rf,
                response_format_fallback=fallback_rf,
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
