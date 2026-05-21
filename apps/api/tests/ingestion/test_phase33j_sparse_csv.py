"""Phase 33J — sparse retailer CSV ingestion (Carrefour-style 3-column).

The staging blocker was that a CSV with only:

  Product Name (FR), Poids unitaire produit, Volume

failed with "external_product_id is required" on every row, and when
the weight column was mapped to ``weight_per_item_kg`` values like 133
became "weight_too_large" instead of being recognised as grams.

This phase changes the contract:

  * external_product_id is optional — auto-generated when missing.
  * "Poids unitaire produit" is recognised as grams via header synonyms,
    so the default auto-mapping picks weight_per_item_g.
  * If the user explicitly maps a gram-scale column to
    weight_per_item_kg, the parser surfaces a specific, French-aware
    error pointing them at the (g) alternative — no silent conversion.
  * Soft warnings fire for borderline values (kg >= 5, or g < 5)
    without blocking ingestion.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from altera_api.domain.common import Methodology
from altera_api.ingestion.mapping import infer_mapping
from altera_api.ingestion.parser import parse_row
from altera_api.ingestion.pipeline import ingest_csv_bytes

_CARREFOUR_SPARSE_CSV = (
    b"Product Name (FR),Poids unitaire produit,Volume\n"
    b"Blanc de poulet,133,1000\n"
    b"Tofu nature,200,500\n"
    b"Salade de ceres,250,800\n"
)


class TestSparseCarrefourCSV:
    def test_ingests_with_auto_generated_ids(self) -> None:
        result = ingest_csv_bytes(
            _CARREFOUR_SPARSE_CSV,
            upload_id=UUID("00000000-0000-0000-0000-0000000a1b1c"),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
            now=datetime(2026, 5, 21),
        )
        assert result.read_error is None
        # The 3 rows must all ingest cleanly — no external_product_id
        # error, weight resolved from "Poids unitaire produit" → grams.
        assert result.report.error_count == 0, result.report.errors
        assert len(result.products) == 3

    def test_auto_generated_ids_are_unique_and_prefixed(self) -> None:
        result = ingest_csv_bytes(
            _CARREFOUR_SPARSE_CSV,
            upload_id=UUID("00000000-0000-0000-0000-0000000a1b1c"),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        ids = [p.external_product_id for p in result.products]
        assert len(set(ids)) == 3
        assert all(i.startswith("AUTO-") for i in ids)

    def test_weight_converted_internally_from_grams(self) -> None:
        result = ingest_csv_bytes(
            _CARREFOUR_SPARSE_CSV,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        by_name = {p.product_name: p for p in result.products}
        assert by_name["Blanc de poulet"].weight_per_item_kg == _kg("0.133")
        assert by_name["Tofu nature"].weight_per_item_kg == _kg("0.2")
        assert by_name["Salade de ceres"].weight_per_item_kg == _kg("0.25")

    def test_missing_protein_pct_is_warning_not_error(self) -> None:
        result = ingest_csv_bytes(
            _CARREFOUR_SPARSE_CSV,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        assert result.report.error_count == 0
        assert any(
            w.code == "enrichment_needed" for w in result.report.warnings
        ), "missing protein_pct should surface as enrichment warning"

    def test_volume_maps_to_items_purchased(self) -> None:
        result = ingest_csv_bytes(
            _CARREFOUR_SPARSE_CSV,
            upload_id=uuid4(),
            project_id=uuid4(),
            organisation_id=uuid4(),
            methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        by_name = {p.product_name: p for p in result.products}
        assert by_name["Blanc de poulet"].pt_fields is not None
        assert by_name["Blanc de poulet"].pt_fields.items_purchased == _kg("1000")


class TestRequiredFieldsRecomputation:
    """infer_mapping must reflect the Phase 33J required-PT contract:
    external_product_id is no longer required; weight is satisfied by
    either kg or g."""

    def test_sparse_carrefour_has_no_missing_pt_fields(self) -> None:
        preview = infer_mapping(
            ["Product Name (FR)", "Poids unitaire produit", "Volume"],
            methodologies=["protein_tracker"],
        )
        assert preview.missing_required_pt == []

    def test_external_product_id_not_in_required_pt(self) -> None:
        # No headers at all → only product_name, items_purchased, and
        # weight should be flagged; external_product_id must NOT appear.
        preview = infer_mapping([], methodologies=["protein_tracker"])
        assert "external_product_id" not in preview.missing_required_pt
        assert "product_name" in preview.missing_required_pt
        assert "weight_per_item_kg" in preview.missing_required_pt
        assert "items_purchased" in preview.missing_required_pt


class TestWeightUnitErrorsAndWarnings:
    """Phase 33J — kg-vs-g must NEVER silently auto-convert."""

    def test_kg_mapping_with_gram_values_gives_actionable_error(self) -> None:
        # User explicitly mapped to weight_per_item_kg but the value
        # is 133 — clearly grams. Parser should emit the new
        # ``weight_unit_likely_grams`` error pointing at the (g) variant.
        raw, errors, _ = parse_row(
            {
                "external_product_id": "P-1",
                "product_name": "Poulet",
                "weight_per_item_kg": "133",
                "items_purchased": "10",
            },
            upload_id=uuid4(),
            row_number=1,
        )
        assert raw is None
        codes = {e.code for e in errors}
        assert "weight_unit_likely_grams" in codes
        msg = next(e.message for e in errors if e.code == "weight_unit_likely_grams")
        assert "Poids unitaire (g)" in msg

    def test_kg_with_borderline_heavy_value_warns(self) -> None:
        # 5kg per unit is heavy but plausible (e.g. industrial pack).
        # We warn but do not block.
        raw, errors, warnings = parse_row(
            {
                "external_product_id": "P-1",
                "product_name": "Big pack",
                "weight_per_item_kg": "5",
                "items_purchased": "10",
            },
            upload_id=uuid4(),
            row_number=1,
        )
        assert raw is not None
        assert errors == ()
        assert any(
            w.code == "weight_unit_likely_grams_warning" for w in warnings
        )

    def test_grams_mapping_with_kg_like_value_warns(self) -> None:
        # User mapped to weight_per_item_g but value is 0.4 — looks
        # like kg. Warn (do not block).
        raw, errors, warnings = parse_row(
            {
                "external_product_id": "P-1",
                "product_name": "Tofu",
                "weight_per_item_g": "0.4",
                "items_purchased": "10",
            },
            upload_id=uuid4(),
            row_number=1,
        )
        assert raw is not None
        assert errors == ()
        assert any(
            w.code == "weight_unit_likely_kg_warning" for w in warnings
        )

    def test_grams_mapping_with_gram_value_no_warning(self) -> None:
        raw, errors, warnings = parse_row(
            {
                "external_product_id": "P-1",
                "product_name": "Tofu",
                "weight_per_item_g": "200",
                "items_purchased": "10",
            },
            upload_id=uuid4(),
            row_number=1,
        )
        assert raw is not None
        assert errors == ()
        codes = {w.code for w in warnings}
        assert "weight_unit_likely_kg_warning" not in codes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _kg(s: str):
    from decimal import Decimal

    return Decimal(s)
