"""End-to-end WWF export tests against the Phase 2 fixtures."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from altera_api.calculation import WWFRunVersions, calculate_wwf_run
from altera_api.domain.common import ClassificationSource, Methodology
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
from altera_api.exports import (
    ExportClassificationMeta,
    ExportProductMaster,
    RunMetadata,
    WWFExportContext,
    render_wwf_csv,
    render_wwf_json,
    render_wwf_markdown,
)
from altera_api.ingestion import ingest_csv_bytes

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


def _classification_from_expected_row(
    product_id: UUID, expected_row: dict, now: datetime
) -> WWFProductClassification:
    food_group = WWFFoodGroup(expected_row["wwf_food_group"])
    sub = expected_row["wwf_subgroup_label"]
    bucket_value = expected_row["wwf_composite_step1_bucket"]
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=food_group,
        wwf_is_composite=expected_row["wwf_is_composite"],
        fg1_subgroup=_FG1_SUBGROUP.get(sub) if food_group is WWFFoodGroup.FG1 else None,
        fg2_subgroup=_FG2_SUBGROUP.get(sub) if food_group is WWFFoodGroup.FG2 else None,
        fg3_subgroup=_FG3_SUBGROUP.get(sub) if food_group is WWFFoodGroup.FG3 else None,
        fg5_grain_kind=_FG5_GRAIN.get(sub) if food_group is WWFFoodGroup.FG5 else None,
        fg7_snack_kind=_FG7_SNACK.get(sub) if food_group is WWFFoodGroup.FG7 else None,
        composite_step1_bucket=_STEP1_BUCKET[bucket_value] if bucket_value else None,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="wwf.fixture.rule",
        updated_at=now,
    )


def _build_context(
    fixture_root: Path,
    fixture_name: str,
    *,
    run_id: UUID,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
    with_step2_ingredients: bool = False,
) -> WWFExportContext:
    csv_path = fixture_root / "wwf" / f"{fixture_name}.csv"
    expected = json.loads(
        (fixture_root / "wwf" / f"{fixture_name}.expected.json").read_text()
    )

    ingest = ingest_csv_bytes(
        csv_path.read_bytes(),
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        methodologies_enabled=frozenset({Methodology.WWF}),
        now=now,
    )
    assert ingest.read_error is None and not ingest.report.is_blocking
    products = list(ingest.products)
    by_external = {p.external_product_id: p for p in products}
    classifications = {
        by_external[row["external_product_id"]].id: _classification_from_expected_row(
            by_external[row["external_product_id"]].id, row, now
        )
        for row in expected["rows"]
    }

    ingredients = None
    if with_step2_ingredients:
        raw = json.loads(
            (fixture_root / "wwf" / "wwf_step2_ingredients.json").read_text()
        )
        ingredients = {}
        uid = 1
        for ext, entry in raw.items():
            if ext not in by_external:
                continue
            parent = by_external[ext].id
            instances: list[WWFCompositeIngredient] = []
            for ing in entry["ingredients"]:
                fg = WWFFoodGroup(ing["food_group"])
                sub = ing.get("subgroup")
                instances.append(
                    WWFCompositeIngredient(
                        id=UUID(f"00000000-0000-0000-0000-{uid:012d}"),
                        parent_product_id=parent,
                        food_group=fg,
                        fg1_subgroup=(
                            _FG1_SUBGROUP.get(sub) if fg is WWFFoodGroup.FG1 else None
                        ),
                        fg2_subgroup=(
                            _FG2_SUBGROUP.get(sub) if fg is WWFFoodGroup.FG2 else None
                        ),
                        ingredient_weight_kg_per_item=Decimal(
                            str(ing["ingredient_weight_kg_per_item"])
                        ),
                    )
                )
                uid += 1
            ingredients[parent] = tuple(instances)

    result = calculate_wwf_run(
        products,
        classifications,
        run_id=run_id,
        reporting_period_label="FY 2024",
        versions=_VERSIONS,
        ingredients_by_product=ingredients,
    )

    return WWFExportContext(
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
                is_own_brand=p.is_own_brand,
                retail_channel=p.wwf_fields.retail_channel if p.wwf_fields else None,
            )
            for p in products
        },
        classifications={
            p.id: ExportClassificationMeta(
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                rule_id="wwf.fixture.rule",
            )
            for p in products
        },
        items_sold={p.id: p.wwf_fields.items_sold for p in products if p.wwf_fields},
        weights_per_item={p.id: p.weight_per_item_kg for p in products},
        ingredients_by_product=ingredients,
    )


class TestWWFCSV:
    @pytest.mark.parametrize(
        "fixture_name",
        ["wwf_tiny", "wwf_dairy_equivalents", "wwf_step1_composites"],
    )
    def test_csv_per_row_matches_expected(
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
        data = render_wwf_csv(ctx)
        assert data.startswith(b"\xef\xbb\xbf")
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        rows = list(reader)
        expected = json.loads(
            (fixture_root / "wwf" / f"{fixture_name}.expected.json").read_text()
        )
        assert len(rows) == len(expected["rows"])
        by_external = {r["external_product_id"]: r for r in rows}
        for expected_row in expected["rows"]:
            ext = expected_row["external_product_id"]
            assert by_external[ext]["weight_kg"] == expected_row["weight_kg"]
            if expected_row["weight_kg_dairy_equiv"]:
                assert (
                    by_external[ext]["weight_kg_dairy_equiv"]
                    == expected_row["weight_kg_dairy_equiv"]
                )
            else:
                assert by_external[ext]["weight_kg_dairy_equiv"] == ""

    def test_csv_step2_ingredients_inline(
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
            "wwf_step2_ingredients",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
            with_step2_ingredients=True,
        )
        data = render_wwf_csv(ctx)
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        by_external = {r["external_product_id"]: r for r in reader}
        # P-VL-001 has 4 ingredients; column carries them as a JSON string.
        payload = json.loads(by_external["P-VL-001"]["wwf_step2_ingredient_weights_json"])
        assert len(payload) == 4
        # Branded P-VL-005 — Step 2 ignored, column empty.
        assert by_external["P-VL-005"]["wwf_step2_ingredient_weights_json"] == ""

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
            "wwf_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        data = render_wwf_csv(ctx).decode("utf-8-sig")
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
            assert col not in header


class TestWWFJSON:
    def test_breakdowns_include_food_groups_and_composites(
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
            "wwf_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        doc = json.loads(render_wwf_json(ctx))
        assert doc["run"]["methodology"] == "wwf"
        assert "FG1" in doc["breakdowns"]["by_food_group"]
        # PHD reference appears
        assert doc["breakdowns"]["by_food_group"]["FG1"]["phd_share_pct"] == "16"
        # Composite Step 1 bucket
        assert doc["breakdowns"]["composites_step1"]["meat_based"] == "1280.00000000"
        # Whole-diet context line
        assert "whole_diet_plant_vs_animal_context" in doc["breakdowns"]
        # Composite share of sales — fraction of in-scope weight
        assert doc["summary"]["composite_share_of_sales_pct"]

    def test_step2_ingredient_block_in_rows(
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
            "wwf_step2_ingredients",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
            with_step2_ingredients=True,
        )
        doc = json.loads(render_wwf_json(ctx))
        rows_by_ext = {r["external_product_id"]: r for r in doc["rows"]}
        # Own-brand with Step 2 data → ingredient block populated.
        assert rows_by_ext["P-VL-001"]["step2_ingredient_weights"] is not None
        assert len(rows_by_ext["P-VL-001"]["step2_ingredient_weights"]) == 4
        # Branded → ingredients ignored even if supplied.
        assert rows_by_ext["P-VL-005"]["step2_ingredient_weights"] is None


class TestWWFMarkdown:
    def test_markdown_contains_food_group_table_and_phd(
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
            "wwf_tiny",
            run_id=run_id,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        md = render_wwf_markdown(ctx)
        assert "# WWF Planet-Based Diets report" in md
        assert "PHD reference %" in md
        assert "FG1 — Protein sources" in md
        assert "FG7" in md
        assert "Composite products (Step 1)" in md
        # Whole-diet context
        assert "Whole-diet plant vs animal" in md
        # Methodology footnote
        assert "WWF" in md
