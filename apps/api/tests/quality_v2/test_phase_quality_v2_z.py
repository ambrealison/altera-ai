"""Phase Quality-V2-Z — plant/animal split proposals + guarded split apply.

Derives a plant/animal split from approved V2 total-protein records using the
existing Protein Tracker classification. Proposal CLI is dry-run only; the apply
CLI is double-gated and writes sibling enrichment records in the existing schema.
No production behaviour change; V1 default; embeddings off; routes clean.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip  (avoid persistence<->api cycle)
from altera_api.classification_v2 import apply_nevo_v2_protein_split as splitapply
from altera_api.classification_v2 import propose_nevo_v2_protein_split as propose
from altera_api.classification_v2.nevo_v2_protein_split import split_proposal
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)


# ---------------------------------------------------------------------------
# Part B — split policy.
# ---------------------------------------------------------------------------
class TestSplitPolicy:
    def test_animal_core(self) -> None:
        p = split_proposal(pt_group="animal_core", total_protein=Decimal("10"),
                           has_manual_override=False, has_classification=True)
        assert p["action"] == "would_split"
        assert p["plant"] == Decimal("0") and p["animal"] == Decimal("10")

    @pytest.mark.parametrize("group", ["plant_based_core", "plant_based_non_core"])
    def test_plant_groups(self, group) -> None:
        p = split_proposal(pt_group=group, total_protein=Decimal("12"),
                           has_manual_override=False, has_classification=True)
        assert p["action"] == "would_split"
        assert p["plant"] == Decimal("12") and p["animal"] == Decimal("0")

    @pytest.mark.parametrize("group", ["composite_products", "unknown",
                                       "out_of_scope"])
    def test_review_groups(self, group) -> None:
        p = split_proposal(pt_group=group, total_protein=Decimal("9"),
                           has_manual_override=False, has_classification=True)
        assert p["action"] == "needs_review"
        assert p["plant"] is None and p["animal"] is None

    def test_manual_override_skips(self) -> None:
        p = split_proposal(pt_group="animal_core", total_protein=Decimal("10"),
                           has_manual_override=True, has_classification=True)
        assert p["action"] == "skip_manual_override"

    def test_missing_classification_skips(self) -> None:
        p = split_proposal(pt_group=None, total_protein=Decimal("10"),
                           has_manual_override=False, has_classification=False)
        assert p["action"] == "skip_missing_class"


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------
def _v2(pid, value):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal(value),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={},
    )


def _manual(pid):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.MANUAL_ALTERA, match_method="manual",
        source_version=None, enriched_value=Decimal("9"),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata=None,
    )


def _split_rec(pid, nutrient):
    return SimpleNamespace(
        product_id=pid, nutrient=nutrient, unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings_split", enriched_value=Decimal("3"),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={},
    )


class _Store:
    def __init__(self, records, classes, *, columns=True, per_product=None):
        self._records = records
        self._classes = classes
        self._columns = columns
        self._per_product = per_product or {}
        self.writes: list = []

    def list_enrichment_records_for_project(self, project_id):
        return self._records

    def list_products_for_project(self, project_id):
        seen = {str(getattr(r, "product_id", "")) for r in self._records}
        return [SimpleNamespace(id=pid, product_name=f"P-{pid[:4]}")
                for pid in seen]

    def get_pt_classification(self, pid):
        return self._classes.get(str(pid))

    def get_enrichment_records_for_product(self, pid):
        return self._per_product.get(str(pid), [])

    def has_enrichment_provenance_columns(self):
        return self._columns

    def add_enrichment_record(self, record):
        self.writes.append(record)


def _clf(group):
    return SimpleNamespace(pt_group=group)


# ---------------------------------------------------------------------------
# Part C — proposal CLI (dry-run, no writes).
# ---------------------------------------------------------------------------
class TestProposalCLI:
    def test_proposals_no_db_writes(self, tmp_path) -> None:
        a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
        records = [_v2(a, "20"), _v2(b, "15"), _v2(c, "8"), _v2(d, "12")]
        classes = {str(a): _clf("animal_core"),
                   str(b): _clf("plant_based_core"),
                   str(c): _clf("composite_products")}  # d: no class
        store = _Store(records, classes)
        rc = propose.main(["--project-id", "pp", "--output-dir", str(tmp_path)],
                          store=store, generated_at="x")
        assert rc == 0
        assert store.writes == []  # never writes
        s = json.loads(
            (tmp_path / "nevo_v2_protein_split_proposals_pp.json").read_text())
        assert s["split_action_counts"] == {
            "would_split": 2, "needs_review": 1, "skip_missing_class": 1,
            "skip_manual_override": 0,
        }
        by_pid = {r["product_id"]: r for r in s["proposals"]}
        assert by_pid[str(a)]["proposed_animal_protein_g_per_100g"] == "20"
        assert by_pid[str(a)]["proposed_plant_protein_g_per_100g"] == "0"
        assert by_pid[str(b)]["proposed_plant_protein_g_per_100g"] == "15"
        assert by_pid[str(c)]["split_action"] == "needs_review"
        assert by_pid[str(d)]["split_action"] == "skip_missing_class"
        # CSV carries the required columns.
        rows = list(csv.DictReader(
            (tmp_path / "nevo_v2_protein_split_proposals_pp.csv").open()))
        assert {"total_protein_g_per_100g", "pt_group", "split_action",
                "split_reason"} <= set(rows[0].keys())

    def test_manual_override_proposal(self, tmp_path) -> None:
        a = uuid4()
        store = _Store([_v2(a, "10"), _manual(a)],
                       {str(a): _clf("animal_core")})
        propose.main(["--project-id", "mo", "--output-dir", str(tmp_path)],
                     store=store, generated_at="x")
        s = json.loads(
            (tmp_path / "nevo_v2_protein_split_proposals_mo.json").read_text())
        assert s["proposals"][0]["split_action"] == "skip_manual_override"


# ---------------------------------------------------------------------------
# Part D — guarded split apply.
# ---------------------------------------------------------------------------
def _proposals_csv(tmp_path, rows):
    cols = ["product_id", "product_name", "total_protein_g_per_100g",
            "pt_group", "proposed_plant_protein_g_per_100g",
            "proposed_animal_protein_g_per_100g", "split_action",
            "split_reason"]
    path = tmp_path / "nevo_v2_protein_split_proposals_ap.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def _wsplit(pid, plant, animal, group="animal_core"):
    return dict(product_id=str(pid), product_name="P", total_protein_g_per_100g="10",
                pt_group=group, proposed_plant_protein_g_per_100g=plant,
                proposed_animal_protein_g_per_100g=animal,
                split_action="would_split", split_reason="")


class TestSplitApply:
    def test_dry_run_writes_nothing(self, tmp_path) -> None:
        a = uuid4()
        path = _proposals_csv(tmp_path, [_wsplit(a, "0", "10")])
        store = _Store([], {})
        rc = splitapply.main(
            ["--proposals", str(path), "--project-id", "ap",
             "--output-dir", str(tmp_path)],
            store=store, generated_at="2026-06-04T00:00:00+00:00")
        assert rc == 0
        assert store.writes == []
        s = json.loads(
            (tmp_path / "nevo_v2_split_apply_result_ap.json").read_text())
        assert s["dry_run"] is True and s["would_write_count"] == 1

    def test_confirmed_writes_two_records(self, tmp_path) -> None:
        a = uuid4()
        path = _proposals_csv(tmp_path, [_wsplit(a, "0", "10")])
        store = _Store([], {})
        rc = splitapply.main(
            ["--proposals", str(path), "--project-id", "ap",
             "--output-dir", str(tmp_path), "--confirm-apply-split"],
            store=store, generated_at="2026-06-04T00:00:00+00:00")
        assert rc == 0
        assert len(store.writes) == 2
        nutrients = {r.nutrient for r in store.writes}
        assert nutrients == {"plant_protein_pct", "animal_protein_pct"}
        for r in store.writes:
            assert r.source is NutritionEnrichmentSource.NEVO
            assert r.source_version == "v2_embeddings_split"
            assert r.source_metadata["split_apply_path"] is True
        s = json.loads(
            (tmp_path / "nevo_v2_split_apply_result_ap.json").read_text())
        assert s["written_pairs_count"] == 1
        assert s["records_written_count"] == 2

    def test_confirm_but_missing_columns_writes_nothing(self, tmp_path) -> None:
        a = uuid4()
        path = _proposals_csv(tmp_path, [_wsplit(a, "0", "10")])
        store = _Store([], {}, columns=False)
        rc = splitapply.main(
            ["--proposals", str(path), "--project-id", "ap",
             "--output-dir", str(tmp_path), "--confirm-apply-split"],
            store=store, generated_at="x")
        assert rc == 2
        assert store.writes == []

    def test_skips_manual_and_existing_split(self, tmp_path) -> None:
        a, b = uuid4(), uuid4()
        path = _proposals_csv(tmp_path,
                              [_wsplit(a, "0", "10"), _wsplit(b, "5", "5")])
        per_product = {
            str(a): [_manual(a)],
            str(b): [_split_rec(b, "plant_protein_pct")],
        }
        store = _Store([], {}, per_product=per_product)
        splitapply.main(
            ["--proposals", str(path), "--project-id", "ap",
             "--output-dir", str(tmp_path), "--confirm-apply-split"],
            store=store, generated_at="2026-06-04T00:00:00+00:00")
        assert store.writes == []
        s = json.loads(
            (tmp_path / "nevo_v2_split_apply_result_ap.json").read_text())
        assert s["skipped_manual_count"] == 1
        assert s["skipped_existing_split_count"] == 1


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_proposal_cli_writes_no_db(self) -> None:
        src = Path(propose.__file__).read_text(encoding="utf-8")
        assert "add_enrichment_record" not in src
        assert ".insert(" not in src

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

    def test_routes_do_not_import_split(self) -> None:
        api_dir = Path(propose.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "nevo_v2_protein_split" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
