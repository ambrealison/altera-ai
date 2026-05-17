"""End-to-end PT export tests against the Phase 2 fixtures."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.calculation import PTRunVersions, calculate_pt_run
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.project import PTValidationStatus
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.exports import (
    ExportClassificationMeta,
    ExportProductMaster,
    PTExportContext,
    RunMetadata,
    render_pt_csv,
    render_pt_json,
    render_pt_markdown,
)
from altera_api.ingestion import ingest_csv_bytes

_VERSIONS = PTRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


def _load_pt_fixture(fixture_root: Path, name: str) -> tuple:
    csv_path = fixture_root / "pt" / f"{name}.csv"
    expected = json.loads((fixture_root / "pt" / f"{name}.expected.json").read_text())
    return csv_path, expected


def _build_context(
    fixture_root: Path,
    fixture_name: str,
    *,
    run_id: UUID,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> PTExportContext:
    csv_path, expected = _load_pt_fixture(fixture_root, fixture_name)

    ingest = ingest_csv_bytes(
        csv_path.read_bytes(),
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=now,
    )
    assert ingest.read_error is None and not ingest.report.is_blocking
    products = list(ingest.products)

    group_by_external = {
        row["external_product_id"]: ProteinTrackerGroup(row["pt_group"])
        for row in expected["rows"]
    }
    classifications: dict[UUID, ProteinTrackerProductClassification] = {
        p.id: ProteinTrackerProductClassification(
            product_id=p.id,
            pt_group=group_by_external[p.external_product_id],
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="pt.fixture.rule",
            updated_at=now,
        )
        for p in products
    }

    result = calculate_pt_run(
        products,
        classifications,
        run_id=run_id,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
    )

    return PTExportContext(
        run=RunMetadata(
            run_id=run_id,
            project_slug="fixture-project",
            started_at=now,
            finished_at=now,
            triggered_by=UUID("00000000-0000-0000-0000-0000000000a1"),
        ),
        summary=result.summary,
        rows=result.rows,
        products={
            p.id: ExportProductMaster(
                product_id=p.id,
                external_product_id=p.external_product_id,
                product_name=p.product_name,
                brand=p.brand,
            )
            for p in products
        },
        classifications={
            p.id: ExportClassificationMeta(
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                rule_id="pt.fixture.rule",
            )
            for p in products
        },
        pt_validation_status=PTValidationStatus.DRAFT,
        protein_sources={
            p.id: p.pt_fields.protein_source
            for p in products
            if p.pt_fields is not None
        },
        items_purchased={
            p.id: p.pt_fields.items_purchased
            for p in products
            if p.pt_fields is not None
        },
        weights_per_item={p.id: p.weight_per_item_kg for p in products},
    )


class TestPTCSV:
    @pytest.mark.parametrize(
        "fixture_name",
        ["pt_tiny", "pt_composite_50_50", "pt_per_product_split"],
    )
    def test_csv_round_trip_matches_expected_per_row(
        self,
        fixture_root: Path,
        fixture_name: str,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            fixture_name,
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        data = render_pt_csv(ctx)
        # CSV must be UTF-8 with BOM.
        assert data.startswith(b"\xef\xbb\xbf")

        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        rows = list(reader)
        assert reader.fieldnames is not None
        # First row's run_id is the run we built.
        assert rows[0]["run_id"] == str(run_id)
        assert rows[0]["methodology"] == "protein_tracker"
        assert rows[0]["methodology_source_edition"] == _VERSIONS.methodology_source_edition

        # Row count matches.
        expected = json.loads(
            (fixture_root / "pt" / f"{fixture_name}.expected.json").read_text()
        )
        assert len(rows) == len(expected["rows"])

        # Per-row volume_kg and protein_kg match expected.
        by_external = {r["external_product_id"]: r for r in rows}
        for expected_row in expected["rows"]:
            ext = expected_row["external_product_id"]
            assert by_external[ext]["volume_kg"] == expected_row["volume_kg"]
            assert by_external[ext]["protein_kg"] == expected_row["protein_kg"]

    def test_csv_never_emits_commercial_columns(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            "pt_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        data = render_pt_csv(ctx).decode("utf-8-sig")
        forbidden = (
            "revenue",
            "margin",
            "supplier_id",
            "supplier_name",
            "cost_price",
            "sales_value",
            "store_id",
            "promotion_",
            "confidential_",
            "internal_",
        )
        header = data.split("\n", 1)[0]
        for col in forbidden:
            assert col not in header, col


class TestPTJSON:
    def test_json_structure(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            "pt_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        doc = json.loads(render_pt_json(ctx))
        assert doc["run"]["methodology"] == "protein_tracker"
        assert doc["run"]["methodology_version"] == "1.0.0"
        # Summary numbers match the fixture.
        expected = json.loads((fixture_root / "pt" / "pt_tiny.expected.json").read_text())
        assert doc["summary"]["plant_protein_kg"] == expected["summary"]["plant_protein_kg"]
        assert doc["summary"]["animal_protein_kg"] == expected["summary"]["animal_protein_kg"]
        assert doc["summary"]["plant_share_pct"] == expected["summary"]["plant_share_pct"]
        assert doc["summary"]["by_group"]["plant_based_core"]["item_count"] == 5
        # Rows shape — at least one entry with the per-row classification block.
        first_row = doc["rows"][0]
        assert "classification" in first_row
        assert first_row["classification"]["source"] == "deterministic"

    def test_json_decimal_precision_preserved(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            "pt_per_product_split",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        doc = json.loads(render_pt_json(ctx))
        # 39.07291729 — the precise value from Phase 9. Numbers are
        # emitted as strings to preserve precision.
        assert doc["summary"]["plant_share_pct"] == "39.07291729"

    def test_json_is_deterministic(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            "pt_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        a = render_pt_json(ctx)
        b = render_pt_json(ctx)
        assert a == b


class TestPTMarkdown:
    def test_markdown_contains_headline_and_groups(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        ctx = _build_context(
            fixture_root,
            "pt_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        md = render_pt_markdown(ctx)
        assert "# Protein Tracker report" in md
        assert "Reporting period:" in md
        # Headline numbers
        assert "358.88000000" in md  # plant_protein_kg
        assert "1576.85000000" in md  # animal_protein_kg
        # Four-group table
        assert "Plant-based, core" in md
        assert "Animal-based, core" in md
        # PT validation status reflected
        assert "draft" in md
        # Methodology footnote
        assert "GPA" in md or "Green Protein Alliance" in md

    def test_markdown_null_shares_render_no_data(
        self,
        fixture_root: Path,
        run_id: UUID,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
    ) -> None:
        # Empty-row case: build a context with no products → null shares.
        ctx = PTExportContext(
            run=RunMetadata(
                run_id=run_id, project_slug="empty", started_at=now, finished_at=now
            ),
            summary=calculate_pt_run(
                [],
                {},
                run_id=run_id,
                reporting_period_label="FY 2024",
                versions=_VERSIONS,
            ).summary,
            rows=(),
            products={},
            classifications={},
            pt_validation_status=PTValidationStatus.NONE,
        )
        md = render_pt_markdown(ctx)
        assert "No in-scope protein found" in md
