"""Phase 33B — column mapping tests.

Covers:
- normalise_header accent stripping + punctuation normalization
- infer_mapping: exact match, synonym, unmatched, duplicates
- infer_mapping: missing required fields reported correctly
- infer_mapping: enrichment_needed flag for protein_pct
- apply_column_mapping: rename, ignore, passthrough
- apply_column_mapping: empty mapping is a no-op
- end-to-end: CSV with synonym headers ingests correctly via pipeline
"""

from __future__ import annotations

import csv
import io
from uuid import uuid4

from altera_api.domain.common import Methodology
from altera_api.ingestion.headers import normalise_header
from altera_api.ingestion.mapping import (
    apply_column_mapping,
    infer_mapping,
)
from altera_api.ingestion.pipeline import ingest_csv_bytes

# ---------------------------------------------------------------------------
# normalise_header — Phase 33B accent + punctuation enhancements
# ---------------------------------------------------------------------------


class TestNormaliseHeaderPhase33B:
    def test_accented_chars_stripped(self) -> None:
        assert normalise_header("Éléments achetés") == "elements_achetes"

    def test_cedilla_stripped(self) -> None:
        assert normalise_header("Référence") == "reference"

    def test_mixed_punctuation_collapsed(self) -> None:
        assert normalise_header("product.name!") == "product_name"

    def test_leading_trailing_underscores_removed(self) -> None:
        assert normalise_header("__sku__") == "sku"

    def test_tab_in_header(self) -> None:
        assert normalise_header("\tproduct name\t") == "product_name"

    def test_slash_becomes_underscore(self) -> None:
        assert normalise_header("weight/unit") == "weight_unit"

    def test_already_canonical(self) -> None:
        assert normalise_header("items_purchased") == "items_purchased"

    def test_german_umlaut(self) -> None:
        # ö → o (via NFKD + ASCII); ß has no NFKD decomposition so is dropped by ASCII encode
        assert normalise_header("Größe") == "groe"

    def test_numbers_preserved(self) -> None:
        assert normalise_header("level2_category") == "level2_category"


# ---------------------------------------------------------------------------
# infer_mapping
# ---------------------------------------------------------------------------


class TestInferMappingExactMatch:
    def test_canonical_name_exact(self) -> None:
        result = infer_mapping(["external_product_id"])
        assert result.entries[0].canonical_field == "external_product_id"
        assert result.entries[0].confidence == "exact"

    def test_items_purchased_exact(self) -> None:
        result = infer_mapping(["items_purchased"])
        assert result.entries[0].canonical_field == "items_purchased"
        assert result.entries[0].confidence == "exact"


class TestInferMappingSynonyms:
    def test_sku_maps_to_external_product_id(self) -> None:
        result = infer_mapping(["SKU"])
        entry = result.entries[0]
        assert entry.canonical_field == "external_product_id"
        assert entry.confidence == "synonym"

    def test_quantity_maps_to_items_purchased(self) -> None:
        result = infer_mapping(["Quantity"])
        assert result.entries[0].canonical_field == "items_purchased"

    def test_units_sold_maps_to_items_sold(self) -> None:
        result = infer_mapping(["units_sold"])
        assert result.entries[0].canonical_field == "items_sold"

    def test_french_header_synonym(self) -> None:
        result = infer_mapping(["Quantité achetée"])
        assert result.entries[0].canonical_field == "items_purchased"

    def test_gtin_maps_to_ean(self) -> None:
        result = infer_mapping(["GTIN"])
        assert result.entries[0].canonical_field == "ean"

    def test_mdd_maps_to_is_own_brand(self) -> None:
        result = infer_mapping(["MDD"])
        assert result.entries[0].canonical_field == "is_own_brand"

    def test_item_code_maps_to_external_product_id(self) -> None:
        result = infer_mapping(["item_code"])
        assert result.entries[0].canonical_field == "external_product_id"


class TestInferMappingUnmatched:
    def test_unknown_header(self) -> None:
        result = infer_mapping(["revenue_eur"])
        assert result.entries[0].canonical_field is None
        assert result.entries[0].confidence == "none"

    def test_multiple_headers_mixed(self) -> None:
        result = infer_mapping(["SKU", "Product Name", "revenue_eur"])
        assert result.entries[0].canonical_field == "external_product_id"
        assert result.entries[1].canonical_field == "product_name"
        assert result.entries[2].canonical_field is None

    def test_empty_headers_list(self) -> None:
        result = infer_mapping([])
        assert result.entries == []


class TestInferMappingMissingRequired:
    def test_missing_required_pt_reported(self) -> None:
        result = infer_mapping(["SKU", "Product Name"])
        assert "weight_per_item_kg" in result.missing_required_pt
        assert "items_purchased" in result.missing_required_pt

    def test_no_missing_pt_when_all_present(self) -> None:
        result = infer_mapping(
            ["external_product_id", "product_name", "weight_per_item_kg", "items_purchased"]
        )
        assert result.missing_required_pt == []

    def test_missing_required_wwf_reported(self) -> None:
        result = infer_mapping(["SKU"])
        assert "items_sold" in result.missing_required_wwf
        assert "retail_channel" in result.missing_required_wwf

    def test_synonym_satisfies_required(self) -> None:
        result = infer_mapping(
            ["SKU", "Product Name", "weight_kg", "Quantity"]
        )
        assert "external_product_id" not in result.missing_required_pt
        assert "product_name" not in result.missing_required_pt
        assert "weight_per_item_kg" not in result.missing_required_pt
        assert "items_purchased" not in result.missing_required_pt


