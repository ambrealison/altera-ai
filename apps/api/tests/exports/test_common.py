from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from altera_api.domain.common import Methodology
from altera_api.exports import export_filename, format_decimal


class TestFormatDecimal:
    def test_none_renders_empty(self) -> None:
        assert format_decimal(None) == ""

    def test_zero_uses_fixed_form_not_e_notation(self) -> None:
        # Decimal("0").quantize(Decimal("0.00000001")) → "0E-8" in repr,
        # which is ugly and unfriendly. format_decimal must normalise.
        v = Decimal("0").quantize(Decimal("0.00000001"))
        assert format_decimal(v) == "0.00000000"

    def test_full_precision_preserved(self) -> None:
        v = Decimal("12345.6789012345")
        assert format_decimal(v) == "12345.6789012345"

    def test_8dp_value(self) -> None:
        v = Decimal("39.07291729")
        assert format_decimal(v) == "39.07291729"


class TestFilename:
    def test_pt_csv(self) -> None:
        name = export_filename(
            project_slug="acme-foods",
            methodology=Methodology.PROTEIN_TRACKER,
            run_id=UUID("12345678-1234-1234-1234-123456789abc"),
            fmt="csv",
            today=date(2026, 5, 15),
        )
        assert name == "altera_acme-foods_protein_tracker_12345678_20260515.csv"

    def test_wwf_markdown(self) -> None:
        name = export_filename(
            project_slug="retailer-x",
            methodology=Methodology.WWF,
            run_id=UUID("abcdef00-0000-0000-0000-000000000000"),
            fmt="md",
            today=date(2026, 1, 1),
        )
        assert name == "altera_retailer-x_wwf_abcdef00_20260101.md"
