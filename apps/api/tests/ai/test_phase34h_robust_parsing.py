"""Phase 34H — Tolerant batched-classifier parsing + repair retry.

Production failure mode this phase fixes:

    14 réponse(s) IA non analysables (JSON invalide / id manquant)
    Sample: parse_failed: JSON decode failed: Expecting ',' delimiter

OpenAI now responds (Phase 34F/G) but the model occasionally:
- wraps JSON in ``` ```json ``` ``` markdown fences;
- prefixes the answer with prose like "Voici les résultats:";
- returns a bare ``[{...}]`` array instead of ``{"results": [...]}``;
- emits French labels like "Végétal — cœur" instead of internal enums;
- skips an ``id`` on one row.

After Phase 34H any of these still produces successful classifications
for the rest of the batch, with a single repair retry as a safety net.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from altera_api.ai import openai_provider as op
from altera_api.ai.batch_classifier import (
    _normalize_pt_category,
    batch_classify,
    extract_json_object,
)
from altera_api.ai.batch_prompt import (
    build_batch_classifier_prompt,
    build_repair_batch_classifier_prompt,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
)
from altera_api.ai.openai_provider import OpenAIProvider
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ClassifierProvider, ProviderResponse
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct, PTProductFields


def _make_product(name: str) -> NormalizedProduct:
    return NormalizedProduct(
        id=uuid4(),
        organisation_id=uuid4(),
        project_id=uuid4(),
        upload_id=uuid4(),
        row_number=1,
        external_product_id=f"ext-{name[:10]}",
        product_name=name,
        weight_per_item_kg=__import__("decimal").Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=__import__("decimal").Decimal("1")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 1. extract_json_object — recovery scenarios
# ---------------------------------------------------------------------------


_PT_ROW = '{"id":"X","pt_group":"plant_based_core","confidence":0.95,"rationale":"ok"}'


class TestExtractJsonObject:
    def test_strict_envelope_passes_through(self) -> None:
        raw = f'{{"results":[{_PT_ROW}]}}'
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_markdown_json_fence_is_stripped(self) -> None:
        raw = f"```json\n{{\"results\":[{_PT_ROW}]}}\n```"
        out = extract_json_object(raw)
        assert out["results"][0]["pt_group"] == "plant_based_core"

    def test_plain_backtick_fence_is_stripped(self) -> None:
        raw = f"```\n{{\"results\":[{_PT_ROW}]}}\n```"
        out = extract_json_object(raw)
        assert out["results"][0]["confidence"] == 0.95

    def test_leading_prose_is_tolerated(self) -> None:
        raw = (
            "Sure! Here are the classifications:\n"
            f'{{"results":[{_PT_ROW}]}}\n'
            "Let me know if you need anything else."
        )
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_array_only_response_is_wrapped(self) -> None:
        raw = f"[{_PT_ROW}]"
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_alternative_envelope_key_products_is_lifted(self) -> None:
        raw = f'{{"products":[{_PT_ROW}]}}'
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_single_row_no_envelope_is_wrapped(self) -> None:
        raw = _PT_ROW
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_bom_and_zero_width_chars_are_stripped(self) -> None:
        raw = "﻿​‍" + f'{{"results":[{_PT_ROW}]}}' + "‌"
        out = extract_json_object(raw)
        assert out["results"][0]["id"] == "X"

    def test_empty_response_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("")

    def test_no_json_at_all_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("Hello, I cannot do that.")

    def test_invalid_json_in_envelope_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("{this is not, valid json")


# ---------------------------------------------------------------------------
# 2. Category normalisation
# ---------------------------------------------------------------------------


class TestNormalizePtCategory:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("plant_based_core", "plant_based_core"),
            ("PLANT_BASED_CORE", "plant_based_core"),
            ("Plant Based Core", "plant_based_core"),
            ("Végétal — cœur", "plant_based_core"),
            ("Vegetal coeur", "plant_based_core"),
            ("plant", "plant_based_core"),
            ("Végétal — hors cœur", "plant_based_non_core"),
            ("plant_non_core", "plant_based_non_core"),
            ("composite", "composite_products"),
            ("Composite", "composite_products"),
            ("animal", "animal_core"),
            ("Animal — cœur", "animal_core"),
            ("Hors périmètre", "out_of_scope"),
            ("inconnu", "unknown"),
        ],
    )
    def test_known_labels_map_correctly(self, raw: str, expected: str) -> None:
        assert _normalize_pt_category(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["made_up_category", "", "   ", "Catégorie X42"],
    )
    def test_unknown_or_empty_returns_none(self, raw: str) -> None:
        assert _normalize_pt_category(raw) is None


# ---------------------------------------------------------------------------
# 3. End-to-end batch_classify with the new tolerant path
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedProvider(ClassifierProvider):
    """Yields a pre-scripted sequence of raw responses, one per
    batch_classify call. Used to assert that the orchestrator runs the
    repair retry exactly once when needed."""

    responses: list[str]
    model_name: str = "fake-scripted-batch-v1"
    _idx: int = field(default=0, init=False)
    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: Any) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: Any) -> ProviderResponse:
        self.calls.append(prompt)
        idx = min(self._idx, len(self.responses) - 1)
        self._idx += 1
        return ProviderResponse(
            raw_text=self.responses[idx], model=self.model_name
        )


class TestBatchClassifyTolerance:
    def test_markdown_wrapped_response_succeeds_without_repair(self) -> None:
        products = [_make_product("Pommes Golden")]
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(products[0].id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.96,
                        "rationale": "fruit",
                    }
                ]
            }
        )
        provider = _ScriptedProvider(responses=[f"```json\n{body}\n```"])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        # No repair retry needed.
        assert len(provider.calls) == 1

    def test_array_only_response_succeeds_without_repair(self) -> None:
        products = [_make_product("Tofu Nature")]
        body = json.dumps(
            [
                {
                    "id": str(products[0].id),
                    "pt_group": "plant_based_core",
                    "confidence": 0.97,
                    "rationale": "tofu",
                }
            ]
        )
        provider = _ScriptedProvider(responses=[body])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        assert len(provider.calls) == 1

    def test_french_label_normalises(self) -> None:
        products = [_make_product("Yaourt Nature")]
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(products[0].id),
                        "pt_group": "Animal — cœur",  # French label
                        "confidence": 0.95,
                        "rationale": "dairy",
                    }
                ]
            }
        )
        provider = _ScriptedProvider(responses=[body])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        v = bundle.verdicts[0]
        assert isinstance(v, AIAccepted)
        assert v.classification.pt_group.value == "animal_core"

    def test_missing_id_fails_only_that_row(self) -> None:
        p1 = _make_product("Pommes")
        p2 = _make_product("Saumon")
        # Response includes p1 but NOT p2.
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(p1.id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.96,
                        "rationale": "ok",
                    }
                ]
            }
        )
        # The orchestrator will trigger a repair retry because p2 has
        # no match. The scripted provider returns the same body for the
        # repair call — so p1 still succeeds, p2 still parse-fails.
        provider = _ScriptedProvider(responses=[body, body])
        bundle = batch_classify(
            [p1, p2],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        # Phase 36K2 — "Saumon" is now caught by the last-chance
        # readable fallback (animal-simple anchor) and routed to
        # AINeedsReviewLowConfidence instead of parse-failed.
        assert isinstance(
            bundle.verdicts[1],
            (AINeedsReviewParseFailed, AINeedsReviewLowConfidence),
        )

    def test_invalid_category_fails_only_that_row(self) -> None:
        p1 = _make_product("X")
        p2 = _make_product("Y")
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(p1.id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.96,
                        "rationale": "ok",
                    },
                    {
                        "id": str(p2.id),
                        "pt_group": "made_up_category",  # invalid
                        "confidence": 0.95,
                        "rationale": "huh",
                    },
                ]
            }
        )
        provider = _ScriptedProvider(responses=[body])
        bundle = batch_classify(
            [p1, p2],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        assert isinstance(bundle.verdicts[1], AINeedsReviewParseFailed)
        assert bundle.unsupported_category_failures == 1

    def test_malformed_first_response_triggers_repair_retry(self) -> None:
        products = [_make_product("Pommes")]
        bad = "I can not help with that."  # unparseable
        good = json.dumps(
            {
                "results": [
                    {
                        "id": str(products[0].id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.96,
                        "rationale": "ok",
                    }
                ]
            }
        )
        provider = _ScriptedProvider(responses=[bad, good])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        # Exactly two HTTP calls: original + repair.
        assert len(provider.calls) == 2
        # The repair prompt names "JSON" and the schema explicitly.
        repair_prompt = provider.calls[1]
        assert "JSON" in repair_prompt.system_message
        assert "results" in repair_prompt.system_message

    def test_repair_also_fails_marks_whole_batch_parse_failed(self) -> None:
        products = [_make_product("A"), _make_product("B")]
        provider = _ScriptedProvider(responses=["junk1", "still junk"])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert all(
            isinstance(v, AINeedsReviewParseFailed) for v in bundle.verdicts
        )
        assert bundle.parse_failures == 2
        # Diagnostic sample includes both attempts.
        joined = " ".join(bundle.sample_errors)
        assert "parse_failed" in joined
        assert "repair_failed" in joined

    def test_partial_batch_classification_after_repair(self) -> None:
        # 3 products: first response is junk, repair returns 2 valid
        # rows and skips the third. The third must be marked
        # parse_failed; the first two must be classified normally.
        p1, p2, p3 = (
            _make_product("Pommes"),
            _make_product("Tofu"),
            _make_product("Yaourt"),
        )
        repair_body = json.dumps(
            {
                "results": [
                    {
                        "id": str(p1.id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.97,
                        "rationale": "fruit",
                    },
                    {
                        "id": str(p2.id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.96,
                        "rationale": "tofu",
                    },
                ]
            }
        )
        provider = _ScriptedProvider(responses=["junk", repair_body])
        bundle = batch_classify(
            [p1, p2, p3],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        assert isinstance(bundle.verdicts[0], AIAccepted)
        assert isinstance(bundle.verdicts[1], AIAccepted)
        # Phase 36K2 — "Yaourt" matches the readable-fallback
        # animal_simple anchor, so the legacy parse-failed path is
        # superseded by a low-confidence verdict with animal_core.
        assert isinstance(
            bundle.verdicts[2],
            (AINeedsReviewParseFailed, AINeedsReviewLowConfidence),
        )


# ---------------------------------------------------------------------------
# 4. Prompt content — "JSON" word is explicit, no commercial fields
# ---------------------------------------------------------------------------


class TestPromptContent:
    def test_main_prompt_includes_word_json(self) -> None:
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Foo"))],
            Methodology.PROTEIN_TRACKER,
        )
        # The main prompt has both system + user message; "JSON" must
        # appear at least once across the two so json_object mode kicks in.
        combined = prompt.system_message + "\n" + prompt.user_message
        assert "JSON" in combined

    def test_repair_prompt_includes_word_json_and_schema(self) -> None:
        prompt = build_repair_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Foo"))],
            Methodology.PROTEIN_TRACKER,
            bad_response="oops",
        )
        assert "JSON" in prompt.system_message
        assert "results" in prompt.system_message
        # The previous bad response is included so the model can self-correct.
        assert "oops" in prompt.user_message

    def test_no_commercial_fields_leak_into_batched_user_message(self) -> None:
        # Re-assert the privacy contract holds even with the new
        # extract/repair plumbing. assert_payload_allowed runs in the
        # builder, so a forbidden field would raise before this point.
        forbidden = [
            "items_purchased",
            "items_sold",
            "weight_per_item_kg",
            "weight_per_item_g",
            "protein_pct",
            "revenue",
            "margin",
            "cost_price",
            "supplier_terms",
        ]
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Tofu", brand="Bio"))],
            Methodology.PROTEIN_TRACKER,
        )
        for f in forbidden:
            assert f not in prompt.user_message
            assert f not in prompt.system_message


# ---------------------------------------------------------------------------
# 5. OpenAIProvider — json_schema → json_object fallback
# ---------------------------------------------------------------------------


@dataclass
class _RecordingCompletions:
    """Records each create() call and emits a scripted response. Can be
    configured to reject json_schema response_format on the first call.
    """

    reject_json_schema: bool = False
    response_text: str = '{"results":[]}'
    model_echo: str = "gpt-test"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        rf = kwargs.get("response_format", {})
        if self.reject_json_schema and rf.get("type") == "json_schema":
            raise Exception(
                "Error code: 400 - "
                "Invalid value: 'json_schema'. Supported values are: 'json_object'."
            )
        choice = type(
            "C", (), {"message": type("M", (), {"content": self.response_text})()}
        )()
        return type("R", (), {"choices": [choice]})()


@dataclass
class _FakeChat:
    completions: _RecordingCompletions


@dataclass
class _FakeClient:
    chat: _FakeChat


@dataclass
class _FakeOpenAIModule:
    client: _FakeClient

    def OpenAI(self, *, api_key: str) -> _FakeClient:  # noqa: N802
        assert api_key
        return self.client


@contextmanager
def _patch_openai(client: _FakeClient) -> Iterator[None]:
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


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    op._TOKEN_PARAM_CACHE.clear()
    op._RESPONSE_FORMAT_CACHE.clear()
    yield
    op._TOKEN_PARAM_CACHE.clear()
    op._RESPONSE_FORMAT_CACHE.clear()


class TestOpenAIProviderResponseFormat:
    def test_batch_classify_uses_json_schema_when_supported(self) -> None:
        client = _FakeClient(chat=_FakeChat(completions=_RecordingCompletions()))
        provider = OpenAIProvider(api_key="sk-test", model="gpt-modern")
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Pommes"))],
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai(client):
            provider.batch_classify(prompt)
        assert len(client.chat.completions.calls) == 1
        rf = client.chat.completions.calls[0]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"].startswith("altera_pt_batch")

    def test_batch_classify_falls_back_to_json_object_on_400(self) -> None:
        client = _FakeClient(
            chat=_FakeChat(completions=_RecordingCompletions(reject_json_schema=True))
        )
        provider = OpenAIProvider(api_key="sk-test", model="gpt-legacy")
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Saumon"))],
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai(client):
            provider.batch_classify(prompt)
        calls = client.chat.completions.calls
        assert len(calls) == 2
        assert calls[0]["response_format"]["type"] == "json_schema"
        assert calls[1]["response_format"]["type"] == "json_object"
        # Cache remembers json_object for this model.
        assert op._RESPONSE_FORMAT_CACHE.get("gpt-legacy") == "json_object"

    def test_cache_avoids_second_json_schema_attempt(self) -> None:
        # First call: rejects json_schema, falls back, caches.
        client = _FakeClient(
            chat=_FakeChat(completions=_RecordingCompletions(reject_json_schema=True))
        )
        provider = OpenAIProvider(api_key="sk-test", model="gpt-legacy")
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Tofu"))],
            Methodology.PROTEIN_TRACKER,
        )
        with _patch_openai(client):
            provider.batch_classify(prompt)
            provider.batch_classify(prompt)
        # First call: 2 attempts (json_schema fails, json_object wins).
        # Second call: 1 attempt (cache used).
        assert len(client.chat.completions.calls) == 3
        assert client.chat.completions.calls[2]["response_format"]["type"] == (
            "json_object"
        )