class TestInferMappingDuplicates:
    def test_duplicate_normalised_headers(self) -> None:
        result = infer_mapping(["Product Name", "product_name"])
        assert "product_name" in result.duplicate_normalised

    def test_no_false_positive_duplicates(self) -> None:
        result = infer_mapping(["SKU", "Product Name", "Quantity"])
        assert result.duplicate_normalised == []


class TestInferMappingEnrichmentNeeded:
    def test_protein_pct_enrichment_needed(self) -> None:
        result = infer_mapping(["protein_pct"])
        assert result.entries[0].enrichment_needed is True

    def test_other_fields_not_enrichment_needed(self) -> None:
        result = infer_mapping(["external_product_id"])
        assert result.entries[0].enrichment_needed is False


# ---------------------------------------------------------------------------
# apply_column_mapping
# ---------------------------------------------------------------------------


class TestApplyColumnMapping:
    def test_rename_key(self) -> None:
        row: dict[str, object] = {"sku": "ABC123", "product_name": "Widget"}
        result = apply_column_mapping(row, {"sku": "external_product_id"})
        assert "external_product_id" in result
        assert result["external_product_id"] == "ABC123"
        assert "sku" not in result

    def test_ignore_drops_key(self) -> None:
        row: dict[str, object] = {"revenue": "1000", "product_name": "Widget"}
        result = apply_column_mapping(row, {"revenue": "ignore"})
        assert "revenue" not in result
        assert "product_name" in result

    def test_passthrough_unmapped_key(self) -> None:
        row: dict[str, object] = {"sku": "X1", "extra_col": "val"}
        result = apply_column_mapping(row, {"sku": "external_product_id"})
        assert "extra_col" in result
        assert result["extra_col"] == "val"

    def test_empty_mapping_is_noop(self) -> None:
        row: dict[str, object] = {"sku": "X1", "product_name": "Widget"}
        result = apply_column_mapping(row, {})
        assert result == row

    def test_multiple_renames(self) -> None:
        row: dict[str, object] = {"sku": "A", "qty": "10", "wt": "0.5"}
        mapping = {
            "sku": "external_product_id",
            "qty": "items_purchased",
            "wt": "weight_per_item_kg",
        }
        result = apply_column_mapping(row, mapping)
        assert set(result.keys()) == {"external_product_id", "items_purchased", "weight_per_item_kg"}

    def test_ignore_and_rename_combined(self) -> None:
        row: dict[str, object] = {"sku": "A", "revenue": "100", "name": "Widget"}
        result = apply_column_mapping(
            row,
            {"sku": "external_product_id", "revenue": "ignore"},
        )
        assert "external_product_id" in result
        assert "revenue" not in result
        assert "name" in result  # passthrough


# ---------------------------------------------------------------------------
# End-to-end: ingest_csv_bytes with column_mapping
# ---------------------------------------------------------------------------


def _make_csv(*rows: dict[str, str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


class TestPipelineWithColumnMapping:
    """Check that column_mapping is correctly applied within the full pipeline."""

    def test_synonym_header_mapped_and_ingested(self) -> None:
        """CSV with 'SKU' instead of 'external_product_id' ingests with mapping."""
        data = _make_csv(
            {
                "SKU": "PROD001",
                "Product Name": "Test Widget",
                "weight_kg": "0.5",
                "Quantity": "100",
                "protein_pct": "15.0",
            }
        )
        mapping = {
            "sku": "external_product_id",
            "product_name": "product_name",
            "weight_kg": "weight_per_item_kg",
            "quantity": "items_purchased",
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
        assert result.products[0].external_product_id == "PROD001"

    def test_no_mapping_still_works(self) -> None:
        """Passing column_mapping=None leaves behaviour unchanged."""
        data = _make_csv(
            {
                "external_product_id": "SKU1",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_purchased": "50",
                "protein_pct": "12.0",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            column_mapping=None,
        )
        assert result.read_error is None
        assert len(result.products) == 1

    def test_ignore_drops_column(self) -> None:
        """Columns mapped to 'ignore' should not appear in dropped_columns (they were never kept)."""
        data = _make_csv(
            {
                "external_product_id": "SKU1",
                "product_name": "Widget",
                "weight_per_item_kg": "0.4",
                "items_purchased": "50",
                "protein_pct": "8.0",
                "revenue_eur": "999",
            }
        )
        result = ingest_csv_bytes(
            data,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            column_mapping={"revenue_eur": "ignore"},
        )
        assert result.read_error is None
        # revenue_eur should have been silently dropped by mapping, not reached filter
        assert "revenue_eur" not in result.dropped_columns

    def test_existing_canonical_csv_unaffected(self) -> None:
        """A correctly-formatted CSV with canonical headers is unaffected by empty mapping."""
        data = _make_csv(
            {
                "external_product_id": "SKU2",
                "product_name": "Gadget",
                "weight_per_item_kg": "0.3",
                "items_purchased": "200",
                "protein_pct": "10.0",
                "items_sold": "180",
                "is_own_brand": "false",
                "retail_channel": "fresh",
            }
        )
        for mapping in [None, {}]:
            result = ingest_csv_bytes(
                data,
                upload_id=uuid4(),
                project_id=uuid4(),
                organisation_id=uuid4(),
                methodologies_enabled=frozenset(
                    {Methodology.PROTEIN_TRACKER, Methodology.WWF}
                ),
                column_mapping=mapping,
            )
            assert result.read_error is None
            assert len(result.products) == 1
