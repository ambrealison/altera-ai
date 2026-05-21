"""Phase 34J — Missing-comma repair + partial row recovery.

Production failure mode this fixes:

    33 réponse(s) IA non analysables (JSON invalide / id manquant)
    Sample raw: ``{"id":"...","pt_group":"plant_based_core""confidence":0.9,...}``

The model dropped the comma between adjacent fields. Without the
Phase 34J repair the whole 33-product batch was unrecoverable; with
the repair it parses cleanly. When the repair somehow fails (a
catastrophically malformed envelope), the orchestrator falls back to
per-row salvage so 30+/33 rows still get classified.

Areas covered:

A. ``_repair_missing_commas`` rewrites the three observed patterns.
B. ``extract_json_object`` runs the repair automatically after a
   plain ``json.loads`` failure.
C. ``extract_rows_partial`` salvages individual rows when the
   envelope is unrecoverable.
D. Orchestrator: a 33-row malformed batch yields >30 parsed verdicts
   (not 0/33 failed).
E. ``BatchClassificationResponse`` Pydantic schema constrains the
   typed-parse path (used by ``client.beta.chat.completions.parse``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from altera_api.ai.batch_classifier import (
    _repair_missing_commas,
    batch_classify,
    extract_json_object,
    extract_rows_partial,
)
from altera_api.ai.batch_prompt import (
    build_batch_classifier_prompt,
)
from altera_api.ai.batch_schema import (
    BatchClassificationResponse,
    BatchClassificationRow,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
)
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
        weight_per_item_kg=Decimal("0.5"),
        language="fr",
        country="FR",
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(items_purchased=Decimal("1")),
        wwf_fields=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# A. _repair_missing_commas
# ---------------------------------------------------------------------------


class TestRepairMissingCommas:
    def test_string_string_missing_comma(self) -> None:
        bad = '{"a":"x""b":"y"}'
        assert _repair_missing_commas(bad) == '{"a":"x","b":"y"}'

    def test_number_string_missing_comma(self) -> None:
        bad = '{"confidence":0.9"rationale":"ok"}'
        # Numeric → string-key comma insertion.
        assert _repair_missing_commas(bad) == '{"confidence":0.9,"rationale":"ok"}'

    def test_integer_string_missing_comma(self) -> None:
        bad = '{"n":3"key":"v"}'
        assert _repair_missing_commas(bad) == '{"n":3,"key":"v"}'

    def test_object_object_missing_comma(self) -> None:
        bad = '[{"id":"a"}{"id":"b"}]'
        assert _repair_missing_commas(bad) == '[{"id":"a"},{"id":"b"}]'

    def test_repair_does_not_break_already_valid_json(self) -> None:
        good = '{"results":[{"id":"x","pt_group":"plant_based_core","confidence":0.9,"rationale":"fruit"}]}'
        assert json.loads(_repair_missing_commas(good)) == json.loads(good)

    def test_repair_preserves_escaped_quotes(self) -> None:
        # An escaped quote inside a string must NOT be treated as the
        # closing quote — the regex specifically anchors on an alpha
        # key-start after the closing quote, so backslash-escaped
        # quotes that don't sit next to a key-start are untouched.
        original = '{"a":"He said \\"hi\\"","b":"ok"}'
        out = _repair_missing_commas(original)
        assert json.loads(out) == json.loads(original)


# ---------------------------------------------------------------------------
# B. extract_json_object — auto-repair pass
# ---------------------------------------------------------------------------


_PRODUCTION_BAD = (
    '{"results":[{"id":"p1","pt_group":"plant_based_core""confidence":0.9,'
    '"rationale":"fruit"},{"id":"p2","pt_group":"animal_core""confidence":0.95,'
    '"rationale":"chicken"}]}'
)


class TestExtractJsonObjectRepair:
    def test_production_pattern_recovers(self) -> None:
        out = extract_json_object(_PRODUCTION_BAD)
        assert len(out["results"]) == 2
        assert out["results"][0]["pt_group"] == "plant_based_core"
        assert out["results"][1]["pt_group"] == "animal_core"

    def test_number_string_missing_comma_recovers(self) -> None:
        raw = '{"results":[{"id":"p1","pt_group":"plant_based_core","confidence":0.95"rationale":"fruit"}]}'
        out = extract_json_object(raw)
        assert out["results"][0]["confidence"] == 0.95
        assert out["results"][0]["rationale"] == "fruit"

    def test_object_object_missing_comma_recovers(self) -> None:
        raw = (
            '{"results":[{"id":"p1","pt_group":"plant_based_core",'
            '"confidence":0.9,"rationale":"a"}{"id":"p2","pt_group":'
            '"animal_core","confidence":0.9,"rationale":"b"}]}'
        )
        out = extract_json_object(raw)
        assert len(out["results"]) == 2


# ---------------------------------------------------------------------------
# C. extract_rows_partial — per-row salvage when envelope is broken
# ---------------------------------------------------------------------------


class TestExtractRowsPartial:
    def test_recovers_individual_rows_from_broken_envelope(self) -> None:
        # The envelope is unrecoverable (no top-level `results`); each
        # row is still recognisable.
        raw = (
            'random prose here {"id":"p1","pt_group":"plant_based_core","'
            'confidence":0.95,"rationale":"fruit"} more prose '
            '{"id":"p2","pt_group":"animal_core","confidence":0.96,'
            '"rationale":"chicken"} junk at end'
        )
        rows = extract_rows_partial(raw)
        assert len(rows) == 2
        ids = sorted(r["id"] for r in rows)
        assert ids == ["p1", "p2"]

    def test_recovers_rows_with_missing_comma(self) -> None:
        raw = (
            '{"id":"p1","pt_group":"plant_based_core""confidence":0.9,'
            '"rationale":"fruit"} '
            '{"id":"p2","pt_group":"animal_core""confidence":0.95,'
            '"rationale":"chicken"}'
        )
        rows = extract_rows_partial(raw)
        assert len(rows) == 2

    def test_returns_empty_when_no_rows(self) -> None:
        assert extract_rows_partial("nothing useful here") == []
        assert extract_rows_partial("") == []


# ---------------------------------------------------------------------------
# D. Batch orchestrator: 33-product malformed batch yields >30 parsed
# ---------------------------------------------------------------------------


@dataclass
class _ScriptedProvider(ClassifierProvider):
    """Yields a pre-scripted sequence of raw responses, one per
    batch_classify call. Used to assert the repair + partial-recovery
    code paths in the orchestrator without an OpenAI key."""

    responses: list[str]
    model_name: str = "phase34j-fake"
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


class TestThirtyThreeBatchSurvives:
    def test_thirty_three_products_with_missing_commas_classify(self) -> None:
        # Build a 33-row batch and a response that reproduces the
        # production failure mode (missing comma between pt_group and
        # confidence in every row).
        products = [_make_product(f"Produit {i}") for i in range(33)]
        rows_text = ",".join(
            (
                f'{{"id":"{p.id}","pt_group":"plant_based_core"'
                f'"confidence":0.92,"rationale":"ok"}}'
            )
            for p in products
        )
        body = '{"results":[' + rows_text + "]}"
        provider = _ScriptedProvider(responses=[body])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        # >95% recovered (target was >30/33).
        accepted = sum(
            1 for v in bundle.verdicts if isinstance(v, AIAccepted)
        )
        assert accepted >= 30, f"only {accepted}/33 recovered"
        assert bundle.parse_failures <= 3

    def test_partial_envelope_breakage_still_recovers_rows(self) -> None:
        # Even when both the initial AND the repair responses cannot
        # be parsed as an envelope, the orchestrator falls back to
        # per-row salvage and recovers everything that looks row-shaped.
        products = [_make_product(f"Item {i}") for i in range(3)]
        # Garbage envelope + good rows interspersed with prose.
        rows = " junk ".join(
            f'{{"id":"{p.id}","pt_group":"plant_based_core","confidence":0.95,"rationale":"x"}}'
            for p in products
        )
        # Both attempts return the same un-enveloped soup.
        provider = _ScriptedProvider(responses=["broken " + rows, "still broken " + rows])
        bundle = batch_classify(
            products,
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        accepted = sum(
            1 for v in bundle.verdicts if isinstance(v, AIAccepted)
        )
        assert accepted == 3
        assert bundle.parse_failures == 0


# ---------------------------------------------------------------------------
# E. Summary counts: low-confidence is classified+review, NOT failed
# ---------------------------------------------------------------------------


class TestSummaryCountsNotFailed:
    def test_low_confidence_does_not_count_as_failed(self) -> None:
        # A valid response with low confidence stores the classification
        # and queues a review item. parse_failures must remain 0.
        products = [_make_product("Tofu Nature")]
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(products[0].id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.55,
                        "rationale": "ok",
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
        assert isinstance(bundle.verdicts[0], AINeedsReviewLowConfidence)
        assert bundle.parse_failures == 0
        assert bundle.unsupported_category_failures == 0

    def test_only_unrecoverable_rows_count_as_failed(self) -> None:
        # One good row + one row missing an id. Parse failure counter
        # must be 1, not 2.
        p_good = _make_product("Pommes")
        p_bad = _make_product("Mystery")
        body = json.dumps(
            {
                "results": [
                    {
                        "id": str(p_good.id),
                        "pt_group": "plant_based_core",
                        "confidence": 0.95,
                        "rationale": "fruit",
                    }
                    # p_bad intentionally absent
                ]
            }
        )
        # Provider returns the same broken response for the repair too.
        provider = _ScriptedProvider(responses=[body, body])
        bundle = batch_classify(
            [p_good, p_bad],
            provider,
            Methodology.PROTEIN_TRACKER,
            now=datetime.now(UTC),
        )
        # First verdict is accepted; second is parse_failed.
        kinds = [type(v).__name__ for v in bundle.verdicts]
        assert "AIAccepted" in kinds
        assert "AINeedsReviewParseFailed" in kinds
        # Failed counter reflects only the truly unrecoverable row.
        assert bundle.parse_failures == 1


# ---------------------------------------------------------------------------
# F. Pydantic schema constrains the typed-parse path
# ---------------------------------------------------------------------------


class TestBatchClassificationSchema:
    def test_valid_row_parses(self) -> None:
        row = BatchClassificationRow(
            id="X",
            pt_group="plant_based_core",
            confidence=0.95,
            rationale="fruit",
        )
        assert row.pt_group == "plant_based_core"

    def test_rationale_cap_64_chars(self) -> None:
        with pytest.raises(ValidationError):
            BatchClassificationRow(
                id="X",
                pt_group="plant_based_core",
                confidence=0.95,
                rationale="x" * 200,
            )

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            BatchClassificationRow.model_validate(
                {
                    "id": "X",
                    "pt_group": "plant_based_core",
                    "confidence": 0.9,
                    "rationale": "ok",
                    "extra": "field",
                }
            )

    def test_response_envelope_roundtrips(self) -> None:
        env = BatchClassificationResponse(
            results=[
                BatchClassificationRow(
                    id="A",
                    pt_group="plant_based_core",
                    confidence=0.9,
                    rationale="x",
                )
            ]
        )
        as_json = env.model_dump_json()
        assert '"results"' in as_json
        # Round-trip cleanly through json.loads + extract_json_object.
        out = extract_json_object(as_json)
        assert out["results"][0]["id"] == "A"


# ---------------------------------------------------------------------------
# G. Prompt content — comma instruction is explicit, rationale is short
# ---------------------------------------------------------------------------


class TestPromptHasCommaInstruction:
    def test_main_prompt_names_commas_and_short_rationale(self) -> None:
        prompt = build_batch_classifier_prompt(
            [("ID-1", ClassifierPromptInput(product_name="Foo"))],
            Methodology.PROTEIN_TRACKER,
        )
        # The system message must explicitly request commas as the
        # field separator and cap rationale length.
        assert "comma" in prompt.system_message.lower()
        assert "8 words" in prompt.system_message
        # Example shows valid commas at every separator.
        # ("plant_based_core","confidence" — commas, no missing-comma
        # pattern in the example.)
        assert (
            '"plant_based_core","confidence"'
            in prompt.system_message
        )
