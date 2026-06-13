from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import ClassificationSource
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationRow,
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
    ProteinTrackerProductClassification,
)

_VER = dict(
    methodology_version="1.0.0",
    methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


class TestProteinTrackerGroup:
    def test_is_methodology_group_flags(self) -> None:
        assert ProteinTrackerGroup.PLANT_BASED_CORE.is_methodology_group
        assert ProteinTrackerGroup.COMPOSITE_PRODUCTS.is_methodology_group
        assert not ProteinTrackerGroup.OUT_OF_SCOPE.is_methodology_group
        assert not ProteinTrackerGroup.UNKNOWN.is_methodology_group

    def test_plant_vs_animal_side(self) -> None:
        assert ProteinTrackerGroup.PLANT_BASED_CORE.is_plant_side
        assert ProteinTrackerGroup.PLANT_BASED_NON_CORE.is_plant_side
        assert ProteinTrackerGroup.ANIMAL_CORE.is_animal_side
        assert not ProteinTrackerGroup.COMPOSITE_PRODUCTS.is_plant_side
        assert not ProteinTrackerGroup.COMPOSITE_PRODUCTS.is_animal_side


class TestProteinTrackerProductClassification:
    def test_deterministic_requires_rule_id_and_confidence_one(
        self, product_id: UUID, now: datetime
    ) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                # rule_id missing
                updated_at=now,
            )
        with pytest.raises(PydanticValidationError):
            # Deterministic confidence must be high (>= 0.9); 0.5 is too low.
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("0.5"),
                rule_id="pt.pulses.lentils",
                updated_at=now,
            )

    def test_deterministic_valid(self, product_id: UUID, now: datetime) -> None:
        c = ProteinTrackerProductClassification(
            product_id=product_id,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="pt.pulses.lentils",
            updated_at=now,
        )
        assert c.rule_id == "pt.pulses.lentils"

    def test_ai_requires_prompt_version_and_model(self, product_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
                source=ClassificationSource.AI,
                confidence=Decimal("0.85"),
                ai_prompt_version="classifier_v1",
                # ai_model missing
                updated_at=now,
            )

    def test_manual_review_requires_reviewer(self, product_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group=ProteinTrackerGroup.ANIMAL_CORE,
                source=ClassificationSource.MANUAL_REVIEW,
                confidence=Decimal("1"),
                updated_at=now,
            )

    def test_invalid_pt_group_string_rejected(self, product_id: UUID, now: datetime) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerProductClassification(
                product_id=product_id,
                pt_group="not_a_group",  # type: ignore[arg-type]
                source=ClassificationSource.DETERMINISTIC,
                confidence=Decimal("1"),
                rule_id="r",
                updated_at=now,
            )


