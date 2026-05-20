"""Phase 33E — NEVO importer + provider + priority tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.nevo import NevoEntry
from altera_api.enrichment.providers.nevo import NevoProvider
from altera_api.enrichment.registry import (
    AVAILABLE_SOURCES,
    ENRICHMENT_SOURCE_REGISTRY,
)

# ---------------------------------------------------------------------------
# Fixture: a tiny in-memory NEVO CSV in the real pipe-delimited format
# ---------------------------------------------------------------------------

_TINY_NEVO_CSV = (
    '"NEVO-versie/NEVO-version"|"Voedingsmiddelgroep"|"Food group"|"NEVO-code"|'
    '"Voedingsmiddelnaam/Dutch food name"|"Engelse naam/Food name"|"Synoniem"|'
    '"Hoeveelheid/Quantity"|"Opmerking"|"Bevat sporen van/Contains traces of"|'
    '"Is verrijkt met/Is fortified with"|"PROT (g)"|"PROTPL (g)"|"PROTAN (g)"\n'
    'NEVO-Online 2025 9.0|Aardappelen en knolgewassen|Potatoes and tubers|1|'
    'Aardappelen rauw|Potatoes raw||per 100g||||"2"|"2"|"0"\n'
    'NEVO-Online 2025 9.0|Vlees|Meat|100|Kipfilet|Chicken breast||per 100g||||'
    '"23,2"|"0"|"23,2"\n'
    'NEVO-Online 2025 9.0|Vlees|Meat|101|Rundergehakt|Beef mince||per 100g||||'
    '"18,5"|"0"|"18,5"\n'
    'NEVO-Online 2025 9.0|Onbekend|Unknown|999|Iets zonder split|Thing without split||'
    'per 100g||||"10"||\n'
)


@pytest.fixture
def nevo_csv_path(tmp_path: Path) -> Path:
    p = tmp_path / "tiny_nevo.csv"
    p.write_text(_TINY_NEVO_CSV, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Importer
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _add_scripts_to_path() -> None:
    """Make scripts/ importable for direct test access."""
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


class TestImporter:
    def test_reads_all_rows(self, nevo_csv_path: Path) -> None:
        from import_nevo import read_nevo_csv  # type: ignore[import-not-found]

        entries = read_nevo_csv(nevo_csv_path)
        assert len(entries) == 4

    def test_imports_prot_protpl_protan(self, nevo_csv_path: Path) -> None:
        from import_nevo import read_nevo_csv  # type: ignore[import-not-found]

        entries = read_nevo_csv(nevo_csv_path)
        chicken = next(e for e in entries if e["nevo_code"] == "100")
        assert chicken["protein_g_per_100g"] == pytest.approx(23.2)
        assert chicken["plant_protein_g_per_100g"] == pytest.approx(0.0)
        assert chicken["animal_protein_g_per_100g"] == pytest.approx(23.2)

    def test_imports_food_names_both_languages(self, nevo_csv_path: Path) -> None:
        from import_nevo import read_nevo_csv  # type: ignore[import-not-found]

        entries = read_nevo_csv(nevo_csv_path)
        chicken = next(e for e in entries if e["nevo_code"] == "100")
        assert chicken["food_name_nl"] == "Kipfilet"
        assert chicken["food_name_en"] == "Chicken breast"
        assert chicken["food_group"] == "Meat"

    def test_missing_split_returns_none(self, nevo_csv_path: Path) -> None:
        from import_nevo import read_nevo_csv  # type: ignore[import-not-found]

        entries = read_nevo_csv(nevo_csv_path)
        unsplit = next(e for e in entries if e["nevo_code"] == "999")
        assert unsplit["protein_g_per_100g"] == pytest.approx(10.0)
        assert unsplit["plant_protein_g_per_100g"] is None
        assert unsplit["animal_protein_g_per_100g"] is None

    def test_negative_protein_rejected(self, tmp_path: Path) -> None:
        from import_nevo import read_nevo_csv  # type: ignore[import-not-found]

        bad_csv = (
            '"NEVO-versie/NEVO-version"|"Voedingsmiddelgroep"|"Food group"|"NEVO-code"|'
            '"Voedingsmiddelnaam/Dutch food name"|"Engelse naam/Food name"|"Synoniem"|'
            '"Hoeveelheid/Quantity"|"Opmerking"|"Bevat sporen van/Contains traces of"|'
            '"Is verrijkt met/Is fortified with"|"PROT (g)"|"PROTPL (g)"|"PROTAN (g)"\n'
            'NEVO-Online 2025 9.0|Test|Test|7|Bad|Bad||per 100g||||"-5"|"0"|"0"\n'
        )
        p = tmp_path / "bad.csv"
        p.write_text(bad_csv, encoding="utf-8")
        entries = read_nevo_csv(p)
        # Row is kept but protein values are zeroed out (rejected as invalid)
        assert len(entries) == 1
        assert entries[0]["protein_g_per_100g"] is None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def _make_entries() -> list[NevoEntry]:
    return [
        NevoEntry(
            id=uuid4(),
            source_version="2025_v9.0",
            nevo_code="1",
            food_name_nl="Aardappelen rauw",
            food_name_en="Potatoes raw",
            food_group="Potatoes and tubers",
            quantity_basis="per 100g",
            protein_g_per_100g=Decimal("2"),
            plant_protein_g_per_100g=Decimal("2"),
            animal_protein_g_per_100g=Decimal("0"),
        ),
        NevoEntry(
            id=uuid4(),
            source_version="2025_v9.0",
            nevo_code="100",
            food_name_nl="Kipfilet",
            food_name_en="Chicken breast",
            food_group="Meat",
            quantity_basis="per 100g",
            protein_g_per_100g=Decimal("23.2"),
            plant_protein_g_per_100g=Decimal("0"),
            animal_protein_g_per_100g=Decimal("23.2"),
        ),
        NevoEntry(
            id=uuid4(),
            source_version="2025_v9.0",
            nevo_code="999",
            food_name_nl="Iets zonder split",
            food_name_en="Thing without split",
            food_group="Unknown",
            quantity_basis="per 100g",
            protein_g_per_100g=Decimal("10"),
            plant_protein_g_per_100g=None,
            animal_protein_g_per_100g=None,
        ),
    ]


class TestProvider:
    def test_exact_english_name_match(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        result = provider.match(food_name="chicken breast")
        assert result is not None
        assert result.match_type == "exact_name_en"
        assert result.entry.protein_g_per_100g == Decimal("23.2")
        assert result.split_available is True

    def test_exact_dutch_name_match(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        result = provider.match(food_name="Kipfilet")
        assert result is not None
        assert result.entry.food_name_en == "Chicken breast"
        assert result.entry.animal_protein_g_per_100g == Decimal("23.2")

    def test_no_match_returns_none(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        result = provider.match(food_name="quantum cheese")
        assert result is None

    def test_split_unavailable_when_only_total(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        result = provider.match(food_name="thing without split")
        assert result is not None
        assert result.entry.protein_g_per_100g == Decimal("10")
        assert result.split_available is False

    def test_enrich_returns_record_with_split(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        pid = uuid4()
        record = provider.enrich(
            pid,
            "protein_pct",
            food_name="Chicken breast",
            now=datetime.now(UTC),
        )
        assert record is not None
        assert record.source is NutritionEnrichmentSource.NEVO
        assert record.status is NutritionEnrichmentStatus.ENRICHED
        assert record.enriched_value == Decimal("23.2")
        assert "plant/animal split" in record.rationale

    def test_enrich_marks_no_split_when_unavailable(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        record = provider.enrich(
            uuid4(),
            "protein_pct",
            food_name="Thing without split",
            now=datetime.now(UTC),
        )
        assert record is not None
        assert "no plant/animal split" in record.rationale

    def test_enrich_returns_failed_when_no_match(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        record = provider.enrich(
            uuid4(),
            "protein_pct",
            food_name="quantum cheese",
            now=datetime.now(UTC),
        )
        assert record is not None
        assert record.status is NutritionEnrichmentStatus.FAILED

    def test_enrich_only_handles_protein_pct(self) -> None:
        provider = NevoProvider.from_entries(_make_entries())
        record = provider.enrich(
            uuid4(),
            "fat_pct",
            food_name="Chicken breast",
            now=datetime.now(UTC),
        )
        assert record is None


# ---------------------------------------------------------------------------
# Priority registry
# ---------------------------------------------------------------------------


class TestEnrichmentPriority:
    def test_nevo_is_available(self) -> None:
        nevo = next(s for s in ENRICHMENT_SOURCE_REGISTRY if s.source is NutritionEnrichmentSource.NEVO)
        assert nevo.is_available is True

    def test_nevo_priority_higher_than_ciqual(self) -> None:
        """Lower priority number = higher preference."""
        nevo = next(s for s in ENRICHMENT_SOURCE_REGISTRY if s.source is NutritionEnrichmentSource.NEVO)
        ciqual = next(s for s in ENRICHMENT_SOURCE_REGISTRY if s.source is NutritionEnrichmentSource.CIQUAL)
        assert nevo.priority < ciqual.priority

    def test_ciqual_remains_available(self) -> None:
        """CIQUAL is not removed — it remains as a total-protein fallback."""
        ciqual = next(s for s in ENRICHMENT_SOURCE_REGISTRY if s.source is NutritionEnrichmentSource.CIQUAL)
        assert ciqual.is_available is True

    def test_retailer_provided_still_first(self) -> None:
        """Retailer-provided protein_pct must remain the highest-priority source."""
        retailer = next(
            s for s in ENRICHMENT_SOURCE_REGISTRY
            if s.source is NutritionEnrichmentSource.RETAILER_PROVIDED
        )
        for other in ENRICHMENT_SOURCE_REGISTRY:
            if other.source is NutritionEnrichmentSource.RETAILER_PROVIDED:
                continue
            assert retailer.priority < other.priority

    def test_available_sources_order_nevo_before_ciqual(self) -> None:
        sorted_avail = sorted(AVAILABLE_SOURCES, key=lambda s: s.priority)
        sources = [s.source for s in sorted_avail]
        nevo_idx = sources.index(NutritionEnrichmentSource.NEVO)
        ciqual_idx = sources.index(NutritionEnrichmentSource.CIQUAL)
        assert nevo_idx < ciqual_idx
