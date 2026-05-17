from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from altera_api.domain.product import ProteinSource, RetailChannel
from altera_api.ingestion.parser import parse_row


def _row(**overrides: str) -> dict[str, str]:
    base = {
        "external_product_id": "P-001",
        "product_name": "Red Lentil Soup",
        "weight_per_item_kg": "0.400",
    }
    base.update(overrides)
    return base


def test_parses_minimal_row(upload_id: UUID) -> None:
    raw, errors, warnings = parse_row(_row(), upload_id=upload_id, row_number=1)
    assert errors == ()
    assert warnings == ()
    assert raw is not None
    assert raw.external_product_id == "P-001"
    assert raw.weight_per_item_kg == Decimal("0.400")


def test_missing_external_id_emits_error(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(external_product_id=""), upload_id=upload_id, row_number=1
    )
    assert raw is None
    assert any(e.code == "missing_required" and e.field == "external_product_id" for e in errors)


def test_invalid_weight_unit_combo(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        {
            "external_product_id": "P-001",
            "product_name": "x",
            "weight_per_item_kg": "0.4",
            "weight_per_item_g": "400",
        },
        upload_id=upload_id,
        row_number=2,
    )
    assert raw is None
    assert any(e.code == "mixed_weight_units" for e in errors)


def test_protein_energy_rejected(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(protein_kj="300"), upload_id=upload_id, row_number=3
    )
    assert raw is None
    assert any(e.code == "energy_not_protein" for e in errors)


def test_truncates_non_integer_count_with_warning(upload_id: UUID) -> None:
    raw, errors, warnings = parse_row(
        _row(items_purchased="1000.7"),
        upload_id=upload_id,
        row_number=4,
    )
    assert errors == ()
    assert raw is not None
    assert raw.items_purchased == Decimal("1000")
    assert any(w.code == "non_integer_count" for w in warnings)


def test_invalid_retail_channel(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(retail_channel="online"), upload_id=upload_id, row_number=5
    )
    assert raw is None
    assert any(e.code == "invalid_enum" and e.field == "retail_channel" for e in errors)


def test_valid_retail_channel(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(retail_channel="fresh"), upload_id=upload_id, row_number=6
    )
    assert errors == ()
    assert raw is not None
    assert raw.retail_channel is RetailChannel.FRESH


def test_parses_protein_source(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(protein_source="label", protein_pct="22"),
        upload_id=upload_id,
        row_number=7,
    )
    assert errors == ()
    assert raw is not None
    assert raw.protein_source is ProteinSource.LABEL


def test_negative_items_purchased_rejected(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(items_purchased="-5"), upload_id=upload_id, row_number=8
    )
    assert raw is None
    assert any(e.code == "invalid_range" and e.field == "items_purchased" for e in errors)


def test_invalid_language_pattern_caught_by_pydantic(upload_id: UUID) -> None:
    raw, errors, _ = parse_row(
        _row(language="english"), upload_id=upload_id, row_number=9
    )
    assert raw is None
    assert errors  # exact code depends on pydantic; any error is fine here


def test_labels_pipe_separated(upload_id: UUID) -> None:
    raw, _, _ = parse_row(
        _row(labels="vegan|organic|gluten_free"), upload_id=upload_id, row_number=10
    )
    assert raw is not None
    assert raw.labels == ("vegan", "organic", "gluten_free")
