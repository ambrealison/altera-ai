"""Hand-verifiable PT calculation tests.

Every input below is small enough that the expected output can be
re-derived in two lines on a piece of paper. These tests pin the
formulas from docs/calculation/protein-tracker-calculation.md.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from altera_api.calculation import PTRunVersions, calculate_pt_run
from altera_api.domain.common import (
    ClassificationSource,
    Methodology,
)
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
)
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)


def _ids(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


def _product(
    *,
    product_uid: int,
    weight_per_item_kg: str,
    items_purchased: str,
    protein_pct: str,
    protein_source: ProteinSource = ProteinSource.REFERENCE_DB,
    plant_protein_pct: str | None = None,
    animal_protein_pct: str | None = None,
    upload_id: UUID,
    project_id: UUID,
    org_id: UUID,
    now: datetime,
) -> NormalizedProduct:
    return NormalizedProduct(
        id=_ids(product_uid),
        upload_id=upload_id,
        project_id=project_id,
        organisation_id=org_id,
        row_number=product_uid,
        external_product_id=f"P-{product_uid:03d}",
        product_name=f"Product {product_uid}",
        weight_per_item_kg=Decimal(weight_per_item_kg),
        methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
        pt_fields=PTProductFields(
            items_purchased=Decimal(items_purchased),
            protein_pct=Decimal(protein_pct),
            protein_source=protein_source,
            plant_protein_pct=Decimal(plant_protein_pct) if plant_protein_pct else None,
            animal_protein_pct=Decimal(animal_protein_pct) if animal_protein_pct else None,
        ),
        created_at=now,
    )


def _classification(
    product_id: UUID, group: ProteinTrackerGroup, now: datetime
) -> ProteinTrackerProductClassification:
    return ProteinTrackerProductClassification(
        product_id=product_id,
        pt_group=group,
        source=ClassificationSource.DETERMINISTIC,
        confidence=Decimal("1"),
        rule_id="pt.test.rule",
        updated_at=now,
    )


class TestSimpleHeadline:
    def test_single_plant_core_product(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        product = _product(
            product_uid=1,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.PLANT_BASED_CORE, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        # volume = 1 × 10 = 10kg; protein = 10 × 20/100 = 2kg.
        assert result.rows[0].volume_kg == Decimal("10.00000000")
        assert result.rows[0].protein_kg == Decimal("2.00000000")
        s = result.summary
        assert s.plant_protein_kg == Decimal("2.00000000")
        assert s.animal_protein_kg == Decimal("0.00000000")
        assert s.total_in_scope_protein_kg == Decimal("2.00000000")
        assert s.plant_share_pct == Decimal("100.00000000")
        assert s.animal_share_pct == Decimal("0.00000000")

    def test_pure_50_50_for_composite_only(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        # One composite, no split data — pool is split 50/50.
        product = _product(
            product_uid=2,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.COMPOSITE_PRODUCTS, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        s = result.summary
        assert s.plant_protein_kg == Decimal("1.00000000")
        assert s.animal_protein_kg == Decimal("1.00000000")
        assert s.plant_share_pct == Decimal("50.00000000")
        assert s.animal_share_pct == Decimal("50.00000000")
        assert s.rows_with_per_product_split == 0


class TestPerProductSplit:
    def test_split_removes_row_from_pool(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        # One composite with a 40/60 split → contributes directly, NOT to pool.
        product = _product(
            product_uid=3,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            plant_protein_pct="8",
            animal_protein_pct="12",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.COMPOSITE_PRODUCTS, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        row = result.rows[0]
        assert row.used_per_product_split is True
        # plant_kg = 10 × 8/100 = 0.8; animal_kg = 10 × 12/100 = 1.2
        assert row.plant_protein_kg == Decimal("0.80000000")
        assert row.animal_protein_kg == Decimal("1.20000000")
        s = result.summary
        # No 50/50 pool applied — the split row covers it directly.
        assert s.plant_protein_kg == Decimal("0.80000000")
        assert s.animal_protein_kg == Decimal("1.20000000")
        assert s.plant_share_pct == Decimal("40.00000000")
        assert s.animal_share_pct == Decimal("60.00000000")
        assert s.rows_with_per_product_split == 1

    def test_invalid_split_sum_falls_back_to_pool(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        # Split sum (5+10=15) does NOT match protein_pct (20) → reject split,
        # treat as ordinary composite, 50/50 applies.
        product = _product(
            product_uid=4,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            plant_protein_pct="5",
            animal_protein_pct="10",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.COMPOSITE_PRODUCTS, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        assert result.rows[0].used_per_product_split is False
        assert result.summary.rows_with_per_product_split == 0
        assert result.summary.plant_protein_kg == Decimal("1.00000000")
        assert result.summary.animal_protein_kg == Decimal("1.00000000")

    def test_split_can_be_disabled_globally(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        product = _product(
            product_uid=5,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            plant_protein_pct="8",
            animal_protein_pct="12",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.COMPOSITE_PRODUCTS, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
            enable_per_product_split=False,
        )
        assert result.rows[0].used_per_product_split is False
        # 50/50 applies even though row had a valid split — flag overrides.
        assert result.summary.plant_protein_kg == Decimal("1.00000000")
        assert result.summary.animal_protein_kg == Decimal("1.00000000")


class TestOutOfScopeAndUnknown:
    def test_out_of_scope_excluded_from_totals(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        in_scope = _product(
            product_uid=10,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        out_of_scope = _product(
            product_uid=11,
            weight_per_item_kg="1",
            items_purchased="100",
            protein_pct="50",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            in_scope.id: _classification(in_scope.id, ProteinTrackerGroup.PLANT_BASED_CORE, now),
            out_of_scope.id: _classification(
                out_of_scope.id, ProteinTrackerGroup.OUT_OF_SCOPE, now
            ),
        }
        result = calculate_pt_run(
            [in_scope, out_of_scope],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        # 50kg protein in the out-of-scope row contributes nothing to totals.
        assert result.summary.plant_protein_kg == Decimal("2.00000000")
        assert result.summary.total_in_scope_protein_kg == Decimal("2.00000000")
        assert result.summary.out_of_scope_count == 1
        # The row exists in result.rows with protein_kg=0.
        oos_row = next(r for r in result.rows if r.pt_group is ProteinTrackerGroup.OUT_OF_SCOPE)
        assert oos_row.in_scope is False
        assert oos_row.protein_kg == Decimal("0.00000000")

    def test_unknown_count_separate_from_out_of_scope(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        unknown = _product(
            product_uid=20,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {unknown.id: _classification(unknown.id, ProteinTrackerGroup.UNKNOWN, now)}
        result = calculate_pt_run(
            [unknown],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        assert result.summary.unknown_count == 1
        assert result.summary.out_of_scope_count == 0
        # No in-scope protein → shares are null.
        assert result.summary.total_in_scope_protein_kg == Decimal("0.00000000")
        assert result.summary.plant_share_pct is None
        assert result.summary.animal_share_pct is None


class TestDataQualityCounters:
    def test_label_vs_reference_db_counts(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        label = _product(
            product_uid=30,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            protein_source=ProteinSource.LABEL,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        ref = _product(
            product_uid=31,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            protein_source=ProteinSource.REFERENCE_DB,
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {
            label.id: _classification(label.id, ProteinTrackerGroup.PLANT_BASED_CORE, now),
            ref.id: _classification(ref.id, ProteinTrackerGroup.PLANT_BASED_CORE, now),
        }
        result = calculate_pt_run(
            [label, ref],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        assert result.summary.rows_protein_source_label == 1
        assert result.summary.rows_protein_source_reference_db == 1


class TestMissingClassificationRaises:
    def test_raises_when_classification_missing(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        import pytest

        product = _product(
            product_uid=40,
            weight_per_item_kg="1",
            items_purchased="1",
            protein_pct="1",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        with pytest.raises(ValueError, match="no PT classification"):
            calculate_pt_run(
                [product],
                {},  # no classification for the product
                run_id=run_id,
                reporting_period_label="FY 2024",
                versions=pt_versions,
            )


class TestPerGroupAggregate:
    def test_all_four_groups_present_even_when_empty(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        run_id: UUID,
        pt_versions: PTRunVersions,
    ) -> None:
        product = _product(
            product_uid=50,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.PLANT_BASED_CORE, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=run_id,
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        groups = {a.pt_group for a in result.summary.per_group}
        assert groups == {
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        }
        # Empty groups have zero protein and zero items.
        empty = next(
            a for a in result.summary.per_group if a.pt_group is ProteinTrackerGroup.ANIMAL_CORE
        )
        assert empty.protein_kg == Decimal("0.00000000")
        assert empty.item_count == 0


class TestVersionStamping:
    def test_versions_propagate_to_rows_and_summary(
        self,
        upload_id: UUID,
        project_id: UUID,
        org_id: UUID,
        now: datetime,
        pt_versions: PTRunVersions,
    ) -> None:
        product = _product(
            product_uid=60,
            weight_per_item_kg="1",
            items_purchased="10",
            protein_pct="20",
            upload_id=upload_id,
            project_id=project_id,
            org_id=org_id,
            now=now,
        )
        cls = {product.id: _classification(product.id, ProteinTrackerGroup.PLANT_BASED_CORE, now)}
        result = calculate_pt_run(
            [product],
            cls,
            run_id=uuid4(),
            reporting_period_label="FY 2024",
            versions=pt_versions,
        )
        row = result.rows[0]
        assert row.methodology_version == pt_versions.methodology_version
        assert row.methodology_source_edition == pt_versions.methodology_source_edition
        assert row.taxonomy_version == pt_versions.taxonomy_version
        assert row.rules_version == pt_versions.rules_version
        s = result.summary
        assert s.methodology is Methodology.PROTEIN_TRACKER
        assert s.methodology_version == pt_versions.methodology_version
