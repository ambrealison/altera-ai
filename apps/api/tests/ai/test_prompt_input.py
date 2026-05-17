from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.ai.policy import ALLOWED_PROMPT_FIELDS
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.domain.product import NormalizedProduct


class TestClassifierPromptInput:
    def test_creates_with_allowed_fields(self) -> None:
        inp = ClassifierPromptInput(
            product_name="Red Lentil Soup",
            brand="GreenLeaf",
            labels=("vegan", "organic"),
            language="en",
            country="GB",
        )
        assert inp.product_name == "Red Lentil Soup"
        assert inp.labels == ("vegan", "organic")

    def test_extras_forbidden(self) -> None:
        # Any commercial field would be rejected at construction.
        with pytest.raises(PydanticValidationError):
            ClassifierPromptInput(
                product_name="x",
                revenue=12345.67,  # type: ignore[call-arg]
            )

    def test_extras_forbidden_for_physical_quantities(self) -> None:
        # Per docs: items_purchased / items_sold live in the DB but
        # NEVER reach a prompt.
        with pytest.raises(PydanticValidationError):
            ClassifierPromptInput(
                product_name="x",
                items_purchased=1000,  # type: ignore[call-arg]
            )

    def test_field_set_matches_policy_allow_list(self) -> None:
        declared = set(ClassifierPromptInput.model_fields.keys())
        assert declared == ALLOWED_PROMPT_FIELDS

    def test_from_product_drops_unrelated_fields(self, pt_product: NormalizedProduct) -> None:
        inp = ClassifierPromptInput.from_product(pt_product)
        # The product has items_purchased + protein_pct on pt_fields;
        # neither appears here.
        payload = inp.to_payload()
        assert "items_purchased" not in payload
        assert "protein_pct" not in payload
        assert "weight_per_item_kg" not in payload
        # But the allowed fields ARE there.
        assert payload["product_name"] == pt_product.product_name
        assert payload["brand"] == pt_product.brand

    def test_to_payload_preserves_labels_as_list(self) -> None:
        inp = ClassifierPromptInput(product_name="x", labels=("a", "b"))
        payload = inp.to_payload()
        assert payload["labels"] == ["a", "b"]

    def test_invalid_language_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            ClassifierPromptInput(product_name="x", language="english")  # type: ignore[arg-type]
