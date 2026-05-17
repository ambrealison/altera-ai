from __future__ import annotations

import pytest

from altera_api.ai.policy import (
    ALLOWED_PROMPT_FIELDS,
    CommercialDataBlockError,
)
from altera_api.ai.prompt_builder import (
    CLASSIFIER_PROMPT_VERSION,
    build_classifier_prompt,
)
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct


class TestBuildPrompt:
    def test_builds_pt_prompt(self, pt_product: NormalizedProduct) -> None:
        inp = ClassifierPromptInput.from_product(pt_product)
        prompt = build_classifier_prompt(inp, Methodology.PROTEIN_TRACKER)
        assert prompt.methodology is Methodology.PROTEIN_TRACKER
        assert prompt.prompt_version == CLASSIFIER_PROMPT_VERSION
        assert "Protein Tracker" in prompt.methodology_card
        assert "JSON only" in prompt.system_instructions

    def test_builds_wwf_prompt(self, wwf_product: NormalizedProduct) -> None:
        inp = ClassifierPromptInput.from_product(wwf_product)
        prompt = build_classifier_prompt(inp, Methodology.WWF)
        assert prompt.methodology is Methodology.WWF
        assert "WWF" in prompt.methodology_card
        assert "Protein Tracker" not in prompt.methodology_card

    def test_product_card_contains_only_allowed_fields(
        self, pt_product: NormalizedProduct
    ) -> None:
        inp = ClassifierPromptInput.from_product(pt_product)
        prompt = build_classifier_prompt(inp, Methodology.PROTEIN_TRACKER)
        assert set(prompt.product_card.keys()) <= ALLOWED_PROMPT_FIELDS

    def test_prompt_does_not_leak_pt_quantities(
        self, pt_product: NormalizedProduct
    ) -> None:
        inp = ClassifierPromptInput.from_product(pt_product)
        prompt = build_classifier_prompt(inp, Methodology.PROTEIN_TRACKER)
        # The PT product has items_purchased, protein_pct, weight_per_item_kg.
        # None of those values may appear anywhere in the prompt.
        bad_strings = [
            "items_purchased",
            "protein_pct",
            "weight_per_item_kg",
            "plant_protein_pct",
        ]
        haystack = (
            prompt.system_instructions + prompt.methodology_card + str(prompt.product_card)
        )
        for s in bad_strings:
            assert s not in haystack

    def test_outbound_guard_rejects_tampered_payload(self) -> None:
        # Direct path: even if a caller hand-builds an input dict, the
        # guard catches it. (The Pydantic input class would reject this
        # earlier; this test pins the guard's role.)
        from altera_api.ai.policy import assert_payload_allowed

        with pytest.raises(CommercialDataBlockError):
            assert_payload_allowed({"product_name": "x", "revenue": 1})

    def test_custom_prompt_version_stamped(self, pt_product: NormalizedProduct) -> None:
        inp = ClassifierPromptInput.from_product(pt_product)
        prompt = build_classifier_prompt(
            inp, Methodology.PROTEIN_TRACKER, prompt_version="classifier_v9"
        )
        assert prompt.prompt_version == "classifier_v9"
