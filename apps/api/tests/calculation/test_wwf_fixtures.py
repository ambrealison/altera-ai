"""End-to-end reproducibility regression on the Phase 2 WWF fixtures.

For each `tests/fixtures/wwf/*.csv` we:

1. Run the ingestion pipeline to produce ``NormalizedProduct``s.
2. Build the classification map from the fixture's
   ``*.expected.json`` (carrying the canonical ``wwf_food_group`` and
   subgroup fields per row).
3. For the Step-2 fixture, build the
   ``ingredients_by_product`` mapping from
   ``wwf_step2_ingredients.json``.
4. Run :func:`calculate_wwf_run`.
5. Assert per-row and summary figures match the expected JSON.

Mirrors ``test_protein_tracker_fixtures.py``. This is the byte-identical
reproducibility check described in
``docs/development/testing.md``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.calculation import WWFRunVersions, calculate_wwf_run
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.wwf import (
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
from altera_api.ingestion import ingest_csv_bytes

_NOW = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
_UPLOAD_ID = UUID("00000000-0000-0000-0000-000000000003")
_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000002")
_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
_RUN_ID = UUID("00000000-0000-0000-0000-000000000def")

_VERSIONS = WWFRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)

_FG1_SUBGROUP = {member.value: member for member in WWFFG1Subgroup}
_FG2_SUBGROUP = {member.value: member for member in WWFFG2Subgroup}
_FG3_SUBGROUP = {member.value: member for member in WWFFG3Subgroup}
_FG5_GRAIN = {member.value: member for member in WWFFG5GrainKind}
_FG7_SNACK = {member.value: member for member in WWFFG7SnackKind}
_STEP1_BUCKET = {member.value: member for member in WWFCompositeStep1Bucket}


@pytest.fixture
def fixture_root() -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "wwf"


def _ingest(csv_path: Path) -> list[NormalizedProduct]:
    result = ingest_csv_bytes(
        csv_path.read_bytes(),
        upload_id=_UPLOAD_ID,
        project_id=_PROJECT_ID,
        organisation_id=_ORG_ID,
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=_NOW,
    )
    assert result.read_error is None
    assert not result.report.is_blocking, result.report.errors
    return list(result.products)


def _classification_from_expected_row(
    product_id: UUID, expected_row: dict
) -> WWFProductClassification:
    """Rebuild the classification used to generate the expected row."""
    food_group = WWFFoodGroup(expected_row["wwf_food_group"])
    is_composite = expected_row["wwf_is_composite"]
    bucket_value = expected_row["wwf_composite_step1_bucket"]
    composite_bucket = _STEP1_BUCKET[bucket_value] if bucket_value else None

    subgroup_label = expected_row["wwf_subgroup_label"]
    fg1 = _FG1_SUBGROUP.get(subgroup_label) if food_group is WWFFoodGroup.FG1 else None
    fg2 = _FG2_SUBGROUP.get(subgroup_label) if food_group is WWFFoodGroup.FG2 else None
    fg3 = _FG3_SUBGROUP.get(subgroup_label) if food_group is WWFFoodGroup.FG3 else None
    fg5 = _FG5_GRAIN.get(subgroup_label) if food_group is WWFFoodGroup.FG5 else None
    fg7 = _FG7_SNACK.get(subgroup_label) if food_group is WWFFoodGroup.FG7 else None

    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=food_group,
        wwf_is_composite=is_composite,
        fg1_subgroup=fg1,
        fg2_subgroup=fg2,
        fg3_subgroup=fg3,
        fg5_grain_kind=fg5,
        fg7_snack_kind=fg7,
        composite_step1_bucket=composite_bucket,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="wwf.fixture.rule",
        updated_at=_NOW,
    )


def _classifications_from_expected(
    products: list[NormalizedProduct], expected: dict
) -> dict[UUID, WWFProductClassification]:
    by_external = {p.external_product_id: p for p in products}
    return {
        by_external[row["external_product_id"]].id: _classification_from_expected_row(
            by_external[row["external_product_id"]].id, row
        )
        for row in expected["rows"]
    }


def _step2_ingredients(
    products: list[NormalizedProduct], step2_json_path: Path
) -> dict[UUID, tuple[WWFCompositeIngredient, ...]]:
    raw = json.loads(step2_json_path.read_text())
    by_external = {p.external_product_id: p.id for p in products}
    out: dict[UUID, tuple[WWFCompositeIngredient, ...]] = {}
    uid = 1
    for ext, entry in raw.items():
        if ext not in by_external:
            continue
        parent = by_external[ext]
        instances: list[WWFCompositeIngredient] = []
        for ing in entry["ingredients"]:
            fg = WWFFoodGroup(ing["food_group"])
            sub = ing.get("subgroup")
            fg1 = _FG1_SUBGROUP.get(sub) if fg is WWFFoodGroup.FG1 else None
            fg2 = _FG2_SUBGROUP.get(sub) if fg is WWFFoodGroup.FG2 else None
            instances.append(
                WWFCompositeIngredient(
                    id=UUID(f"00000000-0000-0000-0000-{uid:012d}"),
                    parent_product_id=parent,
                    food_group=fg,
                    fg1_subgroup=fg1,
                    fg2_subgroup=fg2,
                    ingredient_weight_kg_per_item=Decimal(
                        str(ing["ingredient_weight_kg_per_item"])
                    ),
                )
            )
            uid += 1
        out[parent] = tuple(instances)
    return out


@pytest.mark.parametrize(
    "fixture_name",
    [
        "wwf_tiny",
        "wwf_dairy_equivalents",
        "wwf_step1_composites",
        "wwf_excluded_foods",
    ],
)
def test_fixture_round_trip(fixture_root: Path, fixture_name: str) -> None:
    csv_path = fixture_root / f"{fixture_name}.csv"
    expected_path = fixture_root / f"{fixture_name}.expected.json"
    expected = json.loads(expected_path.read_text())

    products = _ingest(csv_path)
    classifications = _classifications_from_expected(products, expected)
    result = calculate_wwf_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label=expected["reporting_period_label"],
        versions=_VERSIONS,
    )
    _assert_matches(result, products, expected)


def test_step2_fixture_round_trip(fixture_root: Path) -> None:
    csv_path = fixture_root / "wwf_step2_ingredients.csv"
    expected_path = fixture_root / "wwf_step2_ingredients.expected.json"
    step2_json_path = fixture_root / "wwf_step2_ingredients.json"
    expected = json.loads(expected_path.read_text())

    products = _ingest(csv_path)
    classifications = _classifications_from_expected(products, expected)
    ingredients = _step2_ingredients(products, step2_json_path)
    result = calculate_wwf_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label=expected["reporting_period_label"],
        versions=_VERSIONS,
        ingredients_by_product=ingredients,
    )
    _assert_matches(result, products, expected)


def test_reproducibility_byte_identical(fixture_root: Path) -> None:
    csv_path = fixture_root / "wwf_tiny.csv"
    expected = json.loads((fixture_root / "wwf_tiny.expected.json").read_text())
    products = _ingest(csv_path)
    classifications = _classifications_from_expected(products, expected)

    a = calculate_wwf_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
    )
    b = calculate_wwf_run(
        products,
        classifications,
        run_id=_RUN_ID,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
    )

    assert tuple(r.model_dump() for r in a.rows) == tuple(r.model_dump() for r in b.rows)
    assert a.summary.model_dump() == b.summary.model_dump()


def _assert_matches(result, products: list[NormalizedProduct], expected: dict) -> None:
    by_external = {p.id: p.external_product_id for p in products}
    rows_by_external = {by_external[r.product_id]: r for r in result.rows}

    for expected_row in expected["rows"]:
        ext = expected_row["external_product_id"]
        actual = rows_by_external[ext]
        assert actual.wwf_food_group.value == expected_row["wwf_food_group"], ext
        assert actual.wwf_is_composite == expected_row["wwf_is_composite"], ext
        assert f"{actual.weight_kg:.8f}" == expected_row["weight_kg"], ext
        if expected_row["weight_kg_dairy_equiv"] is None:
            assert actual.weight_kg_dairy_equiv is None, ext
        else:
            assert (
                f"{actual.weight_kg_dairy_equiv:.8f}"
                == expected_row["weight_kg_dairy_equiv"]
            ), ext

    actual_groups = {a.food_group.value: a for a in result.summary.per_food_group}
    for expected_group in expected["per_food_group"]:
        fg = expected_group["food_group"]
        actual_group = actual_groups[fg]
        assert f"{actual_group.weight_kg:.8f}" == expected_group["weight_kg"], fg
        if expected_group["weight_kg_dairy_equiv"] is None:
            assert actual_group.weight_kg_dairy_equiv is None, fg
        else:
            assert (
                f"{actual_group.weight_kg_dairy_equiv:.8f}"
                == expected_group["weight_kg_dairy_equiv"]
            ), fg
        assert f"{actual_group.share_pct:.8f}" == expected_group["share_pct"], fg
        if expected_group["phd_reference_share_pct"] is None:
            assert actual_group.phd_reference_share_pct is None, fg
        else:
            assert (
                f"{actual_group.phd_reference_share_pct:.8f}"
                == expected_group["phd_reference_share_pct"]
            ), fg

    s = result.summary
    es = expected["summary"]
    assert f"{s.total_sales_weight_in_scope_kg:.8f}" == es["total_sales_weight_in_scope_kg"]
    assert f"{s.composites_total_weight_kg:.8f}" == es["composites_total_weight_kg"]
    assert f"{s.composites_meat_based_kg:.8f}" == es["composites_meat_based_kg"]
    assert f"{s.composites_seafood_based_kg:.8f}" == es["composites_seafood_based_kg"]
    assert f"{s.composites_vegetarian_kg:.8f}" == es["composites_vegetarian_kg"]
    assert f"{s.composites_vegan_kg:.8f}" == es["composites_vegan_kg"]
    assert f"{s.whole_diet_plant_weight_kg:.8f}" == es["whole_diet_plant_weight_kg"]
    assert f"{s.whole_diet_animal_weight_kg:.8f}" == es["whole_diet_animal_weight_kg"]
    assert s.out_of_scope_count == es["out_of_scope_count"]
    assert s.unknown_count == es["unknown_count"]
