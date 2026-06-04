"""Phase Quality-V2-N — NEVO V2 shadow readiness artifact.

Turns the read-only shadow comparison into a decision artifact: a summary
JSON + filtered CSVs + a recommendation (keep_off / internal_shadow_ok /
admin_opt_in_candidate). All offline; V1 default; embeddings off; no route
imports V2/embeddings; strictly read-only (no DB writes).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import compare_nevo_v1_v2 as cli
from altera_api.classification_v2.compare_nevo_v1_v2 import (
    _recommendation,
    build_summary,
    write_filtered_csvs,
    write_summary_json,
)


def _row(agreement, risk, *, v2_outcome="match", notes=""):
    return {
        "product_id": str(uuid4()), "product_name": "x", "retailer_category": "",
        "retailer_subcategory": "", "ingredients_present": False,
        "v1_outcome": "no_match", "v1_reference_name": "", "v1_reference_code": "",
        "v1_confidence": "", "v1_notes": "", "v2_outcome": v2_outcome,
        "v2_reference_name": "Tofu", "v2_reference_code": "1", "v2_confidence": 0.96,
        "v2_match_type": "embedding_plus_rule", "v2_review_required": False,
        "v2_top_5_candidates": "", "v2_rejection_reasons_summary": "",
        "agreement_bucket": agreement, "risk_bucket": risk, "notes": notes,
    }


# ---------------------------------------------------------------------------
# Recommendation logic.
# ---------------------------------------------------------------------------
class TestRecommendation:
    def test_high_risk_forces_keep_off(self) -> None:
        rec, reasons = _recommendation(
            product_count=100, potential_high_risk=1, v2_better=20,
            v2_auto_accept=66, threshold="auto",
        )
        assert rec == "keep_off"
        assert any("high-risk" in r for r in reasons)

    def test_strong_run_is_admin_opt_in(self) -> None:
        rec, _ = _recommendation(
            product_count=100, potential_high_risk=0, v2_better=25,
            v2_auto_accept=66, threshold="auto",
        )
        assert rec == "admin_opt_in_candidate"

    def test_small_corpus_is_internal_shadow_ok(self) -> None:
        rec, reasons = _recommendation(
            product_count=40, potential_high_risk=0, v2_better=20,
            v2_auto_accept=40, threshold="auto",
        )
        assert rec == "internal_shadow_ok"
        assert any("<50" in r for r in reasons)

    def test_low_auto_accept_is_internal_shadow_ok(self) -> None:
        rec, _ = _recommendation(
            product_count=100, potential_high_risk=0, v2_better=5,
            v2_auto_accept=40, threshold="auto",
        )
        assert rec == "internal_shadow_ok"

    def test_no_v2_wins_is_internal_shadow_ok(self) -> None:
        rec, _ = _recommendation(
            product_count=100, potential_high_risk=0, v2_better=0,
            v2_auto_accept=60, threshold="auto",
        )
        assert rec == "internal_shadow_ok"

    def test_conservative_threshold_raises_bar(self) -> None:
        # 55% auto-accept: admin under 'auto' (≥50%), not under 'conservative'
        # (≥60%).
        assert _recommendation(
            product_count=100, potential_high_risk=0, v2_better=10,
            v2_auto_accept=55, threshold="auto",
        )[0] == "admin_opt_in_candidate"
        assert _recommendation(
            product_count=100, potential_high_risk=0, v2_better=10,
            v2_auto_accept=55, threshold="conservative",
        )[0] == "internal_shadow_ok"


# ---------------------------------------------------------------------------
# build_summary structure + counts.
# ---------------------------------------------------------------------------
class TestBuildSummary:
    def _rows(self):
        return [
            _row("same_code", "safe_agreement"),
            _row("same_concept", "v2_more_specific"),
            *[_row("v2_only", "v2_better_than_v1", notes="V2 own-concept match")
              for _ in range(60)],
            _row("v1_only", "manual_inspection_needed", v2_outcome="no_match",
                 notes="V1 likely false positive (...)"),
            _row("disagreement_needs_review", "v2_potential_false_positive"),
        ]

    def test_summary_has_all_keys(self) -> None:
        s = build_summary(
            self._rows(), project_id="p1", top_k=20, provider="fake",
            model="voyage-4-lite", generated_at="2026-06-04T00:00:00+00:00",
        )
        for key in (
            "project_id", "product_count", "top_k", "provider", "model",
            "generated_at", "agreement_bucket_counts", "risk_bucket_counts",
            "v2_auto_accept_count", "v2_review_required_count",
            "v2_better_than_v1_count", "v1_likely_false_positive_count",
            "potential_high_risk_count", "recommendation",
            "recommendation_threshold", "recommendation_reasons",
            "admin_opt_in_gates",
        ):
            assert key in s

    def test_counts_are_correct(self) -> None:
        s = build_summary(
            self._rows(), project_id="p1", top_k=20, provider="fake",
            model="m", generated_at=None,
        )
        assert s["product_count"] == 64
        assert s["v2_better_than_v1_count"] == 60
        assert s["v1_likely_false_positive_count"] == 1
        # potential high risk counts ONLY v2_potential_false_positive (the
        # v1_only / manual-inspection row is a coverage gap, not a V2 risk).
        assert s["potential_high_risk_count"] == 1
        assert s["agreement_bucket_counts"]["v2_only"] == 60
        # 1 high-risk → keep_off.
        assert s["recommendation"] == "keep_off"

    def test_admin_gates_reflect_state(self) -> None:
        rows = [_row("v2_only", "v2_better_than_v1") for _ in range(60)]
        s = build_summary(
            rows, project_id="p1", top_k=20, provider="voyage", model="m",
            generated_at=None,
        )
        assert s["recommendation"] == "admin_opt_in_candidate"
        g = s["admin_opt_in_gates"]
        assert g["potential_high_risk_zero"] is True
        assert g["v2_better_than_v1_positive"] is True
        assert g["v1_default_unchanged"] is True
        assert g["embeddings_cli_only"] is True


# ---------------------------------------------------------------------------
# Artifact writers.
# ---------------------------------------------------------------------------
class TestWriters:
    def test_summary_json_is_valid(self, tmp_path) -> None:
        s = build_summary(
            [_row("v2_only", "v2_better_than_v1")], project_id="p1", top_k=20,
            provider="fake", model="m", generated_at=None,
        )
        path = tmp_path / "s.json"
        write_summary_json(path, s)
        loaded = json.loads(path.read_text())
        assert loaded["recommendation"] in (
            "keep_off", "internal_shadow_ok", "admin_opt_in_candidate"
        )

    def test_filtered_csvs_written(self, tmp_path) -> None:
        rows = [
            _row("v2_only", "v2_better_than_v1"),
            _row("disagreement_needs_review", "v2_review_only", v2_outcome="review"),
            _row("disagreement_needs_review", "v2_potential_false_positive"),
        ]
        counts = write_filtered_csvs(tmp_path, "p1", rows)
        assert counts["nevo_v2_better_than_v1_p1.csv"] == 1
        assert counts["nevo_v2_review_only_p1.csv"] == 1
        assert counts["nevo_v2_high_risk_p1.csv"] == 1
        for fname in counts:
            assert (tmp_path / fname).exists()


# ---------------------------------------------------------------------------
# End-to-end CLI — read-only; artifacts written; flags honoured.
# ---------------------------------------------------------------------------
class _ReadOnlyStore:
    _WRITES = frozenset({"add_enrichment_record", "upsert_pt_classification",
                         "add_product", "add_run"})

    def __init__(self, products):
        self._products = products

    def get_project(self, project_id):
        return object()

    def list_products_for_project(self, project_id):
        return self._products

    def list_nevo_entries(self):
        return []

    def __getattr__(self, name):
        if name in self._WRITES:
            raise AssertionError(f"read-only violation: {name}")
        raise AttributeError(name)


def _product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        retailer_subcategory=None, ingredients_text=None, labels=(),
        pt_fields=object(),
    )


class TestCli:
    def _run(self, tmp_path, *, extra=None):
        store = _ReadOnlyStore([_product("Tofu nature"), _product("Pois chiches")])
        pid = str(uuid4())
        rc = cli.main(
            ["--project-id", pid, "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path), *(extra or [])],
            store=store,
        )
        return rc, pid

    def test_writes_all_artifacts(self, tmp_path, capsys) -> None:
        rc, pid = self._run(tmp_path)
        assert rc == 0
        assert (tmp_path / f"nevo_v1_v2_comparison_{pid}.csv").exists()
        assert (tmp_path / f"nevo_v1_v2_comparison_{pid}.json").exists()
        for stem in ("nevo_v2_better_than_v1", "nevo_v2_review_only",
                     "nevo_v2_high_risk"):
            assert (tmp_path / f"{stem}_{pid}.csv").exists()
        out = capsys.readouterr().out
        assert "RECOMMENDATION:" in out
        assert "no database writes" in out.lower()

    def test_no_write_flags_suppress(self, tmp_path) -> None:
        rc, pid = self._run(
            tmp_path, extra=["--no-write-summary-json", "--no-write-filtered-csvs"]
        )
        assert rc == 0
        assert (tmp_path / f"nevo_v1_v2_comparison_{pid}.csv").exists()
        assert not (tmp_path / f"nevo_v1_v2_comparison_{pid}.json").exists()
        assert not (tmp_path / f"nevo_v2_better_than_v1_{pid}.csv").exists()

    def test_json_recommendation_present(self, tmp_path) -> None:
        _rc, pid = self._run(tmp_path)
        s = json.loads((tmp_path / f"nevo_v1_v2_comparison_{pid}.json").read_text())
        assert s["recommendation"] in (
            "keep_off", "internal_shadow_ok", "admin_opt_in_candidate"
        )
        assert s["project_id"] == pid
        assert s["recommendation_threshold"] == "auto"


# ---------------------------------------------------------------------------
# Hotfix — review-only filtered CSV is keyed on risk_bucket, not v2_outcome.
# A v1_only row (V1 matched, V2 no_match) lands in the v2_review_only risk
# bucket (the no_match decision still carries review_required=True) while its
# v2_outcome is "no_match" — so filtering on outcome wrongly dropped them.
# ---------------------------------------------------------------------------
class TestReviewOnlyFilterHotfix:
    def _rows(self):
        rows = []
        # 25 V2 wins (auto-accept).
        rows += [_row("v2_only", "v2_better_than_v1",
                      notes="V2 own-concept match") for _ in range(25)]
        # 35 exact-code agreements (auto-accept).
        rows += [_row("same_code", "safe_agreement") for _ in range(35)]
        # 15 v1_only rows: V2 produced no match → risk_bucket "v2_review_only"
        # but v2_outcome == "no_match" (the real-run scenario).
        rows += [_row("v1_only", "v2_review_only", v2_outcome="no_match")
                 for _ in range(15)]
        return rows

    def test_summary_counts(self) -> None:
        s = build_summary(
            self._rows(), project_id="p1", top_k=20, provider="voyage",
            model="voyage-4-lite", generated_at=None,
        )
        assert s["risk_bucket_counts"]["v2_review_only"] == 15
        # No row has v2_outcome == "review" → review_required count is 0.
        assert s["v2_review_required_count"] == 0
        assert s["v2_better_than_v1_count"] == 25
        assert s["potential_high_risk_count"] == 0
        assert s["recommendation"] == "admin_opt_in_candidate"

    def test_filtered_csv_row_counts_match_buckets(self, tmp_path) -> None:
        rows = self._rows()
        counts = write_filtered_csvs(tmp_path, "p1", rows)
        # review_only CSV now contains the 15 risk_bucket==v2_review_only rows
        # even though none had v2_outcome == "review".
        assert counts["nevo_v2_review_only_p1.csv"] == 15
        assert counts["nevo_v2_better_than_v1_p1.csv"] == 25
        assert counts["nevo_v2_high_risk_p1.csv"] == 0
        # Console-reported counts == actual written data rows (lines - header).
        for fname, expected in counts.items():
            lines = (tmp_path / fname).read_text().splitlines()
            assert len(lines) - 1 == expected, fname

    def test_high_risk_includes_manual_inspection(self, tmp_path) -> None:
        rows = [
            _row("disagreement_needs_review", "v2_potential_false_positive"),
            _row("v1_only", "manual_inspection_needed", v2_outcome="no_match"),
            _row("v2_only", "v2_better_than_v1"),
        ]
        counts = write_filtered_csvs(tmp_path, "p2", rows)
        assert counts["nevo_v2_high_risk_p2.csv"] == 2  # both risk buckets

    def test_high_risk_header_only_when_zero(self, tmp_path) -> None:
        rows = [_row("v2_only", "v2_better_than_v1") for _ in range(5)]
        counts = write_filtered_csvs(tmp_path, "p3", rows)
        assert counts["nevo_v2_high_risk_p3.csv"] == 0
        assert len((tmp_path / "nevo_v2_high_risk_p3.csv").read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_routes_do_not_import_v2(self) -> None:
        from pathlib import Path

        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
        ]
        assert not offenders

    def test_v1_default_and_embeddings_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from altera_api.classification_v2.nevo_matcher import get_nevo_matcher
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert get_nevo_matcher().version == "v1"
        assert embeddings_enabled() is False
