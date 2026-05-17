"""Regenerate WWF fixture `*.expected.json` from the calculator.

Same pattern as ``regenerate_pt_expected.py``: the Phase 2 expected
files were hand-written with a richer-than-needed structure and had
arithmetic errors; this script rebuilds them from the actual Phase 10
calculator so each fixture becomes a true reproducibility regression
contract aligned with the ``WWFCalculationSummary`` shape.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from altera_api.calculation import WWFRunVersions, calculate_wwf_run  # noqa: E402
from altera_api.domain.common import (  # noqa: E402
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import NormalizedProduct  # noqa: E402
from altera_api.domain.wwf import (  # noqa: E402
    WWFCompositeIngredient,
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.ingestion import ingest_csv_bytes  # noqa: E402

FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "wwf"
CROSS_ROOT = REPO_ROOT / "tests" / "fixtures" / "cross"

WWF_VERSIONS = WWFRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)
NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000003")
PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
RUN_ID = UUID("00000000-0000-0000-0000-000000000def")


# Per-fixture classification map. Each entry: external_product_id -> kwargs
# for WWFProductClassification (minus product_id, source/confidence/etc.).
WWF_TINY: dict[str, dict] = {
    "W-WWF-001": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.RED_MEAT),
    "W-WWF-002": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
    ),
    "W-WWF-003": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.SEAFOOD),
    "W-WWF-004": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.EGGS),
    "W-WWF-005": dict(
        wwf_food_group=WWFFoodGroup.FG5, fg5_grain_kind=WWFFG5GrainKind.WHOLE_GRAIN
    ),
    "W-WWF-006": dict(wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.CHEESE),
    "W-WWF-007": dict(
        wwf_food_group=WWFFoodGroup.FG3, fg3_subgroup=WWFFG3Subgroup.PLANT_BASED_FAT
    ),
    "W-WWF-008": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.LEGUMES),
    "W-WWF-009": dict(wwf_food_group=WWFFoodGroup.FG4),
    "W-WWF-010": dict(
        wwf_food_group=WWFFoodGroup.FG7, fg7_snack_kind=WWFFG7SnackKind.PLANT_BASED_SNACK
    ),
    "W-WWF-011": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "W-WWF-012": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.POULTRY),
}

WWF_DAIRY: dict[str, dict] = {
    "D-WWF-001": dict(wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.CHEESE),
    "D-WWF-002": dict(wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.CHEESE),
    "D-WWF-003": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
    ),
    "D-WWF-004": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
    ),
    "D-WWF-005": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.OTHER_DAIRY_ANIMAL
    ),
    "D-WWF-006": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT
    ),
    "D-WWF-007": dict(
        wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT
    ),
    "D-WWF-008": dict(wwf_food_group=WWFFoodGroup.FG2, fg2_subgroup=WWFFG2Subgroup.CHEESE),
}

WWF_STEP1: dict[str, dict] = {
    "CS-WWF-001": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "CS-WWF-002": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.POULTRY,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "CS-WWF-003": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.SEAFOOD,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.SEAFOOD_BASED,
    ),
    "CS-WWF-004": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.SEAFOOD,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.SEAFOOD_BASED,
    ),
    "CS-WWF-005": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.LEGUMES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGETARIAN,
    ),
    "CS-WWF-006": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.LEGUMES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
    ),
    "CS-WWF-007": dict(
        wwf_food_group=WWFFoodGroup.FG2,
        fg2_subgroup=WWFFG2Subgroup.CHEESE,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGETARIAN,
    ),
    "CS-WWF-008": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "CS-WWF-009": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
    ),
    "CS-WWF-010": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.PROCESSED_MEATS_ALTERNATIVES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "CS-WWF-011": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.SEAFOOD,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.SEAFOOD_BASED,
    ),
    "CS-WWF-012": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
    ),
}

# Excluded foods: spices/salt/alcohol/water are out_of_scope; chicken + beans are FG1.
WWF_EXCLUDED: dict[str, dict] = {
    "E-WWF-001": dict(wwf_food_group=WWFFoodGroup.OUT_OF_SCOPE),
    "E-WWF-002": dict(wwf_food_group=WWFFoodGroup.OUT_OF_SCOPE),
    "E-WWF-003": dict(wwf_food_group=WWFFoodGroup.OUT_OF_SCOPE),
    "E-WWF-004": dict(wwf_food_group=WWFFoodGroup.OUT_OF_SCOPE),
    "E-WWF-005": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.POULTRY),
    "E-WWF-006": dict(wwf_food_group=WWFFoodGroup.FG1, fg1_subgroup=WWFFG1Subgroup.LEGUMES),
}

# Step 2 fixture: 6 own-brand composites, with 4 of them carrying ingredient data.
WWF_STEP2_CLASSIFICATIONS: dict[str, dict] = {
    "P-VL-001": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
    ),
    "P-VL-002": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.POULTRY,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "P-VL-003": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.LEGUMES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGETARIAN,
    ),
    "P-VL-004": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.SEAFOOD,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.SEAFOOD_BASED,
    ),
    "P-VL-005": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
    ),
    "P-VL-006": dict(
        wwf_food_group=WWFFoodGroup.FG1,
        fg1_subgroup=WWFFG1Subgroup.LEGUMES,
        wwf_is_composite=True,
        composite_step1_bucket=WWFCompositeStep1Bucket.VEGAN,
    ),
}


def _classify(
    products: list[NormalizedProduct],
    group_kwargs_by_external_id: dict[str, dict],
) -> dict[UUID, WWFProductClassification]:
    out: dict[UUID, WWFProductClassification] = {}
    for p in products:
        kwargs = dict(group_kwargs_by_external_id[p.external_product_id])
        kwargs.setdefault("wwf_is_composite", False)
        out[p.id] = WWFProductClassification(
            product_id=p.id,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="wwf.fixture.rule",
            updated_at=NOW,
            **kwargs,
        )
    return out


def _ingredients_for_step2(products: list[NormalizedProduct]) -> dict[UUID, tuple]:
    by_external = {p.external_product_id: p.id for p in products}
    # Hand-typed from tests/fixtures/wwf/wwf_step2_ingredients.json:
    raw = {
        "P-VL-001": [
            (WWFFoodGroup.FG1, WWFFG1Subgroup.ALTERNATIVE_PROTEIN_SOURCES, None, "0.070"),
            (WWFFoodGroup.FG2, None, WWFFG2Subgroup.DAIRY_ALTERNATIVE_PLANT, "0.020"),
            (WWFFoodGroup.FG5, None, None, "0.100"),
            (WWFFoodGroup.FG4, None, None, "0.150"),
        ],
        "P-VL-002": [
            (WWFFoodGroup.FG1, WWFFG1Subgroup.POULTRY, None, "0.070"),
            (WWFFoodGroup.FG5, None, None, "0.133"),
            (WWFFoodGroup.FG4, None, None, "0.098"),
        ],
        "P-VL-003": [
            (WWFFoodGroup.FG4, None, None, "0.160"),
            (WWFFoodGroup.FG1, WWFFG1Subgroup.LEGUMES, None, "0.072"),
            (WWFFoodGroup.FG1, WWFFG1Subgroup.MEAT_EGG_SEAFOOD_ALTERNATIVES, None, "0.048"),
            (WWFFoodGroup.FG4, None, None, "0.060"),
        ],
        "P-VL-004": [
            (WWFFoodGroup.FG1, WWFFG1Subgroup.SEAFOOD, None, "0.090"),
            (WWFFoodGroup.FG5, None, None, "0.120"),
            (WWFFoodGroup.FG4, None, None, "0.042"),
        ],
        # P-VL-005 branded — ingredients ignored even if supplied.
        # P-VL-006 own-brand but no Step 2 data — falls back to Step 1 only.
    }
    out: dict[UUID, tuple[WWFCompositeIngredient, ...]] = {}
    ingredient_uid = 1
    for ext, items in raw.items():
        parent = by_external[ext]
        instances = []
        for fg, fg1, fg2, weight in items:
            instances.append(
                WWFCompositeIngredient(
                    id=UUID(f"00000000-0000-0000-0000-{ingredient_uid:012d}"),
                    parent_product_id=parent,
                    food_group=fg,
                    fg1_subgroup=fg1,
                    fg2_subgroup=fg2,
                    ingredient_weight_kg_per_item=Decimal(weight),
                )
            )
            ingredient_uid += 1
        out[parent] = tuple(instances)
    return out


def _serialise(result, products: list[NormalizedProduct]) -> dict:
    id_to_external = {p.id: p.external_product_id for p in products}

    def _d(v):
        return f"{v:.8f}" if v is not None else None

    rows_out = [
        {
            "external_product_id": id_to_external[r.product_id],
            "wwf_food_group": r.wwf_food_group.value,
            "wwf_subgroup_label": r.wwf_subgroup_label,
            "wwf_is_composite": r.wwf_is_composite,
            "wwf_composite_step1_bucket": (
                r.wwf_composite_step1_bucket.value if r.wwf_composite_step1_bucket else None
            ),
            "weight_kg": _d(r.weight_kg),
            "weight_kg_dairy_equiv": _d(r.weight_kg_dairy_equiv),
        }
        for r in result.rows
    ]
    s = result.summary
    food_groups_out = [
        {
            "food_group": a.food_group.value,
            "weight_kg": _d(a.weight_kg),
            "weight_kg_dairy_equiv": _d(a.weight_kg_dairy_equiv),
            "share_pct": _d(a.share_pct),
            "phd_reference_share_pct": _d(a.phd_reference_share_pct),
        }
        for a in s.per_food_group
    ]
    return {
        "methodology": "wwf",
        "reporting_period_label": s.reporting_period_label,
        "versions": {
            "methodology_version": s.methodology_version,
            "methodology_source_edition": s.methodology_source_edition,
            "taxonomy_version": s.taxonomy_version,
            "rules_version": s.rules_version,
        },
        "rows": rows_out,
        "per_food_group": food_groups_out,
        "summary": {
            "total_sales_weight_in_scope_kg": _d(s.total_sales_weight_in_scope_kg),
            "composites_total_weight_kg": _d(s.composites_total_weight_kg),
            "composites_meat_based_kg": _d(s.composites_meat_based_kg),
            "composites_seafood_based_kg": _d(s.composites_seafood_based_kg),
            "composites_vegetarian_kg": _d(s.composites_vegetarian_kg),
            "composites_vegan_kg": _d(s.composites_vegan_kg),
            "whole_diet_plant_weight_kg": _d(s.whole_diet_plant_weight_kg),
            "whole_diet_animal_weight_kg": _d(s.whole_diet_animal_weight_kg),
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
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=NOW,
    )
    if result.read_error or result.report.is_blocking:
        raise RuntimeError(f"{path.name}: ingestion failed — {result.report.errors}")
    return list(result.products)


def _process(
    csv_name: str,
    classification_map: dict[str, dict],
    *,
    fixture_root: Path = FIXTURE_ROOT,
    with_step2_ingredients: bool = False,
) -> None:
    csv_path = fixture_root / csv_name
    out_path = csv_path.with_suffix(".expected.json")
    products = _ingest(csv_path)
    classifications = _classify(products, classification_map)
    ingredients = _ingredients_for_step2(products) if with_step2_ingredients else None
    result = calculate_wwf_run(
        products,
        classifications,
        run_id=RUN_ID,
        reporting_period_label="FY 2024",
        versions=WWF_VERSIONS,
        ingredients_by_product=ingredients,
    )
    out_path.write_text(json.dumps(_serialise(result, products), indent=2) + "\n")
    print(f"Wrote {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    _process("wwf_tiny.csv", WWF_TINY)
    _process("wwf_dairy_equivalents.csv", WWF_DAIRY)
    _process("wwf_step1_composites.csv", WWF_STEP1)
    _process("wwf_excluded_foods.csv", WWF_EXCLUDED)
    _process(
        "wwf_step2_ingredients.csv",
        WWF_STEP2_CLASSIFICATIONS,
        with_step2_ingredients=True,
    )
