from __future__ import annotations

import pytest

from altera_api.ingestion.headers import normalise_header, normalise_row_headers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Items Purchased", "items_purchased"),
        ("items-purchased", "items_purchased"),
        ("  items_purchased  ", "items_purchased"),
        ("ITEMS_PURCHASED", "items_purchased"),
        ("retailer-Sub Category", "retailer_sub_category"),
        ("weight_per_item_kg", "weight_per_item_kg"),
        ("multi   spaces", "multi_spaces"),
        ("trail---hyphens---", "trail_hyphens_"),
    ],
)
def test_normalise_header(raw: str, expected: str) -> None:
    assert normalise_header(raw) == expected


def test_normalise_row_headers_round_trips_values() -> None:
    row = {"Items Purchased": "1000", "Brand": "GreenLeaf"}
    assert normalise_row_headers(row) == {"items_purchased": "1000", "brand": "GreenLeaf"}
