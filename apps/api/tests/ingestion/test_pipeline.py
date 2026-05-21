"""End-to-end ingestion tests, including against Phase 2 fixtures."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.ingestion.pipeline import ingest_csv_bytes


def test_pt_tiny_fixture_ingests_cleanly(
    fixture_root: Path,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> None:
    data = (fixture_root / "pt" / "pt_tiny.csv").read_bytes()
    result = ingest_csv_bytes(
        data,
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert result.read_error is None
    assert result.dropped_columns == ()
    assert result.report.error_count == 0
    assert result.report.total_rows == 12
    assert len(result.products) == 12
    # PT-only: every product has pt_fields, no wwf_fields
    for p in result.products:
        assert p.pt_fields is not None
        assert p.wwf_fields is None


def test_wwf_tiny_fixture_ingests_cleanly(
    fixture_root: Path,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> None:
    data = (fixture_root / "wwf" / "wwf_tiny.csv").read_bytes()
    result = ingest_csv_bytes(
        data,
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=now,
    )
    assert result.read_error is None
    assert result.report.error_count == 0
    assert result.report.total_rows == 12
    assert len(result.products) == 12
    for p in result.products:
        assert p.wwf_fields is not None
        assert p.pt_fields is None
        assert p.is_own_brand is not None


def test_cross_methodology_fixture(
    fixture_root: Path,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> None:
    data = (fixture_root / "cross" / "cross_pt_and_wwf_tiny.csv").read_bytes()
    result = ingest_csv_bytes(
        data,
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER, Methodology.WWF}),
        now=now,
    )
    assert result.read_error is None
    assert result.report.error_count == 0
    assert len(result.products) == 12
    for p in result.products:
        assert p.pt_fields is not None
        assert p.wwf_fields is not None


def test_pt_unit_conversion_fixture_g_and_lb_normalised(
    fixture_root: Path,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> None:
    data = (fixture_root / "pt" / "pt_unit_conversion.csv").read_bytes()
    result = ingest_csv_bytes(
        data,
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert result.read_error is None
    assert result.report.error_count == 0
    by_id = {p.external_product_id: p for p in result.products}
    # 300 g → 0.300 kg
    assert by_id["U-PT-001"].weight_per_item_kg.normalize() == (0.3).real or by_id[
        "U-PT-001"
    ].weight_per_item_kg == __import__("decimal").Decimal("0.300")
    # 2 lb → 0.90718474 kg
    from decimal import Decimal

    assert by_id["U-PT-011"].weight_per_item_kg == Decimal("2.0") * Decimal("0.45359237")


def test_pipeline_drops_commercial_columns_with_audit() -> None:
    csv_bytes = (
        b"external_product_id,product_name,weight_per_item_kg,revenue,supplier_id,"
        b"items_purchased,protein_pct\n"
        b"P-001,Lentil Soup,0.400,12345.67,S-9,1000,4.5\n"
    )
    result = ingest_csv_bytes(
        csv_bytes,
        upload_id=UUID("00000000-0000-0000-0000-000000000003"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        organisation_id=UUID("00000000-0000-0000-0000-000000000001"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=datetime(2026, 5, 15),
    )
    assert result.dropped_columns == ("revenue", "supplier_id")
    assert result.report.error_count == 0
    assert len(result.products) == 1
    p = result.products[0]
    # The product must not carry any commercial attribute, ever.
    assert not hasattr(p, "revenue")
    assert not hasattr(p, "supplier_id")


def test_pipeline_aggregates_row_errors() -> None:
    csv_bytes = (
        b"external_product_id,product_name,weight_per_item_kg,weight_per_item_g,"
        b"items_purchased,protein_pct\n"
        b"P-OK,Lentil Soup,0.4,,1000,4.5\n"
        b"P-MIX,Bad Row,0.4,400,500,5\n"  # mixed weight units
        b"P-NEG,Bad Row,-1,,500,5\n"  # negative weight
        b"P-PCT,Bad Row,0.4,,500,150\n"  # protein out of range
        b",Auto-ID Row,0.4,,500,5\n"  # Phase 33J: missing ID now auto-generated
    )
    result = ingest_csv_bytes(
        csv_bytes,
        upload_id=UUID("00000000-0000-0000-0000-000000000003"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        organisation_id=UUID("00000000-0000-0000-0000-000000000001"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=datetime(2026, 5, 15),
    )
    codes = {e.code for e in result.report.errors}
    assert "mixed_weight_units" in codes
    assert "weight_non_positive" in codes
    assert "protein_out_of_range" in codes
    # Phase 33J: missing external_product_id no longer errors — the row
    # ingests successfully with an auto-generated ID.
    assert "missing_required" not in {
        e.code for e in result.report.errors if e.field == "external_product_id"
    }
    assert len(result.products) == 2  # P-OK + Auto-ID row
    auto_id_product = next(
        p for p in result.products if p.external_product_id.startswith("AUTO-")
    )
    assert auto_id_product.product_name == "Auto-ID Row"
    # The auto-generation warning is recorded in the report.
    assert any(
        w.code == "auto_generated" and w.field == "external_product_id"
        for w in result.report.warnings
    )


def test_pipeline_reports_oversize_as_read_error() -> None:
    from altera_api.ingestion.csv_reader import CSVReadConfig

    tiny = b"x\n" * 50
    result = ingest_csv_bytes(
        b"product_name\n" + tiny,
        upload_id=UUID("00000000-0000-0000-0000-000000000003"),
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        organisation_id=UUID("00000000-0000-0000-0000-000000000001"),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        config=CSVReadConfig(max_bytes=20),
    )
    assert result.read_error is not None
    assert result.succeeded is False
