from __future__ import annotations

from altera_api.ingestion.column_filter import (
    detect_forbidden_columns,
    filter_commercial_columns,
)


def test_drops_exact_match_columns() -> None:
    row = {
        "product_name": "Lentil Soup",
        "revenue": "12345",
        "margin": "0.4",
        "weight_per_item_kg": "0.4",
    }
    kept, dropped = filter_commercial_columns(row)
    assert kept == {"product_name": "Lentil Soup", "weight_per_item_kg": "0.4"}
    assert dropped == ("margin", "revenue")


def test_drops_prefixed_columns() -> None:
    row = {
        "product_name": "x",
        "promotion_id": "abc",
        "promotion_discount_pct": "10",
        "confidential_strategy_note": "secret",
        "internal_score": "5",
        "store_region": "north",
        "store_floor_id": "42",
        "supplier_id": "S-1",
        "supplier_terms_text": "...",
    }
    kept, dropped = filter_commercial_columns(row)
    assert kept == {"product_name": "x"}
    assert "confidential_strategy_note" in dropped
    assert "promotion_id" in dropped
    assert "store_region" in dropped
    assert "supplier_id" in dropped
    assert dropped == tuple(sorted(dropped))


def test_keeps_items_purchased_and_items_sold() -> None:
    # These are physical methodology quantities (not commercial) — must be kept.
    row = {"items_purchased": "1000", "items_sold": "950"}
    kept, dropped = filter_commercial_columns(row)
    assert kept == row
    assert dropped == ()


def test_detect_forbidden_columns_returns_sorted_unique() -> None:
    headers = ["product_name", "revenue", "margin", "revenue", "confidential_x"]
    assert detect_forbidden_columns(headers) == ("confidential_x", "margin", "revenue", "revenue")
