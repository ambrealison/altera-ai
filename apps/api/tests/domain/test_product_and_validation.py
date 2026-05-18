from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError as PydanticValidationError

from altera_api.domain.common import Methodology
from altera_api.domain.product import (
    NormalizedProduct,
    ProteinSource,
    PTProductFields,
    RawProduct,
    RetailChannel,
    WWFProductFields,
)
from altera_api.domain.validation import (
    ValidationError,
    ValidationReport,
    ValidationSeverity,
    ValidationWarning,
)


class TestRawProduct:
    def test_minimal_creates(self, upload_id: UUID) -> None:
        r = RawProduct(
            upload_id=upload_id,
            row_number=1,
            external_product_id="P-001",
            product_name="Red Lentil Soup",
        )
        assert r.brand is None

    def test_row_number_must_be_one_indexed(self, upload_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            RawProduct(
                upload_id=upload_id,
                row_number=0,
                external_product_id="P-001",
                product_name="x",
            )

    def test_rejects_invalid_language(self, upload_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            RawProduct(
                upload_id=upload_id,
                row_number=1,
                external_product_id="P",
                product_name="x",
                language="english",  # type: ignore[arg-type]
            )

    def test_rejects_invalid_country(self, upload_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            RawProduct(
                upload_id=upload_id,
                row_number=1,
                external_product_id="P",
                product_name="x",
                country="gb",  # type: ignore[arg-type]
            )

    def test_protein_pct_out_of_range(self, upload_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            RawProduct(
                upload_id=upload_id,
                row_number=1,
                external_product_id="P",
                product_name="x",
                protein_pct=Decimal("150"),
            )


class TestPTProductFields:
    def test_creates(self) -> None:
        pt = PTProductFields(
            items_purchased=Decimal("1000"),
            protein_pct=Decimal("22.0"),
            protein_source=ProteinSource.LABEL,
        )
        assert pt.protein_source is ProteinSource.LABEL

    def test_split_requires_both_or_neither(self) -> None:
        with pytest.raises(PydanticValidationError):
            PTProductFields(
                items_purchased=Decimal("100"),
                protein_pct=Decimal("10"),
                plant_protein_pct=Decimal("4"),
                # animal_protein_pct missing
            )

    def test_split_both_present_ok(self) -> None:
        pt = PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=Decimal("10"),
            plant_protein_pct=Decimal("4"),
            animal_protein_pct=Decimal("6"),
        )
        assert pt.plant_protein_pct == Decimal("4")


class TestWWFProductFields:
    def test_creates(self) -> None:
        wwf = WWFProductFields(
            items_sold=Decimal("3500"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=False,
        )
        assert wwf.retail_channel is RetailChannel.FRESH

    def test_invalid_retail_channel_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            WWFProductFields(
                items_sold=Decimal("1"),
                retail_channel="online",  # type: ignore[arg-type]
                is_own_brand=False,
            )


class TestNormalizedProduct:
    def _base(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> dict:
        return dict(
            id=product_id,
            upload_id=upload_id,
            project_id=project_id,
            organisation_id=org_id,
            row_number=1,
            external_product_id="P-001",
            product_name="Red Lentil Soup",
            weight_per_item_kg=Decimal("0.400"),
            created_at=now,
        )

    def test_pt_only_project_requires_pt_fields(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, upload_id, project_id, org_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.PROTEIN_TRACKER})
        with pytest.raises(PydanticValidationError):
            NormalizedProduct(**base)

    def test_pt_only_project_with_pt_fields_ok(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, upload_id, project_id, org_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.PROTEIN_TRACKER})
        base["pt_fields"] = PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=Decimal("4.5"),
        )
        p = NormalizedProduct(**base)
        assert p.pt_fields is not None

    def test_wwf_requires_is_own_brand(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, upload_id, project_id, org_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.WWF})
        base["wwf_fields"] = WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=True,
        )
        # is_own_brand not set on the NormalizedProduct itself
        with pytest.raises(PydanticValidationError):
            NormalizedProduct(**base)

    def test_wwf_full_creates(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, upload_id, project_id, org_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.WWF})
        base["is_own_brand"] = True
        base["wwf_fields"] = WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=True,
        )
        p = NormalizedProduct(**base)
        assert p.wwf_fields is not None

    def test_orphan_pt_fields_rejected(
        self, product_id: UUID, upload_id: UUID, project_id: UUID, org_id: UUID, now: datetime
    ) -> None:
        base = self._base(product_id, upload_id, project_id, org_id, now)
        base["methodologies_enabled"] = frozenset({Methodology.WWF})
        base["is_own_brand"] = False
        base["wwf_fields"] = WWFProductFields(
            items_sold=Decimal("100"),
            retail_channel=RetailChannel.FRESH,
            is_own_brand=False,
        )
        base["pt_fields"] = PTProductFields(
            items_purchased=Decimal("100"),
            protein_pct=Decimal("4.5"),
        )
        with pytest.raises(PydanticValidationError):
            NormalizedProduct(**base)


class TestValidationReport:
    def test_aggregates_counts(self, upload_id: UUID) -> None:
        errors = (
            ValidationError(row_number=1, code="missing_weight", message="missing"),
            ValidationError(row_number=3, code="bad_protein", message="bad"),
            ValidationError(row_number=3, code="bad_unit", message="bad"),
        )
        warnings = (ValidationWarning(row_number=2, code="rounded", message="rounded"),)
        rep = ValidationReport(upload_id=upload_id, total_rows=10, errors=errors, warnings=warnings)
        assert rep.error_count == 3
        assert rep.warning_count == 1
        assert rep.rows_with_errors == 2
        assert rep.rows_with_warnings == 1
        assert rep.is_blocking is True

    def test_empty_is_non_blocking(self, upload_id: UUID) -> None:
        rep = ValidationReport(upload_id=upload_id, total_rows=10)
        assert rep.is_blocking is False
        assert rep.error_count == 0

    def test_rejects_row_numbers_above_total(self, upload_id: UUID) -> None:
        with pytest.raises(PydanticValidationError):
            ValidationReport(
                upload_id=upload_id,
                total_rows=5,
                errors=(
                    ValidationError(row_number=10, code="x", message="row 10 in a 5-row file"),
                ),
            )

    def test_error_severity_locked_to_error(self) -> None:
        with pytest.raises(PydanticValidationError):
            ValidationError(
                row_number=1, code="x", message="x", severity=ValidationSeverity.WARNING
            )

    def test_warning_severity_locked_to_warning(self) -> None:
        with pytest.raises(PydanticValidationError):
            ValidationWarning(
                row_number=1, code="x", message="x", severity=ValidationSeverity.ERROR
            )