class TestProteinTrackerCalculationRow:
    def test_in_scope_row(self, run_id: UUID, product_id: UUID) -> None:
        r = ProteinTrackerCalculationRow(
            run_id=run_id,
            product_id=product_id,
            in_scope=True,
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            volume_kg=Decimal("480"),
            protein_pct=Decimal("4.5"),
            protein_kg=Decimal("21.6"),
            used_per_product_split=False,
            **_VER,
        )
        assert r.in_scope is True

    def test_in_scope_must_match_group(self, run_id: UUID, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product_id,
                in_scope=True,
                pt_group=ProteinTrackerGroup.OUT_OF_SCOPE,
                volume_kg=Decimal("0"),
                protein_pct=Decimal("0"),
                protein_kg=Decimal("0"),
                used_per_product_split=False,
                **_VER,
            )

    def test_split_only_on_composites(self, run_id: UUID, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product_id,
                in_scope=True,
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                volume_kg=Decimal("100"),
                protein_pct=Decimal("10"),
                protein_kg=Decimal("10"),
                used_per_product_split=True,
                plant_protein_kg=Decimal("8"),
                animal_protein_kg=Decimal("2"),
                **_VER,
            )

    def test_split_required_when_used(self, run_id: UUID, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product_id,
                in_scope=True,
                pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
                volume_kg=Decimal("100"),
                protein_pct=Decimal("10"),
                protein_kg=Decimal("10"),
                used_per_product_split=True,
                # plant_protein_kg / animal_protein_kg missing
                **_VER,
            )

    def test_split_must_be_null_when_unused(self, run_id: UUID, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product_id,
                in_scope=True,
                pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
                volume_kg=Decimal("100"),
                protein_pct=Decimal("10"),
                protein_kg=Decimal("10"),
                used_per_product_split=False,
                plant_protein_kg=Decimal("8"),
                animal_protein_kg=Decimal("2"),
                **_VER,
            )

    def test_out_of_scope_must_have_zero_protein(self, run_id: UUID, product_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationRow(
                run_id=run_id,
                product_id=product_id,
                in_scope=False,
                pt_group=ProteinTrackerGroup.OUT_OF_SCOPE,
                volume_kg=Decimal("100"),
                protein_pct=Decimal("10"),
                protein_kg=Decimal("10"),
                used_per_product_split=False,
                **_VER,
            )


class TestProteinTrackerCalculationSummary:
    def _aggs(self) -> tuple[ProteinTrackerGroupAggregate, ...]:
        return (
            ProteinTrackerGroupAggregate(
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                volume_kg=Decimal("1303"),
                protein_kg=Decimal("283.28"),
                item_count=5,
            ),
            ProteinTrackerGroupAggregate(
                pt_group=ProteinTrackerGroup.ANIMAL_CORE,
                volume_kg=Decimal("8260"),
                protein_kg=Decimal("1546.25"),
                item_count=5,
            ),
        )

    def test_creates(self, run_id: UUID) -> None:
        s = ProteinTrackerCalculationSummary(
            run_id=run_id,
            reporting_period_label="FY 2024",
            per_group=self._aggs(),
            plant_protein_kg=Decimal("313.88"),
            animal_protein_kg=Decimal("1576.85"),
            total_in_scope_protein_kg=Decimal("1890.73"),
            plant_share_pct=Decimal("16.6068"),
            animal_share_pct=Decimal("83.3932"),
            rows_with_per_product_split=0,
            rows_protein_source_label=10,
            rows_protein_source_reference_db=2,
            out_of_scope_count=0,
            unknown_count=0,
            **_VER,
        )
        assert s.plant_share_pct == Decimal("16.6068")

    def test_null_shares_only_when_zero_total(self, run_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationSummary(
                run_id=run_id,
                reporting_period_label="FY 2024",
                per_group=self._aggs(),
                plant_protein_kg=Decimal("313.88"),
                animal_protein_kg=Decimal("1576.85"),
                total_in_scope_protein_kg=Decimal("1890.73"),
                plant_share_pct=None,
                animal_share_pct=None,
                rows_with_per_product_split=0,
                rows_protein_source_label=10,
                rows_protein_source_reference_db=2,
                out_of_scope_count=0,
                unknown_count=0,
                **_VER,
            )

    def test_share_nullness_must_match(self, run_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationSummary(
                run_id=run_id,
                reporting_period_label="FY 2024",
                per_group=self._aggs(),
                plant_protein_kg=Decimal("0"),
                animal_protein_kg=Decimal("0"),
                total_in_scope_protein_kg=Decimal("0"),
                plant_share_pct=Decimal("50"),
                animal_share_pct=None,
                rows_with_per_product_split=0,
                rows_protein_source_label=0,
                rows_protein_source_reference_db=0,
                out_of_scope_count=5,
                unknown_count=0,
                **_VER,
            )

    def test_per_group_must_be_unique(self, run_id: UUID) -> None:
        dup = (
            ProteinTrackerGroupAggregate(
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                volume_kg=Decimal("0"),
                protein_kg=Decimal("0"),
                item_count=0,
            ),
            ProteinTrackerGroupAggregate(
                pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
                volume_kg=Decimal("0"),
                protein_kg=Decimal("0"),
                item_count=0,
            ),
        )
        with pytest.raises(PydanticValidationError):
            ProteinTrackerCalculationSummary(
                run_id=run_id,
                reporting_period_label="FY 2024",
                per_group=dup,
                plant_protein_kg=Decimal("0"),
                animal_protein_kg=Decimal("0"),
                total_in_scope_protein_kg=Decimal("0"),
                plant_share_pct=None,
                animal_share_pct=None,
                rows_with_per_product_split=0,
                rows_protein_source_label=0,
                rows_protein_source_reference_db=0,
                out_of_scope_count=0,
                unknown_count=0,
                **_VER,
            )
