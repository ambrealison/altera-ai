"""Phase Quality-V2-AA hotfix — robust split audit after /tmp artifact loss.

Proposals are now idempotent (existing split records don't change split_action),
and the audit reconstructs eligibility from the DB when the original proposal CSV
is missing/stale — so a fresh Render pod can audit without the /tmp CSV and a
stale CSV never triggers a false rollback. Real corruptions still fail.
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
from altera_api.classification_v2 import audit_nevo_v2_protein_split as audit
from altera_api.classification_v2 import propose_nevo_v2_protein_split as propose
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)

_PROP_COLUMNS = propose.PROPOSAL_CSV_COLUMNS


def _prot(pid, value):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal(value),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={})


def _split(pid, nutrient, value, **kw):
    base = dict(
        product_id=pid, nutrient=nutrient, unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings_split", enriched_value=Decimal(value),
        status=NutritionEnrichmentStatus.ENRICHED, source_metadata={"x": 1})
    base.update(kw)
    return SimpleNamespace(**base)


def _clf(group):
    return SimpleNamespace(pt_group=group)


class _Store:
    """Fake store WITH PT classification (so the audit can reconstruct)."""

    def __init__(self, records, classes):
        self._records = records
        self._classes = classes

    def list_enrichment_records_for_project(self, project_id):
        return self._records

    def get_pt_classification(self, pid):
        return self._classes.get(str(pid))

    def list_products_for_project(self, project_id):
        seen = {str(getattr(r, "product_id", "")) for r in self._records}
        return [SimpleNamespace(id=pid, product_name=f"P-{pid[:4]}")
                for pid in seen]


def _scenario():
    """3 products: a=animal_core, b=plant_based_core (both split applied),
    c=composite (needs_review, no split)."""
    a, b, c = uuid4(), uuid4(), uuid4()
    classes = {str(a): _clf("animal_core"), str(b): _clf("plant_based_core"),
               str(c): _clf("composite_products")}
    records = [
        _prot(a, "24.9"), _split(a, "plant_protein_pct", "0"),
        _split(a, "animal_protein_pct", "24.9"),
        _prot(b, "6.8"), _split(b, "plant_protein_pct", "6.8"),
        _split(b, "animal_protein_pct", "0"),
        _prot(c, "10"),
    ]
    return (a, b, c), classes, records


def _audit(tmp_path, project_id, store, *args):
    return audit.main(
        ["--project-id", project_id, "--output-dir", str(tmp_path), *args],
        store=store, generated_at="x")


def _audit_json(tmp_path, project_id):
    return json.loads(
        (tmp_path / f"nevo_v2_split_audit_{project_id}.json").read_text())


# ---------------------------------------------------------------------------
# Part A — proposals are idempotent before vs after split records exist.
# ---------------------------------------------------------------------------
class TestProposalIdempotent:
    def test_same_decisions_before_and_after_splits(self) -> None:
        (a, b, c), classes, after = _scenario()
        # "before" state: only the V2 totals, no split records.
        before = [r for r in after if r.source_version != "v2_embeddings_split"]

        def decisions(records):
            rows = propose.build_proposals(
                records=records, classifications=classes, names={})
            return {r["product_id"]: r["split_action"] for r in rows}

        assert decisions(before) == decisions(after)
        # and the headline counts are unchanged (2 would_split, 1 needs_review).
        after_rows = propose.build_proposals(records=after,
                                             classifications=classes, names={})
        counts = {a: sum(1 for r in after_rows if r["split_action"] == a)
                  for a in ("would_split", "needs_review")}
        assert counts == {"would_split": 2, "needs_review": 1}


# ---------------------------------------------------------------------------
# Part B/C — audit robust to missing / stale proposals.
# ---------------------------------------------------------------------------
class TestAuditRobust:
    def test_reconstructs_when_no_proposals(self, tmp_path) -> None:
        _ids, classes, records = _scenario()
        rc = _audit(tmp_path, "recon", _Store(records, classes))
        assert rc == 0
        s = _audit_json(tmp_path, "recon")
        assert s["audit_status"] == "pass"
        assert s["recommendation"] == "split_apply_verified"
        assert s["proposal_source"] == "reconstructed"
        assert s["proposal_mismatch_warning"] is False
        assert s["unexpected_split_count"] == 0
        assert s["matched_would_split_count"] == 2
        assert (tmp_path
                / "nevo_v2_protein_split_reconstructed_proposals_recon.csv").exists()

    def test_reconstruct_flag(self, tmp_path) -> None:
        _ids, classes, records = _scenario()
        # even with a (here-absent) CSV, the flag forces reconstruction.
        rc = _audit(tmp_path, "flag", _Store(records, classes),
                    "--reconstruct-proposals-from-db")
        assert rc == 0
        assert _audit_json(tmp_path, "flag")["proposal_source"] == "reconstructed"

    def test_stale_proposals_warn_not_fail(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        # a stale CSV that (wrongly) calls every product needs_review.
        stale = tmp_path / "stale.csv"
        with stale.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_PROP_COLUMNS)
            w.writeheader()
            for pid in ids:
                w.writerow({c: "" for c in _PROP_COLUMNS}
                           | {"product_id": str(pid),
                              "split_action": "needs_review"})
        rc = _audit(tmp_path, "stale", _Store(records, classes),
                    "--proposals", str(stale))
        assert rc == 0
        s = _audit_json(tmp_path, "stale")
        assert s["audit_status"] == "pass"
        assert s["proposal_source"] == "regenerated"
        assert s["proposal_mismatch_warning"] is True
        # the valid splits are NOT flagged as unexpected just because the CSV
        # disagrees with the reconstructed policy.
        assert s["unexpected_split_count"] == 0

    def test_original_proposals_unchanged_behaviour(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        # a CSV that MATCHES the reconstructed policy → proposal_source=original.
        good = tmp_path / "good.csv"
        rows = propose.build_proposals(records=records, classifications=classes,
                                       names={})
        with good.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=_PROP_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in _PROP_COLUMNS})
        rc = _audit(tmp_path, "good", _Store(records, classes),
                    "--proposals", str(good))
        assert rc == 0
        s = _audit_json(tmp_path, "good")
        assert s["proposal_source"] == "original"
        assert s["proposal_mismatch_warning"] is False
        assert s["audit_status"] == "pass"


# ---------------------------------------------------------------------------
# Part E — real anomalies still fail under reconstruction.
# ---------------------------------------------------------------------------
class TestRealAnomaliesStillFail:
    def test_unexpected_split_on_composite(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        records += [_split(ids[2], "plant_protein_pct", "5"),
                    _split(ids[2], "animal_protein_pct", "5")]
        rc = _audit(tmp_path, "unx", _Store(records, classes))
        assert rc == 2
        assert _audit_json(tmp_path, "unx")["unexpected_split_count"] == 1

    def test_duplicate_split(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        records.append(_split(ids[0], "plant_protein_pct", "0"))
        rc = _audit(tmp_path, "dup", _Store(records, classes))
        assert rc == 2
        assert _audit_json(tmp_path, "dup")["duplicate_split_count"] == 1

    def test_broken_pair(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        records = [r for r in records
                   if not (r.product_id == ids[0]
                           and r.nutrient == "animal_protein_pct")]
        rc = _audit(tmp_path, "brk", _Store(records, classes))
        assert rc == 2
        s = _audit_json(tmp_path, "brk")
        assert s["missing_split_count"] == 1
        assert s["audit_status"] == "fail"

    def test_sum_mismatch(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        for r in records:
            if r.product_id == ids[1] and r.nutrient == "animal_protein_pct":
                r.enriched_value = Decimal("5")  # 6.8 + 5 != 6.8
        rc = _audit(tmp_path, "sum", _Store(records, classes))
        assert rc == 2
        assert _audit_json(tmp_path, "sum")["sum_mismatch_count"] == 1

    def test_bad_tags_and_metadata(self, tmp_path) -> None:
        ids, classes, records = _scenario()
        for r in records:
            if r.product_id == ids[0] and r.nutrient == "plant_protein_pct":
                r.match_method = "deterministic"
            if r.product_id == ids[0] and r.nutrient == "animal_protein_pct":
                r.unit = "mg"
                r.source_metadata = None
        rc = _audit(tmp_path, "bad", _Store(records, classes))
        assert rc == 2
        s = _audit_json(tmp_path, "bad")
        assert s["invalid_match_method_count"] == 1
        assert s["invalid_unit_count"] == 1
        assert s["metadata_missing_count"] == 1

    def test_not_applied_is_warn_not_fail(self, tmp_path) -> None:
        # A would_split product with NEITHER half is "not applied yet" → warn.
        ids, classes, records = _scenario()
        records = [r for r in records
                   if not (r.product_id == ids[1]
                           and r.nutrient in ("plant_protein_pct",
                                              "animal_protein_pct"))]
        rc = _audit(tmp_path, "na", _Store(records, classes))
        assert rc == 1
        s = _audit_json(tmp_path, "na")
        assert s["audit_status"] == "warn"
        assert s["not_applied_split_count"] == 1
        assert s["missing_split_count"] == 0


# ---------------------------------------------------------------------------
# Safety.
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

    def test_routes_clean(self) -> None:
        api_dir = Path(audit.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "audit_nevo_v2_protein_split" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
