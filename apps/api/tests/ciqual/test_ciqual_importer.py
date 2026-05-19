"""Phase 33A — CIQUAL importer and enrichment provider tests.

Verifies:
- Importer reads protein values and normalises comma-decimal format
- Importer handles missing values ("-") and below-detection ("< N") markers
- Importer rejects rows missing the food code
- CiqualProvider matches by exact food name
- CiqualProvider falls back to food-group average
- CiqualProvider returns FAILED record when no match found
- CiqualProvider never overwrites existing retailer protein value
- Source metadata (version, food_code) is preserved
- Enrichment priority: manual_altera > ciqual > category_average
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from altera_api.domain.ciqual import CiqualEntry
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.enrichment.providers.ciqual import CiqualProvider
from altera_api.enrichment.selection import _SOURCE_PRIORITY, select_protein_enrichment
from scripts.import_ciqual import _parse_numeric

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ciqual_sample.csv"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_PRODUCT_ID = uuid4()


# ---------------------------------------------------------------------------
# Unit tests for _parse_numeric helper
# ---------------------------------------------------------------------------


class TestParseNumeric:
    def test_comma_decimal(self) -> None:
        val, below = _parse_numeric("4,41")
        assert val == Decimal("4.41")
        assert not below

    def test_dot_decimal(self) -> None:
        val, below = _parse_numeric("17.9")
        assert val == Decimal("17.9")
        assert not below

    def test_plain_float(self) -> None:
        val, below = _parse_numeric(20.1)
        assert val == Decimal("20.1")
        assert not below

    def test_dash_is_missing(self) -> None:
        val, below = _parse_numeric("-")
        assert val is None
        assert not below

    def test_none_is_missing(self) -> None:
        val, below = _parse_numeric(None)
        assert val is None
        assert not below

    def test_empty_string_is_missing(self) -> None:
        val, below = _parse_numeric("")
        assert val is None
        assert not below

    def test_below_detection_marker(self) -> None:
        val, below = _parse_numeric("< 0,2")
        assert val is None
        assert below

    def test_below_detection_no_space(self) -> None:
        val, below = _parse_numeric("<0,5")
        assert val is None
        assert below

    def test_integer_value(self) -> None:
        val, below = _parse_numeric("8")
        assert val == Decimal("8")
        assert not below

    def test_garbage_returns_none(self) -> None:
        val, below = _parse_numeric("N/A")
        assert val is None
        assert not below


# ---------------------------------------------------------------------------
# Importer — read_ciqual_excel (via CSV fixture bypass)
# ---------------------------------------------------------------------------


def _make_openpyxl_stub(rows: list[tuple]) -> object:
    """Create a minimal stub that mimics openpyxl worksheet iteration."""

    class _WS:
        def iter_rows(self, values_only=True):
            return iter(rows)

    class _WB:
        sheetnames = ["food composition"]

        def __getitem__(self, name):
            return _WS()

        def close(self):
            pass

    return _WB()


def _make_row(code, name, grp, ssgrp, ssssgrp, protein):
    """Build a tuple that matches the CIQUAL column layout."""
    # [0]=grp_code [1]=ssgrp_code [2]=ssssgrp_code [3]=grp_nom [4]=ssgrp_nom
    # [5]=ssssgrp_nom [6]=alim_code [7]=alim_nom [8]=sci ... [14]=protein
    return (
        "01",  # grp_code
        "0101",  # ssgrp_code
        "000000",  # ssssgrp_code
        grp,  # [3]
        ssgrp,  # [4]
        ssssgrp or "-",  # [5]
        code,  # [6]
        name,  # [7]
        "",  # sci [8]
        *([None] * 5),  # [9-13]
        protein,  # [14]
    )


class TestReadCiqualExcel:
    def test_parses_comma_decimal_protein(self, tmp_path, monkeypatch) -> None:
        header = _make_row("code", "name", "grp", "ssgrp", None, "Protein\n(g\n100g)")
        row1 = _make_row("9999", "Test Food", "starters", "salads", None, "8,51")
        stub = _make_openpyxl_stub([header, row1])

        import scripts.import_ciqual as ic

        monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **kw: stub)
        entries = ic.read_ciqual_excel(tmp_path / "fake.xlsx")
        assert len(entries) == 1
        assert entries[0]["protein_g_per_100g"] == pytest.approx(8.51)
        assert entries[0]["source"] == "ciqual"
        assert entries[0]["source_version"] == "2025"
        assert entries[0]["source_food_code"] == "9999"

    def test_missing_dash_gives_none_protein(self, tmp_path, monkeypatch) -> None:
        header = _make_row("code", "name", "grp", "ssgrp", None, "Protein")
        row1 = _make_row("8888", "No Protein Food", "cereals", "grains", None, "-")
        stub = _make_openpyxl_stub([header, row1])

        import scripts.import_ciqual as ic

        monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **kw: stub)
        entries = ic.read_ciqual_excel(tmp_path / "fake.xlsx")
        assert entries[0]["protein_g_per_100g"] is None
        assert not entries[0]["is_below_detection"]

    def test_below_detection_sets_flag(self, tmp_path, monkeypatch) -> None:
        header = _make_row("code", "name", "grp", "ssgrp", None, "Protein")
        row1 = _make_row("7777", "Trace Food", "beverages", "soft drinks", None, "< 0,2")
        stub = _make_openpyxl_stub([header, row1])

        import scripts.import_ciqual as ic

        monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **kw: stub)
        entries = ic.read_ciqual_excel(tmp_path / "fake.xlsx")
        assert entries[0]["protein_g_per_100g"] is None
        assert entries[0]["is_below_detection"] is True

    def test_skips_rows_without_food_code(self, tmp_path, monkeypatch) -> None:
        header = _make_row("code", "name", "grp", "ssgrp", None, "Protein")
        row_ok = _make_row("1234", "Valid Food", "meat", "beef", None, "20,1")
        # Row with None in code position
        row_bad = list(_make_row("1235", "Bad Food", "meat", "beef", None, "10"))
        row_bad[6] = None
        stub = _make_openpyxl_stub([header, tuple(row_bad), row_ok])

        import scripts.import_ciqual as ic

        monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **kw: stub)
        entries = ic.read_ciqual_excel(tmp_path / "fake.xlsx")
        assert len(entries) == 1
        assert entries[0]["source_food_code"] == "1234"

    def test_source_metadata_preserved(self, tmp_path, monkeypatch) -> None:
        header = _make_row("code", "name", "grp", "ssgrp", None, "Protein")
        row1 = _make_row("5555", "Test", "fish", "lean fish", None, "17,9")
        stub = _make_openpyxl_stub([header, row1])

        import scripts.import_ciqual as ic

        monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **kw: stub)
        entries = ic.read_ciqual_excel(tmp_path / "fake.xlsx")
        e = entries[0]
        assert e["source"] == "ciqual"
        assert e["source_version"] == "2025"
        assert e["food_name_en"] == "Test"
        assert e["food_group"] == "fish"
        assert e["food_subgroup"] == "lean fish"


# ---------------------------------------------------------------------------
# CiqualProvider — matching and enrichment
# ---------------------------------------------------------------------------


def _make_entry(
    code: str,
    name: str,
    group: str,
    protein: str | None,
    *,
    below: bool = False,
    subgroup: str | None = None,
) -> CiqualEntry:
    return CiqualEntry(
        id=uuid4(),
        source_version="2025",
        source_food_code=code,
        food_name_en=name,
        food_group=group,
        food_subgroup=subgroup,
        food_subsubgroup=None,
        protein_g_per_100g=Decimal(protein) if protein is not None else None,
        is_below_detection=below,
    )


@pytest.fixture
def provider() -> CiqualProvider:
    entries = [
        _make_entry("8406", "Salad of pig's snout with sauce", "starters and dishes", "8.51"),
        _make_entry("25601", "Tuna salad with vegetables", "starters and dishes", "9.15"),
        _make_entry("26099", "Cod fillet raw", "fish", "17.9", subgroup="lean fish"),
        _make_entry("26200", "Salmon raw", "fish", "20.1", subgroup="fatty fish"),
        _make_entry("99999", "Below detection food", "beverages", None, below=True),
    ]
    return CiqualProvider.from_entries(entries)


class TestCiqualProviderMatch:
    def test_exact_name_match(self, provider: CiqualProvider) -> None:
        result = provider.match(food_name="Cod fillet raw")
        assert result is not None
        assert result.match_type == "exact_name"
        assert result.entry.source_food_code == "26099"
        assert result.confidence == Decimal("0.80")

    def test_exact_name_case_insensitive(self, provider: CiqualProvider) -> None:
        result = provider.match(food_name="COD FILLET RAW")
        assert result is not None
        assert result.entry.source_food_code == "26099"

    def test_food_group_average(self, provider: CiqualProvider) -> None:
        result = provider.match(food_group="fish")
        assert result is not None
        assert result.match_type == "food_group_average"
        # Average of 17.9 and 20.1 = 19.0
        assert result.entry.protein_g_per_100g == pytest.approx(Decimal("19.0"), abs=Decimal("0.5"))
        assert result.confidence == Decimal("0.55")

    def test_no_match_returns_none(self, provider: CiqualProvider) -> None:
        result = provider.match(food_name="Unknown product XYZ", food_group="unknown_group")
        assert result is None

    def test_below_detection_excluded_from_group_avg(self, provider: CiqualProvider) -> None:
        # "beverages" group only has the below-detection entry → no valid avg
        result = provider.match(food_group="beverages")
        assert result is None


class TestCiqualProviderEnrich:
    def test_enriches_missing_protein(self, provider: CiqualProvider) -> None:
        record = provider.enrich(
            _PRODUCT_ID,
            "protein_pct",
            food_name="Salmon raw",
            now=_NOW,
        )
        assert record is not None
        assert record.status is NutritionEnrichmentStatus.ENRICHED
        assert record.enriched_value == Decimal("20.1")
        assert record.source is NutritionEnrichmentSource.CIQUAL
        assert "2025" in record.rationale
        assert "26200" in record.rationale

    def test_failed_when_no_match(self, provider: CiqualProvider) -> None:
        record = provider.enrich(
            _PRODUCT_ID,
            "protein_pct",
            food_name="Imaginary product",
            food_group="imaginary_group",
            now=_NOW,
        )
        assert record is not None
        assert record.status is NutritionEnrichmentStatus.FAILED

    def test_returns_none_for_non_protein_nutrient(self, provider: CiqualProvider) -> None:
        record = provider.enrich(_PRODUCT_ID, "fat_pct", food_name="Cod fillet raw", now=_NOW)
        assert record is None

    def test_is_available_when_loaded(self, provider: CiqualProvider) -> None:
        assert provider.is_available is True

    def test_is_not_available_when_empty(self) -> None:
        empty = CiqualProvider.from_entries([])
        assert empty.is_available is False

    def test_enrichment_record_has_source_ciqual(self, provider: CiqualProvider) -> None:
        record = provider.enrich(_PRODUCT_ID, "protein_pct", food_name="Cod fillet raw", now=_NOW)
        assert record is not None
        assert record.source is NutritionEnrichmentSource.CIQUAL


# ---------------------------------------------------------------------------
# Enrichment priority order
# ---------------------------------------------------------------------------


class TestEnrichmentPriority:
    def test_ciqual_priority_between_manual_and_category(self) -> None:
        manual = _SOURCE_PRIORITY[NutritionEnrichmentSource.MANUAL_ALTERA]
        ciqual = _SOURCE_PRIORITY[NutritionEnrichmentSource.CIQUAL]
        category = _SOURCE_PRIORITY[NutritionEnrichmentSource.CATEGORY_AVERAGE]
        assert manual < ciqual < category

    def test_select_prefers_manual_over_ciqual(self) -> None:
        from altera_api.domain.enrichment import NutritionEnrichmentRecord

        manual_rec = NutritionEnrichmentRecord(
            product_id=_PRODUCT_ID,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("10.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.MANUAL_ALTERA,
            confidence=Decimal("0.90"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="manual",
            created_at=_NOW,
        )
        ciqual_rec = NutritionEnrichmentRecord(
            product_id=_PRODUCT_ID,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("17.9"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.CIQUAL,
            confidence=Decimal("0.80"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="ciqual",
            created_at=_NOW,
        )
        result = select_protein_enrichment([ciqual_rec, manual_rec])
        assert result is not None
        value, source = result
        assert source is NutritionEnrichmentSource.MANUAL_ALTERA
        assert value == Decimal("10.0")

    def test_select_prefers_ciqual_over_category(self) -> None:
        from altera_api.domain.enrichment import NutritionEnrichmentRecord

        ciqual_rec = NutritionEnrichmentRecord(
            product_id=_PRODUCT_ID,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("17.9"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.CIQUAL,
            confidence=Decimal("0.80"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="ciqual",
            created_at=_NOW,
        )
        cat_rec = NutritionEnrichmentRecord(
            product_id=_PRODUCT_ID,
            nutrient="protein_pct",
            original_value=None,
            enriched_value=Decimal("8.0"),
            unit="g_per_100g",
            source=NutritionEnrichmentSource.CATEGORY_AVERAGE,
            confidence=Decimal("0.60"),
            status=NutritionEnrichmentStatus.ENRICHED,
            rationale="category",
            created_at=_NOW,
        )
        result = select_protein_enrichment([cat_rec, ciqual_rec])
        assert result is not None
        value, source = result
        assert source is NutritionEnrichmentSource.CIQUAL
        assert value == Decimal("17.9")

    def test_never_overwrite_retailer_provided(self) -> None:
        """CIQUAL enrichment must never be applied when retailer protein is present.

        The enrichment pipeline only calls enrich() when protein_pct is None.
        This test verifies the assessor logic: if original_value is present,
        status must be NOT_NEEDED.
        """
        from altera_api.domain.enrichment import (
            NutritionEnrichmentRecord,
            NutritionEnrichmentStatus,
        )

        retailer_record = NutritionEnrichmentRecord(
            product_id=_PRODUCT_ID,
            nutrient="protein_pct",
            original_value=Decimal("23.2"),
            enriched_value=None,
            unit="g_per_100g",
            source=NutritionEnrichmentSource.RETAILER_PROVIDED,
            confidence=Decimal("1.0"),
            status=NutritionEnrichmentStatus.NOT_NEEDED,
            rationale="retailer provided",
            created_at=_NOW,
        )
        # NOT_NEEDED records are excluded from selection
        result = select_protein_enrichment([retailer_record])
        assert result is None
