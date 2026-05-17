"""Regenerate PT fixture `*.expected.json` from the calculator.

The Phase 2 expected.json files were hand-written from rough mental
arithmetic and contain errors (e.g. the headline plant_protein_kg was
missing the plant_based_non_core contribution). This script rebuilds
them from the actual Phase 9 calculator so the fixture is a true
reproducibility regression contract.

It does NOT touch the CSV inputs — those are correct.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

# Make the script runnable from any cwd by inserting the API package on path.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from altera_api.calculation import PTRunVersions, calculate_pt_run  # noqa: E402
from altera_api.domain.common import (  # noqa: E402
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import NormalizedProduct  # noqa: E402
from altera_api.domain.protein_tracker import (  # noqa: E402
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.ingestion import ingest_csv_bytes  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "pt"

PT_VERSIONS = PTRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)
NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000003")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
RUN_ID = UUID("00000000-0000-0000-0000-000000000abc")


# Hand-coded category map per fixture (mirrors the original Phase 2
# expected.json's "pt_group" assignments — those category assignments
# were correct; only the downstream arithmetic was wrong).
PT_TINY_GROUPS = {
    "P-PT-001": ProteinTrackerGroup.PLANT_BASED_CORE,
    "P-PT-002": ProteinTrackerGroup.ANIMAL_CORE,
    "P-PT-003": ProteinTrackerGroup.PLANT_BASED_CORE,
    "P-PT-004": ProteinTrackerGroup.ANIMAL_CORE,
    "P-PT-005": ProteinTrackerGroup.PLANT_BASED_NON_CORE,
    "P-PT-006": ProteinTrackerGroup.COMPOSITE_PRODUCTS,
    "P-PT-007": ProteinTrackerGroup.ANIMAL_CORE,
    "P-PT-008": ProteinTrackerGroup.PLANT_BASED_CORE,
    "P-PT-009": ProteinTrackerGroup.ANIMAL_CORE,
    "P-PT-010": ProteinTrackerGroup.PLANT_BASED_CORE,
    "P-PT-011": ProteinTrackerGroup.ANIMAL_CORE,
    "P-PT-012": ProteinTrackerGroup.PLANT_BASED_CORE,
}

PT_COMPOSITE_GROUPS = dict.fromkeys(
    [f"C-PT-{i:03d}" for i in range(1, 7)], ProteinTrackerGroup.COMPOSITE_PRODUCTS
)

PT_PER_PRODUCT_SPLIT_GROUPS = dict.fromkeys(
    [f"S-PT-{i:03d}" for i in range(1, 7)], ProteinTrackerGroup.COMPOSITE_PRODUCTS
)

PT_MIXED_GROUPS = {
    "M-PT-001": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-002": ProteinTrackerGroup.PLANT_BASED_CORE,
    "M-PT-003": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-004": ProteinTrackerGroup.PLANT_BASED_CORE,
    "M-PT-005": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-006": ProteinTrackerGroup.PLANT_BASED_CORE,
    "M-PT-007": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-008": ProteinTrackerGroup.PLANT_BASED_CORE,
    "M-PT-009": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-010": ProteinTrackerGroup.PLANT_BASED_CORE,
    "M-PT-011": ProteinTrackerGroup.ANIMAL_CORE,
    "M-PT-012": ProteinTrackerGroup.PLANT_BASED_CORE,
}


def _classify(
    products: list[NormalizedProduct],
    group_by_external_id: dict[str, ProteinTrackerGroup],
) -> dict[UUID, ProteinTrackerProductClassification]:
    return {
        p.id: ProteinTrackerProductClassification(
            product_id=p.id,
            pt_group=group_by_external_id[p.external_product_id],
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="pt.fixture.rule",
            updated_at=NOW,
        )
        for p in products
    }


def _serialise(result, products: list[NormalizedProduct]) -> dict:
    id_to_external = {p.id: p.external_product_id for p in products}

    def _d(value):
        # Decimal("0").quantize(...).__str__() returns "0E-8"; format the
        # canonical 8-dp string ourselves so the fixture is human-readable.
        if value is None:
            return None
        return f"{value:.8f}"

    rows_out = [
        {
            "external_product_id": id_to_external[r.product_id],
            "pt_group": r.pt_group.value,
            "volume_kg": _d(r.volume_kg),
            "protein_kg": _d(r.protein_kg),
            "used_per_product_split": r.used_per_product_split,
            "plant_protein_kg": _d(r.plant_protein_kg),
            "animal_protein_kg": _d(r.animal_protein_kg),
        }
        for r in result.rows
    ]
    groups_out = {
        a.pt_group.value: {
            "volume_kg": _d(a.volume_kg),
            "protein_kg": _d(a.protein_kg),
            "item_count": a.item_count,
        }
        for a in result.summary.per_group
    }
    s = result.summary
    return {
        "methodology": "protein_tracker",
        "reporting_period_label": s.reporting_period_label,
        "versions": {
            "methodology_version": s.methodology_version,
            "methodology_source_edition": s.methodology_source_edition,
            "taxonomy_version": s.taxonomy_version,
            "rules_version": s.rules_version,
        },
        "rows": rows_out,
        "groups": groups_out,
        "summary": {
            "plant_protein_kg": _d(s.plant_protein_kg),
            "animal_protein_kg": _d(s.animal_protein_kg),
            "total_in_scope_protein_kg": _d(s.total_in_scope_protein_kg),
            "plant_share_pct": _d(s.plant_share_pct),
            "animal_share_pct": _d(s.animal_share_pct),
            "rows_with_per_product_split": s.rows_with_per_product_split,
            "rows_protein_source_label": s.rows_protein_source_label,
            "rows_protein_source_reference_db": s.rows_protein_source_reference_db,
            "out_of_scope_count": s.out_of_scope_count,
            "unknown_count": s.unknown_count,
        },
    }


def _ingest(path: Path) -> list[NormalizedProduct]:
    data = path.read_bytes()
    result = ingest_csv_bytes(
        data,
        upload_id=UPLOAD_ID,
        project_id=PROJECT_ID,
        organisation_id=ORG_ID,
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        now=NOW,
    )
    if result.read_error is not None or result.report.is_blocking:
        raise RuntimeError(f"{path.name}: ingestion failed — {result.report.errors}")
    return list(result.products)


def _process(csv_name: str, groups: dict[str, ProteinTrackerGroup]) -> None:
    csv_path = FIXTURE_ROOT / csv_name
    out_path = csv_path.with_suffix(".expected.json")
    products = _ingest(csv_path)
    classifications = _classify(products, groups)
    result = calculate_pt_run(
        products,
        classifications,
        run_id=RUN_ID,
        reporting_period_label="FY 2024",
        versions=PT_VERSIONS,
    )
    out_path.write_text(json.dumps(_serialise(result, products), indent=2) + "\n")
    print(f"Wrote {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    _process("pt_tiny.csv", PT_TINY_GROUPS)
    _process("pt_composite_50_50.csv", PT_COMPOSITE_GROUPS)
    _process("pt_per_product_split.csv", PT_PER_PRODUCT_SPLIT_GROUPS)
    _process("pt_mixed_protein_sources.csv", PT_MIXED_GROUPS)
