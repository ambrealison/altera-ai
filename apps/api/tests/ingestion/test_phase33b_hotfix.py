"""Phase 33B-hotfix — regression tests for staging issues.

Covers:
- PT-only project: missing_required_wwf is empty (methodology-aware mapping)
- WWF-only project: missing_required_pt is empty
- Official PT template columns map correctly (ID, Weight gram, Sales, etc.)
- Comma decimals parse: "0,4" → 0.4, "1.234,5" → 1234.5
- oui/non/vrai/faux booleans work in _coerce_bool
- Output/diagnostic columns get auto_ignore=True from infer_mapping
- Missing protein_pct produces a ValidationWarning, not a ValidationError
- Pipeline: product is created even when protein_pct is absent
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from uuid import uuid4

import pytest

from altera_api.domain.common import Methodology
from altera_api.ingestion.mapping import infer_mapping
from altera_api.ingestion.parser import _coerce_bool, parse_row
from altera_api.ingestion.pipeline import ingest_csv_bytes
from altera_api.ingestion.units import _coerce_decimal


# ---------------------------------------------------------------------------
# Methodology-aware required-field reporting
# ---------------------------------------------------------------------------


class TestMethodologyAwareMissingFields:
    def test_pt_only_no_wwf_missing(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg", "items_purchased"],
            methodologies=["protein_tracker"],
        )
        assert result.missing_required_pt == []
        assert result.missing_required_wwf == []

    def test_pt_only_wwf_fields_absent_no_complaint(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg", "items_purchased"],
            methodologies=["protein_tracker"],
        )
        assert "items_sold" not in result.missing_required_wwf
        assert "retail_channel" not in result.missing_required_wwf

    def test_wwf_only_no_pt_missing(self) -> None:
        result = infer_mapping(
            [
                "external_product_id",
                "product_name",
                "weight_per_item_kg",
                "items_sold",
                "is_own_brand",
                "retail_channel",
            ],
            methodologies=["wwf"],
        )
        assert result.missing_required_pt == []
        assert result.missing_required_wwf == []

    def test_wwf_only_items_purchased_not_required(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg"],
            methodologies=["wwf"],
        )
        assert "items_purchased" not in result.missing_required_pt

    def test_both_methodologies_reports_both(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg"],
            methodologies=["protein_tracker", "wwf"],
        )
        assert "items_purchased" in result.missing_required_pt
        assert "items_sold" in result.missing_required_wwf

    def test_no_methodologies_reports_both(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg"],
        )
        assert "items_purchased" in result.missing_required_pt
        assert "items_sold" in result.missing_required_wwf


# ---------------------------------------------------------------------------
# Official PT template column synonyms
# ---------------------------------------------------------------------------


class TestOfficialPTTemplateSynonyms:
    def test_id_maps_to_external_product_id(self) -> None:
        result = infer_mapping(["ID"])
        assert result.entries[0].canonical_field == "external_product_id"

    def test_ean_maps_to_ean(self) -> None:
        result = infer_mapping(["EAN"])
        assert result.entries[0].canonical_field == "ean"

    def test_name_maps_to_product_name(self) -> None:
        result = infer_mapping(["Name"])
        assert result.entries[0].canonical_field == "product_name"

    def test_l1_category_maps_to_retailer_category(self) -> None:
        result = infer_mapping(["L1 category"])
        assert result.entries[0].canonical_field == "retailer_category"

    def test_l2_category_maps_to_retailer_subcategory(self) -> None:
        result = infer_mapping(["L2 category"])
        assert result.entries[0].canonical_field == "retailer_subcategory"

    def test_l3_category_maps_to_retailer_subcategory(self) -> None:
        result = infer_mapping(["L3 category"])
        assert result.entries[0].canonical_field == "retailer_subcategory"

    def test_weight_gram_maps_to_weight_per_item_g(self) -> None:
        result = infer_mapping(["Weight gram"])
        assert result.entries[0].canonical_field == "weight_per_item_g"

    def test_sales_maps_to_items_purchased(self) -> None:
        result = infer_mapping(["Sales"])
        assert result.entries[0].canonical_field == "items_purchased"

    def test_protein_per_100_gram_maps_to_protein_pct(self) -> None:
        result = infer_mapping(["Protein per 100 gram"])
        assert result.entries[0].canonical_field == "protein_pct"

    def test_plant_protein_per_100g_maps(self) -> None:
        result = infer_mapping(["Plant protein per 100g"])
        assert result.entries[0].canonical_field == "plant_protein_pct"

    def test_animal_protein_per_100g_maps(self) -> None:
        result = infer_mapping(["Animal protein per 100g"])
        assert result.entries[0].canonical_field == "animal_protein_pct"

    def test_store_label_maps_to_is_own_brand(self) -> None:
        result = infer_mapping(["Store label"])
        assert result.entries[0].canonical_field == "is_own_brand"

    def test_weight_gram_satisfies_weight_requirement(self) -> None:
        result = infer_mapping(
            ["ID", "Name", "Weight gram", "Sales"],
            methodologies=["protein_tracker"],
        )
        assert "weight_per_item_kg" not in result.missing_required_pt


# ---------------------------------------------------------------------------
# Auto-ignore output columns
# ---------------------------------------------------------------------------


class TestAutoIgnoreOutputColumns:
    def test_deterministic_label_auto_ignored(self) -> None:
        result = infer_mapping(["Deterministic Label"])
        entry = result.entries[0]
        assert entry.auto_ignore is True
        assert entry.canonical_field is None

    def test_ai_label_auto_ignored(self) -> None:
        result = infer_mapping(["AI Label"])
        assert result.entries[0].auto_ignore is True

    def test_final_label_auto_ignored(self) -> None:
        result = infer_mapping(["Final Label"])
        assert result.entries[0].auto_ignore is True

    def test_pt_group_auto_ignored(self) -> None:
        result = infer_mapping(["PT Group"])
        assert result.entries[0].auto_ignore is True

    def test_wwf_group_auto_ignored(self) -> None:
        result = infer_mapping(["WWF Group"])
        assert result.entries[0].auto_ignore is True

    def test_script_version_auto_ignored(self) -> None:
        result = infer_mapping(["Script version"])
        assert result.entries[0].auto_ignore is True

    def test_normal_column_not_auto_ignored(self) -> None:
        result = infer_mapping(["external_product_id"])
        assert result.entries[0].auto_ignore is False


# ---------------------------------------------------------------------------
# Comma decimal parsing
# ---------------------------------------------------------------------------


class TestCommaDecimalParsing:
    def test_french_decimal_comma(self) -> None:
        assert _coerce_decimal("0,4") == Decimal("0.4")

    def test_french_decimal_comma_larger(self) -> None:
        assert _coerce_decimal("15,3") == Decimal("15.3")

    def test_european_thousands_and_decimal(self) -> None:
        assert _coerce_decimal("1.234,5") == Decimal("1234.5")

    def test_plain_integer_unaffected(self) -> None:
        assert _coerce_decimal("400") == Decimal("400")

    def test_plain_decimal_unaffected(self) -> None:
        assert _coerce_decimal("0.4") == Decimal("0.4")

    def test_blank_returns_none(self) -> None:
        assert _coerce_decimal("") is None

    def test_none_returns_none(self) -> None:
        assert _coerce_decimal(None) is None


# ---------------------------------------------------------------------------
# Boolean parsing (oui/non/vrai/faux)
# ---------------------------------------------------------------------------


class TestBooleanParsing:
    def test_oui_is_true(self) -> None:
        assert _coerce_bool("oui") is True

    def test_non_is_false(self) -> None:
        assert _coerce_bool("non") is False

    def test_vrai_is_true(self) -> None:
        assert _coerce_bool("vrai") is True

    def test_faux_is_false(self) -> None:
        assert _coerce_bool("faux") is False

    def test_o_is_true(self) -> None:
        assert _coerce_bool("o") is True

    def test_case_insensitive(self) -> None:
        assert _coerce_bool("OUI") is True
        assert _coerce_bool("NON") is False

    def test_true_still_works(self) -> None:
        assert _coerce_bool("true") is True

    def test_false_still_works(self) -> None:
        assert _coerce_bool("false") is False


# ---------------------------------------------------------------------------
# protein_pct missing → warning not error
# ---------------------------------------------------------------------------


def _make_csv(*rows: dict[str, str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


class TestProteinPctWarning:
    def test_missing_protein_pct_yields_warning_not_error(self) -> None:
        """Row without protein_pct should be accepted with a warning, not rejected."""
        data = _make_csv(
            {
                "external_product_id": "SKU1",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_purchased": "100",
                # no protein_pct
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        assert result.read_error is None
        assert len(result.products) == 1, "row should not be rejected"
        assert result.report.error_count == 0
        # Should have one enrichment_needed warning
        assert any(
            w.code == "enrichment_needed" and w.field == "protein_pct"
            for w in result.report.warnings
        )

    def test_provided_protein_pct_no_warning(self) -> None:
        data = _make_csv(
            {
                "external_product_id": "SKU2",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_purchased": "100",
                "protein_pct": "12.5",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        assert not any(w.code == "enrichment_needed" for w in result.report.warnings)

    def test_protein_pct_not_required_for_wwf(self) -> None:
        data = _make_csv(
            {
                "external_product_id": "SKU3",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_sold": "80",
                "is_own_brand": "false",
                "retail_channel": "fresh",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.WWF}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        assert result.report.error_count == 0


# ---------------------------------------------------------------------------
# End-to-end: official PT template headers ingested via mapping
# ---------------------------------------------------------------------------


class TestOfficialPTTemplateEndToEnd:
    def test_pt_template_ingest_with_inferred_mapping(self) -> None:
        """CSV with official PT template headers should ingest correctly."""
        data = _make_csv(
            {
                "ID": "PROD001",
                "Name": "Test Widget",
                "Weight gram": "400",
                "Sales": "100",
                "Protein per 100 gram": "15.0",
            }
        )
        # Build mapping by running infer_mapping on these headers
        preview = infer_mapping(
            ["ID", "Name", "Weight gram", "Sales", "Protein per 100 gram"],
            methodologies=["protein_tracker"],
        )
        mapping = {
            e.normalised_header: e.canonical_field
            for e in preview.entries
            if e.canonical_field and not e.auto_ignore
        }
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            column_mapping=mapping,
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.external_product_id == "PROD001"
        assert p.pt_fields is not None
        assert p.pt_fields.items_purchased == Decimal("100")
        # Weight was 400 g → converted to kg
        assert p.weight_per_item_kg == Decimal("400") * Decimal("0.001")

    def test_comma_decimal_weight_and_protein(self) -> None:
        """Comma-formatted decimals should be parsed correctly."""
        data = _make_csv(
            {
                "external_product_id": "SKU1",
                "product_name": "Widget",
                "weight_per_item_kg": "0,4",
                "items_purchased": "100",
                "protein_pct": "15,3",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        p = result.products[0]
        assert p.weight_per_item_kg == Decimal("0.4")
        assert p.pt_fields is not None
        assert p.pt_fields.protein_pct == Decimal("15.3")

    def test_oui_non_is_own_brand(self) -> None:
        """oui/non booleans should be accepted for is_own_brand."""
        data = _make_csv(
            {
                "external_product_id": "SKU1",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_sold": "80",
                "is_own_brand": "oui",
                "retail_channel": "fresh",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.WWF}),
        )
        assert result.read_error is None
        assert len(result.products) == 1
        assert result.products[0].wwf_fields is not None
        assert result.products[0].wwf_fields.is_own_brand is True
