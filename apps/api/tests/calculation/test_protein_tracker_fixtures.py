"""End-to-end reproducibility regression on the Phase 2 PT fixtures.

For each `tests/fixtures/pt/*.csv` we:

1. Run the ingestion pipeline to produce ``NormalizedProduct``s.
2. Build the classification map from the fixture's
   ``*.expected.json`` (which carries the canonical ``pt_group`` per
   row, derived once and pinned).
3. Run :func:`calculate_pt_run`.
4. Assert the output matches the expected JSON byte-for-byte
   (in terms of per-row volume / protein and the headline summary).

This is the byte-identical reproducibility test described in
``docs/development/testing.md``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.calculation import PTRunVersions, calculate_pt_run
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.ingestion import ingest_csv_bytes

_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
_UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000003")
_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000abc")

_VERSIONS = PTRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


def _ingest(csv_path: Path) -> list[NormalizedProduct]:
    result = ingest_csv_bytes(
        csv_path.read_bytes(),
        upload_id=_UPLOAD_ID,
        project_id=_PROJECT_ID,
        organisation_id=_ORG_ID,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=_NOW,
    )
    assert result.read_error is None, csv_path
    assert not result.report.is_blocking, result.report.errors
    return list(result.products)


def _classifications_from_expected(
    products: list[NormalizedProduct], expected: dict
) -> dict[UUID, ProteinTrackerProductClassification]:
    group_by_external: dict[str, ProteinTrackerGroup] = {
        row["external_product_id"]: ProteinTrackerGroup(row["pt_group"]) for row in expected["rows"]
    }
    return {
        p.id: ProteinTrackerProductClassification(
            product_id=p.id,
            pt_group=group_by_external[p.external_product_id],
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="pt.fixture.rule",
            updated_at=_NOW,
        )
        for p in products
    }


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "pt"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "pt_tiny",
        "pt_composite_50_50",
        "pt_per_product_split",
        "pt_mixed_protein_sources",
    ],
)
def test_fixture_round_trip(fixture_root: Path, fixture_name: str) -> None:
    csv_path = fixture_root / f"{fixture_name}.csv"
    expected_path = fixture_root / f"{fixture_name}.expected.json"
    expected = json.loads(expected_path.read_text())

    products = _ingest(csv_path)
    classifications = _classifications_from_expected(products, expected)
    result = calculate_pt_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label=expected["reporting_period_label"],
        versions=_VERSIONS,
    )

    # --- Per-row figures ---
    by_external = {p.id: p.external_product_id for p in products}
    rows_by_external = {by_external[r.product_id]: r for r in result.rows}
    for expected_row in expected["rows"]:
        ext = expected_row["external_product_id"]
        actual = rows_by_external[ext]
        assert f"{actual.volume_kg:.8f}" == expected_row["volume_kg"], ext
        assert f"{actual.protein_kg:.8f}" == expected_row["protein_kg"], ext
        assert actual.used_per_product_split == expected_row["used_per_product_split"], ext
        if expected_row["plant_protein_kg"] is None:
            assert actual.plant_protein_kg is None, ext
        else:
            assert f"{actual.plant_protein_kg:.8f}" == expected_row["plant_protein_kg"], ext
        if expected_row["animal_protein_kg"] is None:
            assert actual.animal_protein_kg is None, ext
        else:
            assert f"{actual.animal_protein_kg:.8f}" == expected_row["animal_protein_kg"], ext

    # --- Per-group aggregates ---
    actual_groups = {a.pt_group.value: a for a in result.summary.per_group}
    for group_name, expected_group in expected["groups"].items():
        actual_group = actual_groups[group_name]
        assert f"{actual_group.volume_kg:.8f}" == expected_group["volume_kg"], group_name
        assert f"{actual_group.protein_kg:.8f}" == expected_group["protein_kg"], group_name
        assert actual_group.item_count == expected_group["item_count"], group_name

    # --- Headline summary ---
    s = result.summary
    es = expected["summary"]
    assert f"{s.plant_protein_kg:.8f}" == es["plant_protein_kg"]
    assert f"{s.animal_protein_kg:.8f}" == es["animal_protein_kg"]
    assert f"{s.total_in_scope_protein_kg:.8f}" == es["total_in_scope_protein_kg"]
    if es["plant_share_pct"] is None:
        assert s.plant_share_pct is None
        assert s.animal_share_pct is None
    else:
        assert f"{s.plant_share_pct:.8f}" == es["plant_share_pct"]
        assert f"{s.animal_share_pct:.8f}" == es["animal_share_pct"]
    assert s.rows_with_per_product_split == es["rows_with_per_product_split"]
    assert s.rows_protein_source_label == es["rows_protein_source_label"]
    assert s.rows_protein_source_reference_db == es["rows_protein_source_reference_db"]
    assert s.out_of_scope_count == es["out_of_scope_count"]
    assert s.unknown_count == es["unknown_count"]


def test_reproducibility_byte_identical(fixture_root: Path, tmp_path: Path) -> None:
    """Two consecutive runs on the same fixture produce byte-identical output.

    This pins the determinism guarantee that the calc layer is allowed
    to make no random or time-dependent choices.
    """
    csv_path = fixture_root / "pt_tiny.csv"
    expected = json.loads((fixture_root / "pt_tiny.expected.json").read_text())
    products = _ingest(csv_path)
    classifications = _classifications_from_expected(products, expected)

    a = calculate_pt_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
    )
    b = calculate_pt_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
    )

    assert tuple(r.model_dump() for r in a.rows) == tuple(r.model_dump() for r in b.rows)
    assert a.summary.model_dump() == b.summary.model_dump()
