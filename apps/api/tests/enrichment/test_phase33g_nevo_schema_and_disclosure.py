"""Phase 33G — NEVO migration smoke + report disclosure tests.

Two layers:

1. Migration smoke test — pins the structural contract of
   ``supabase/migrations/0032_phase33g_nevo_reference.sql`` so future
   edits don't drift away from what the importer and provider expect
   (column names, unique constraint, check constraints, indexes, RLS).

2. Report disclosure tests — assert the Protein Tracker coverage section
   emits the Phase 33G plant/animal-split provenance caveat and the
   NEVO/CIQUAL disclosure lines, so any report exported from staging
   tells the reader where the split came from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
MIGRATION_PATH = (
    REPO_ROOT / "supabase" / "migrations" / "0032_phase33g_nevo_reference.sql"
)


# ---------------------------------------------------------------------------
# Migration smoke
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migration_sql() -> str:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


class TestNevoMigrationSchema:
    def test_creates_nevo_reference_table(self, migration_sql: str) -> None:
        assert "create table if not exists public.nevo_reference" in migration_sql

    @pytest.mark.parametrize(
        "column",
        [
            "id",
            "source",
            "source_version",
            "nevo_code",
            "food_name_nl",
            "food_name_en",
            "food_group",
            "quantity_basis",
            "protein_g_per_100g",
            "plant_protein_g_per_100g",
            "animal_protein_g_per_100g",
            "created_at",
        ],
    )
    def test_required_columns_present(self, migration_sql: str, column: str) -> None:
        assert column in migration_sql, f"migration missing column {column}"

    def test_unique_on_source_version_and_code(self, migration_sql: str) -> None:
        assert "unique (source_version, nevo_code)" in migration_sql

    def test_source_check_pins_to_nevo(self, migration_sql: str) -> None:
        assert "check (source = 'nevo')" in migration_sql

    def test_non_negative_protein_constraints(self, migration_sql: str) -> None:
        # All three protein columns must allow NULL but reject negatives.
        for col in (
            "protein_g_per_100g",
            "plant_protein_g_per_100g",
            "animal_protein_g_per_100g",
        ):
            assert f"{col} is null or {col} >= 0" in migration_sql

    def test_lowercase_indexes_on_names_and_group(self, migration_sql: str) -> None:
        assert "(lower(food_name_en))" in migration_sql
        assert "(lower(food_name_nl))" in migration_sql
        assert "(lower(food_group))" in migration_sql

    def test_source_version_index(self, migration_sql: str) -> None:
        assert "nevo_reference_source_version_idx" in migration_sql

    def test_rls_enabled_and_altera_only(self, migration_sql: str) -> None:
        assert "alter table public.nevo_reference enable row level security" in migration_sql
        assert "altera_read_nevo" in migration_sql
        assert "altera_write_nevo" in migration_sql
        assert "public.current_user_is_altera()" in migration_sql


class TestImporterPayloadMatchesMigration:
    """Pin the importer's row keys to the table's columns.

    If the migration column names change, the importer must change with
    it; this test catches drift in one direction by asserting the keys
    the importer emits all appear in the migration text.
    """

    def test_importer_keys_are_table_columns(self, migration_sql: str) -> None:
        import sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from import_nevo import _row_to_entry  # type: ignore[import-not-found]

        # Build one entry from a minimal row dict; the keys are what
        # supabase-py will upsert.
        rejected: list[int] = [0]
        row = {
            "NEVO-code": "1",
            "Voedingsmiddelnaam/Dutch food name": "Test NL",
            "Engelse naam/Food name": "Test EN",
            "Food group": "Test",
            "Hoeveelheid/Quantity": "per 100g",
            "PROT (g)": "5",
            "PROTPL (g)": "5",
            "PROTAN (g)": "0",
        }
        entry = _row_to_entry(row, rejected_counter=rejected)
        assert entry is not None
        expected_columns = {
            "id",
            "source",
            "source_version",
            "nevo_code",
            "food_name_nl",
            "food_name_en",
            "food_group",
            "quantity_basis",
            "protein_g_per_100g",
            "plant_protein_g_per_100g",
            "animal_protein_g_per_100g",
        }
        assert set(entry.keys()) == expected_columns
        # And every key appears in the migration text.
        for key in entry.keys():
            assert key in migration_sql, (
                f"importer emits column {key!r} not declared in migration"
            )


class TestImporterExcelDispatch:
    """The dispatcher should pick the right reader by file extension."""

    def test_dispatch_uses_csv_for_csv(self, tmp_path: Path) -> None:
        import sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from import_nevo import read_nevo  # type: ignore[import-not-found]

        csv_text = (
            '"NEVO-versie/NEVO-version"|"Voedingsmiddelgroep"|"Food group"|"NEVO-code"|'
            '"Voedingsmiddelnaam/Dutch food name"|"Engelse naam/Food name"|"Synoniem"|'
            '"Hoeveelheid/Quantity"|"Opmerking"|"Bevat sporen van/Contains traces of"|'
            '"Is verrijkt met/Is fortified with"|"PROT (g)"|"PROTPL (g)"|"PROTAN (g)"\n'
            'NEVO-Online 2025 9.0|Test|Test|1|Test NL|Test EN||per 100g||||"5"|"5"|"0"\n'
        )
        p = tmp_path / "tiny.csv"
        p.write_text(csv_text, encoding="utf-8")
        entries = read_nevo(p)
        assert len(entries) == 1


# ---------------------------------------------------------------------------
# Report disclosure
# ---------------------------------------------------------------------------


class TestPlantAnimalSplitProvenanceCaveat:
    """The Phase 33G provenance caveat must always be present on a PT run."""

    def test_pt_caveats_includes_provenance_line(self) -> None:
        from decimal import Decimal
        from uuid import uuid4

        from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary
        from altera_api.exports.coverage import _pt_caveats

        # Construct a minimal valid PT summary (no composites, no data gaps).
        summary = ProteinTrackerCalculationSummary(
            run_id=uuid4(),
            reporting_period_label="FY24",
            per_group=(),
            plant_protein_kg=Decimal("0"),
            animal_protein_kg=Decimal("0"),
            total_in_scope_protein_kg=Decimal("0"),
            rows_with_per_product_split=0,
            rows_protein_source_label=0,
            rows_protein_source_reference_db=0,
            out_of_scope_count=0,
            unknown_count=0,
            methodology_version="v1",
            methodology_source_edition="edA",
            taxonomy_version="v1",
            rules_version="v1",
        )
        caveats = _pt_caveats(summary, products_with_missing_protein=0)
        joined = " ".join(caveats)
        assert "Plant/animal protein split" in joined
        assert "Protein Tracker classification" in joined
        assert "NEVO" in joined
        assert "CIQUAL provides total protein only" in joined


class TestNevoEnrichmentCaveat:
    """When an enrichment record cites NEVO, the coverage caveats expose it."""

    def test_nevo_record_emits_dedicated_caveat(self) -> None:
        from datetime import UTC, datetime
        from decimal import Decimal
        from uuid import uuid4

        from altera_api.api.state import InMemoryStore
        from altera_api.domain.common import Methodology
        from altera_api.domain.enrichment import (
            NutritionEnrichmentRecord,
            NutritionEnrichmentSource,
            NutritionEnrichmentStatus,
        )
        from altera_api.domain.product import (
            NormalizedProduct,
            ProteinSource,
            PTProductFields,
        )
        from altera_api.exports.coverage import _enrichment_caveats

        store = InMemoryStore()
        org_id = uuid4()
        project_id = uuid4()
        product_id = uuid4()
        # Add a product so the project exists and store methods work.
        store.add_product(
            NormalizedProduct(
                id=product_id,
                upload_id=uuid4(),
                project_id=project_id,
                organisation_id=org_id,
                row_number=1,
                external_product_id="P-1",
                product_name="Test",
                weight_per_item_kg=Decimal("0.4"),
                methodologies_enabled=frozenset({Methodology.PROTEIN_TRACKER}),
                pt_fields=PTProductFields(
                    items_purchased=Decimal("10"),
                    protein_pct=None,
                    protein_source=ProteinSource.REFERENCE_DB,
                ),
                created_at=datetime.now(UTC),
            )
        )
        store.add_enrichment_record(
            NutritionEnrichmentRecord(
                product_id=product_id,
                nutrient="protein_pct",
                original_value=None,
                enriched_value=Decimal("12.0"),
                unit="g_per_100g",
                source=NutritionEnrichmentSource.NEVO,
                confidence=Decimal("0.85"),
                status=NutritionEnrichmentStatus.ENRICHED,
                rationale="NEVO 2025_v9.0 exact_name_en match",
                created_at=datetime.now(UTC),
                created_by=None,
            )
        )

        caveats = _enrichment_caveats(store, project_id, pt_summary=None)
        joined = " ".join(caveats)
        assert "NEVO" in joined
        assert "RIVM" in joined
        # CIQUAL line must NOT appear when only a NEVO record exists.
        assert "ANSES CIQUAL" not in joined
