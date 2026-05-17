from __future__ import annotations

import pytest

from altera_api.ai.policy import (
    ALLOWED_PROMPT_FIELDS,
    CommercialDataBlockError,
    assert_payload_allowed,
)


class TestAllowedFields:
    def test_allow_list_matches_doc(self) -> None:
        expected = {
            "product_name",
            "retailer_category",
            "retailer_subcategory",
            "brand",
            "ingredients_text",
            "labels",
            "language",
            "country",
        }
        assert ALLOWED_PROMPT_FIELDS == expected


class TestPayloadGuard:
    def test_accepts_allowed_payload(self) -> None:
        payload = {
            "product_name": "Lentil Soup",
            "brand": "GreenLeaf",
            "labels": ["vegan"],
        }
        assert_payload_allowed(payload)  # no raise

    @pytest.mark.parametrize(
        "field",
        [
            "revenue",
            "margin",
            "sales_value",
            "cost_price",
            "contract_terms",
            "items_purchased",
            "items_sold",
            "weight_per_item_kg",
            "protein_pct",
            "plant_protein_pct",
        ],
    )
    def test_rejects_explicit_forbidden(self, field: str) -> None:
        with pytest.raises(CommercialDataBlockError) as info:
            assert_payload_allowed({"product_name": "x", field: 1})
        assert info.value.field_name == field
        # Critically, the error message must not include the value.
        assert "1" not in str(info.value) or field in str(info.value)

    @pytest.mark.parametrize(
        "field",
        [
            "promotion_id",
            "promotion_discount",
            "confidential_strategy",
            "internal_score",
            "store_id",
            "store_region",
            "supplier_id",
            "supplier_terms",
        ],
    )
    def test_rejects_forbidden_prefix(self, field: str) -> None:
        with pytest.raises(CommercialDataBlockError):
            assert_payload_allowed({"product_name": "x", field: "y"})

    def test_rejects_unknown_field(self) -> None:
        # Allow-list semantics: anything not in ALLOWED_PROMPT_FIELDS is blocked.
        with pytest.raises(CommercialDataBlockError) as info:
            assert_payload_allowed({"product_name": "x", "freeform_note": "hi"})
        assert info.value.field_name == "freeform_note"
        assert info.value.reason == "not in allow-list"

    def test_empty_payload_is_allowed(self) -> None:
        # No fields → nothing to leak.
        assert_payload_allowed({})

    def test_error_does_not_contain_value(self) -> None:
        secret_value = "STRATEGIC_TRADE_SECRET"
        with pytest.raises(CommercialDataBlockError) as info:
            assert_payload_allowed({"confidential_strategy": secret_value})
        assert secret_value not in str(info.value)
