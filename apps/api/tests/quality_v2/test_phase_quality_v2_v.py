"""Phase Quality-V2-V — V2 enrichment provenance fields (model/store only).

Adds optional ``source_version`` + ``source_metadata`` to the enrichment domain
model and mapper so a FUTURE (still-gated) apply can persist V2-tagged records.
No apply path, no V2 activation, no route wiring; V1 stays default and existing
records keep null provenance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

# Load the api package first so importing persistence.mappers below does not
# trip the persistence<->api import cycle at collection time.
import altera_api.api  # noqa: F401  isort:skip
from altera_api.domain.enrichment import (
    SOURCE_VERSION_V2_EMBEDDINGS,
    NutritionEnrichmentRecord,
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.persistence.mappers import (
    enrichment_record_from_row,
    enrichment_record_to_row,
)

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[4] / "supabase" / "migrations"
)
_MIGRATION = _MIGRATIONS_DIR / "0037_quality_v2v_nevo_enrichment_provenance.sql"


def _record(**kw) -> NutritionEnrichmentRecord:
    base = dict(
        product_id=uuid4(), nutrient="protein_pct",
        original_value=None, enriched_value=Decimal("8.0"), unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, confidence=Decimal("0.97"),
        status=NutritionEnrichmentStatus.ENRICHED, rationale="matched",
        created_at=datetime(2026, 6, 4, tzinfo=UTC), created_by=None,
        match_method="deterministic",
    )
    base.update(kw)
    return NutritionEnrichmentRecord(**base)


def _row(**kw) -> dict:
    base = dict(
        product_id=str(uuid4()), nutrient="protein_pct", original_value=None,
        enriched_value=8.0, unit="g_per_100g", source="nevo", confidence=0.97,
        status="enriched", rationale="matched",
        created_at="2026-06-04T00:00:00+00:00", created_by=None,
        match_method="deterministic",
    )
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Part B — model + mapper.
# ---------------------------------------------------------------------------
class TestModelDefaults:
    def test_record_without_provenance_defaults_none(self) -> None:
        rec = _record()
        assert rec.source_version is None
        assert rec.source_metadata is None

    def test_from_row_without_provenance_keys(self) -> None:
        # A pre-0037 / V1 row has neither key → both default to None.
        rec = enrichment_record_from_row(_row())
        assert rec.source_version is None
        assert rec.source_metadata is None
        assert rec.match_method == "deterministic"

    def test_from_row_with_null_provenance(self) -> None:
        rec = enrichment_record_from_row(
            _row(source_version=None, source_metadata=None)
        )
        assert rec.source_version is None and rec.source_metadata is None


class TestMapperToRow:
    def test_v1_to_row_omits_provenance_columns(self) -> None:
        # V1 writes must not depend on migration 0037 — provenance keys absent.
        row = enrichment_record_to_row(_record())
        assert "source_version" not in row
        assert "source_metadata" not in row
        assert row["match_method"] == "deterministic"

    def test_v2_to_row_includes_provenance(self) -> None:
        meta = {
            "provider": "voyage", "model": "voyage-4-lite", "top_k": 20,
            "matcher_confidence": 0.97, "nutrition_safety_action": "would_enrich",
            "review_package_id": "rp-1", "apply_plan_id": "ap-1",
        }
        row = enrichment_record_to_row(_record(
            match_method="ai_assisted",
            source_version=SOURCE_VERSION_V2_EMBEDDINGS, source_metadata=meta,
        ))
        assert row["source_version"] == "v2_embeddings"
        assert row["source_metadata"] == meta
        # match_method stays inside the existing enum — NOT 'v2_embeddings'.
        assert row["match_method"] == "ai_assisted"


class TestRoundTrip:
    def test_v2_round_trip(self) -> None:
        meta = {"provider": "voyage", "model": "voyage-4-lite", "top_k": 20}
        rec = _record(
            match_method="ai_assisted",
            source_version=SOURCE_VERSION_V2_EMBEDDINGS, source_metadata=meta,
        )
        rebuilt = enrichment_record_from_row(enrichment_record_to_row(rec))
        assert rebuilt.source_version == "v2_embeddings"
        assert rebuilt.source_metadata == meta
        assert rebuilt.match_method == "ai_assisted"
        assert rebuilt.source is NutritionEnrichmentSource.NEVO

    def test_v1_round_trip_keeps_null_provenance(self) -> None:
        rebuilt = enrichment_record_from_row(enrichment_record_to_row(_record()))
        assert rebuilt.source_version is None
        assert rebuilt.source_metadata is None


# ---------------------------------------------------------------------------
# Part A — migration 0037 is additive only; match_method CHECK untouched.
# ---------------------------------------------------------------------------
class TestMigration:
    def test_migration_file_exists(self) -> None:
        assert _MIGRATION.exists()

    def test_adds_nullable_provenance_columns(self) -> None:
        sql = _MIGRATION.read_text(encoding="utf-8").lower()
        assert "add column if not exists source_version text" in sql
        assert "add column if not exists source_metadata jsonb" in sql
        # additive only — no NOT NULL, no backfill update.
        assert "update public.nutrition_enrichment_records" not in sql

    def test_does_not_touch_match_method_check(self) -> None:
        sql = _MIGRATION.read_text(encoding="utf-8").lower()
        # No constraint DDL at all in this migration.
        assert "add constraint" not in sql
        assert "drop constraint" not in sql
        assert "match_method_check" not in sql
        assert "check (" not in sql

    def test_match_method_check_last_changed_in_0035(self) -> None:
        migrations = _MIGRATION.parent
        touching = sorted(
            p.name for p in migrations.glob("*.sql")
            if "match_method_check" in p.read_text(encoding="utf-8")
        )
        # 0033 added it, 0035 extended it; 0037 must NOT appear.
        assert touching[-1].startswith("0035")
        assert not any(n.startswith("0037") for n in touching)


# ---------------------------------------------------------------------------
# Safety — defaults unchanged, routes clean, apply still blocked.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_v1_default_and_embeddings_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

    def test_routes_do_not_import_v2(self) -> None:
        from altera_api.classification_v2 import nevo_v2_enrich as cli

        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_apply_path_still_refuses(self, tmp_path, capsys) -> None:
        from altera_api.classification_v2 import nevo_v2_enrich as cli

        rc = cli.main(
            ["--project-id", str(uuid4()), "--matcher-version", "v2-embeddings",
             "--evaluator-fake", "--apply", "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path)],
            store=object(),  # refused before any store/DB access
        )
        assert rc == 2
        out = capsys.readouterr().out
        assert "--apply is gated" in out and "migration" in out.lower()
        assert not list(tmp_path.glob("nevo_v2_enrich_*"))
