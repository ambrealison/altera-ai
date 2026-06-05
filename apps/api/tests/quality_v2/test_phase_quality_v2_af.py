"""Phase Quality-V2-AF — batch review package + correction-loop validator.

Consolidates the project batch review buckets into one reviewer CSV, then
validates filled decisions into approved / gold / alias-rule candidates. No apply
plan; no DB writes; V1 default; embeddings off; routes clean.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from altera_api.classification_v2 import build_nevo_v2_batch_review_package as build
from altera_api.classification_v2 import (
    validate_nevo_v2_batch_review_package as validate,
)

_PROJECT_COLS = [
    "product_id", "canonical_product_key", "representative_product_name",
    "product_name", "brand", "category", "duplicate_count", "v2_outcome",
    "safety_action", "batch_nevo_code", "batch_nevo_name", "protein_g_per_100g",
    "confidence", "match_type", "top_5_candidate_names", "top_5_candidate_codes",
    "top_5_similarities", "rejection_summary", "review_priority",
    "suggested_action", "existing_v2_total_record", "existing_v2_split_record",
    "existing_v2_nevo_code", "existing_v2_nevo_name", "batch_matches_existing_v2",
]
_DIFF_COLS = [
    "product_name", "existing_v2_nevo_code", "existing_v2_nevo_name",
    "batch_nevo_code", "batch_nevo_name", "safety_action", "suggested_action",
    "confidence", "top_5_candidate_names", "diff_bucket",
]
_PID, _RUN = "proj1", "RUN"


def _prow(name, action, outcome, *, code="N1", fn="Food", top="A | B"):
    return dict(
        product_id=name + "-id", canonical_product_key=name + "-k",
        representative_product_name=name, product_name=name, brand="B",
        category="C", duplicate_count="1", v2_outcome=outcome,
        safety_action=action, batch_nevo_code=code, batch_nevo_name=fn,
        protein_g_per_100g="", confidence="0.9", match_type="concept",
        top_5_candidate_names=top, top_5_candidate_codes="N1 | N2",
        top_5_similarities="0.9 | 0.8", rejection_summary="",
        review_priority="P1", suggested_action="x",
        existing_v2_total_record="False", existing_v2_split_record="False",
        existing_v2_nevo_code="", existing_v2_nevo_name="",
        batch_matches_existing_v2="unknown")


def _write_bucket(tmp_path, slug, rows, cols=_PROJECT_COLS):
    p = tmp_path / f"nevo_v2_project_batch_{slug}_{_PID}_{_RUN}.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def _scenario(tmp_path, *, with_policy=False):
    _write_bucket(tmp_path, "safety_downgrade", [
        _prow("Lentilles Cuites", "skip_state_mismatch", "auto_accept"),
        _prow("Riz Thai", "skip_proxy_too_broad", "auto_accept")])
    _write_bucket(tmp_path, "needs_review", [
        _prow("Huile Colza", "route_to_review", "review_required",
              code="606", fn="Oil Becel Blend")])
    _write_bucket(tmp_path, "no_match", [
        _prow("Mystere", "skip_no_match", "no_match", code="", fn="",
              top="A | B"),
        _prow("Truc", "skip_no_match", "no_match", code="", fn="", top="")])
    _write_bucket(tmp_path, "auto_ready", [
        _prow("Choc", "would_enrich", "auto_accept")])
    if with_policy:
        _write_bucket(tmp_path, "policy_excluded", [
            _prow("Liquide Vaisselle", "skip_no_match", "policy_excluded",
                  code="", fn="")])
    diff = tmp_path / f"nevo_v2_project_batch_existing_v2_diffs_{_PID}_{_RUN}.csv"
    with diff.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_DIFF_COLS)
        w.writeheader()
        w.writerow(dict(
            product_name="Huile Colza", existing_v2_nevo_code="5041",
            existing_v2_nevo_name="Oil vegetable av", batch_nevo_code="606",
            batch_nevo_name="Oil Becel Blend", safety_action="route_to_review",
            suggested_action="x", confidence="0.9", top_5_candidate_names="A",
            diff_bucket="safety_downgraded_current_batch"))


def _run_build(tmp_path, *extra):
    return build.main(["--project-id", _PID, "--output-dir", str(tmp_path),
                       *extra])


def _package_rows(tmp_path):
    return list(csv.DictReader(
        (tmp_path / f"nevo_v2_batch_review_package_{_PID}_{_RUN}.csv").open()))


# ---------------------------------------------------------------------------
# Part A/B/C — builder.
# ---------------------------------------------------------------------------
class TestBuilder:
    def test_auto_discovers_and_includes_buckets(self, tmp_path) -> None:
        _scenario(tmp_path)
        rc = _run_build(tmp_path)
        assert rc == 0
        rows = _package_rows(tmp_path)
        by_source = {}
        for r in rows:
            by_source[r["review_source"]] = by_source.get(r["review_source"], 0) + 1
        assert by_source == {"safety_downgrade": 2, "needs_review": 1,
                             "no_match": 2, "existing_v2_diff": 1}
        assert len(rows) == 6

    def test_excludes_auto_ready_and_policy_by_default(self, tmp_path) -> None:
        _scenario(tmp_path, with_policy=True)
        _run_build(tmp_path)
        rows = _package_rows(tmp_path)
        assert all(r["product_name"] != "Choc" for r in rows)  # auto_ready out
        assert all(r["review_source"] != "policy_excluded" for r in rows)

    def test_include_policy_excluded(self, tmp_path) -> None:
        _scenario(tmp_path, with_policy=True)
        _run_build(tmp_path, "--include-policy-excluded")
        rows = _package_rows(tmp_path)
        assert any(r["review_source"] == "policy_excluded" for r in rows)

    def test_priority_assignment(self, tmp_path) -> None:
        _scenario(tmp_path)
        _run_build(tmp_path)
        rows = _package_rows(tmp_path)
        prio = {(r["product_name"], r["review_source"]): r["review_priority"]
                for r in rows}
        assert prio[("Lentilles Cuites", "safety_downgrade")] == "P1"
        assert prio[("Huile Colza", "needs_review")] == "P1"
        assert prio[("Huile Colza", "existing_v2_diff")] == "P1"
        assert prio[("Mystere", "no_match")] == "P2"   # has candidates
        assert prio[("Truc", "no_match")] == "P3"      # no candidates


# ---------------------------------------------------------------------------
# Part D/E — validator rules.
# ---------------------------------------------------------------------------
def _vrow(**kw):
    base = dict(manual_decision="", reviewer_notes="", review_priority="P1",
                review_source="safety_downgrade", safety_action="would_enrich",
                diff_bucket="", approved_nevo_code="", approved_nevo_name="",
                approved_protein_g_per_100g="", batch_nevo_code="N1",
                batch_nevo_name="Food", existing_v2_nevo_code="",
                existing_v2_nevo_name="")
    base.update(kw)
    return base


class TestValidatorRules:
    def test_approve_existing_candidate_ok(self) -> None:
        e, _ = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate",
            safety_action="would_enrich", review_source="needs_review"))
        assert e == []

    def test_approve_existing_v2_requires_code_name(self) -> None:
        e, _ = validate.validate_row(_vrow(
            manual_decision="approve_existing_v2", review_source="existing_v2_diff"))
        assert any("existing_v2" in m for m in e)
        e2, _ = validate.validate_row(_vrow(
            manual_decision="approve_existing_v2", review_source="existing_v2_diff",
            existing_v2_nevo_code="5041", existing_v2_nevo_name="Oil"))
        assert e2 == []

    def test_replace_requires_code_name(self) -> None:
        e, _ = validate.validate_row(_vrow(manual_decision="replace",
                                           approved_nevo_code="N9"))
        assert any("replace requires" in m for m in e)

    def test_safety_downgrade_approve_warns_without_override(self) -> None:
        _e, w = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate",
            review_source="safety_downgrade", safety_action="skip_state_mismatch"))
        assert any("OVERRIDE_SAFE_STATE" in m for m in w)
        _e2, w2 = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate",
            review_source="safety_downgrade", safety_action="skip_state_mismatch",
            reviewer_notes="OVERRIDE_SAFE_STATE checked"))
        assert w2 == []

    def test_p0_approve_blocked_without_override(self) -> None:
        e, _ = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate", review_priority="P0",
            review_source="true_high_risk"))
        assert any("P0" in m for m in e)
        e2, _ = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate", review_priority="P0",
            review_source="true_high_risk", reviewer_notes="OVERRIDE ok"))
        assert e2 == []

    def test_existing_v2_diff_safety_downgraded_warns(self) -> None:
        _e, w = validate.validate_row(_vrow(
            manual_decision="approve_existing_candidate",
            review_source="existing_v2_diff",
            diff_bucket="safety_downgraded_current_batch"))
        assert w

    def test_pending_is_not_error(self) -> None:
        e, w = validate.validate_row(_vrow(manual_decision=""))
        assert e == [] and w == []

    def test_bad_protein_errors(self) -> None:
        e, _ = validate.validate_row(_vrow(manual_decision="replace",
                                           approved_nevo_code="N9",
                                           approved_nevo_name="X",
                                           approved_protein_g_per_100g="abc"))
        assert any("numeric" in m for m in e)


# ---------------------------------------------------------------------------
# Part E/F — end-to-end validation outputs.
# ---------------------------------------------------------------------------
class TestValidatorEndToEnd:
    def _filled_package(self, tmp_path):
        _scenario(tmp_path)
        _run_build(tmp_path)
        rows = _package_rows(tmp_path)
        for r in rows:
            if r["product_name"] == "Lentilles Cuites":
                r["manual_decision"] = "approve_existing_candidate"
                r["reviewer_notes"] = "OVERRIDE_SAFE_STATE ok"
            elif r["product_name"] == "Mystere":
                r["manual_decision"] = "replace"
                r["approved_nevo_code"] = "N9"
                r["approved_nevo_name"] = "Picked"
                r["alias_candidate"] = "mystere->picked"
            elif (r["product_name"] == "Huile Colza"
                  and r["review_source"] == "existing_v2_diff"):
                r["manual_decision"] = "approve_existing_v2"
            # Riz Thai, Truc, Huile needs_review stay pending.
        pkg = tmp_path / f"nevo_v2_batch_review_package_{_PID}_{_RUN}.csv"
        with pkg.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
        return pkg

    def test_validate_writes_all_outputs(self, tmp_path) -> None:
        pkg = self._filled_package(tmp_path)
        rc = validate.main(["--input", str(pkg), "--output-dir", str(tmp_path),
                            "--project-id", _PID])
        assert rc == 0
        suffix = f"{_PID}_{_RUN}"
        for name in ("validation_summary", "errors", "warnings",
                     "approved_candidates", "alias_rule_candidates"):
            ext = "json" if name == "validation_summary" else "csv"
            assert (tmp_path
                    / f"nevo_v2_batch_review_{name}_{suffix}.{ext}").exists()
        assert (tmp_path
                / f"nevo_v2_batch_review_gold_candidates_{suffix}.json").exists()

        s = json.loads((tmp_path
                        / f"nevo_v2_batch_review_validation_summary_{suffix}.json"
                        ).read_text())
        assert s["error_count"] == 0
        assert s["pending_count"] == 3  # Riz, Truc, Huile(needs_review)
        assert s["approved_candidate_count"] == 3
        assert s["alias_rule_candidate_count"] == 1
        assert s["recommendation"] == "review_incomplete"  # pending remain

    def test_approved_effective_sources(self, tmp_path) -> None:
        pkg = self._filled_package(tmp_path)
        validate.main(["--input", str(pkg), "--output-dir", str(tmp_path),
                       "--project-id", _PID])
        approved = list(csv.DictReader((
            tmp_path / f"nevo_v2_batch_review_approved_candidates_{_PID}_{_RUN}.csv"
        ).open()))
        by_name = {a["product_name"]: a for a in approved}
        assert by_name["Lentilles Cuites"]["source"] == "batch_candidate"
        assert by_name["Lentilles Cuites"]["effective_nevo_code"] == "N1"
        assert by_name["Mystere"]["source"] == "replacement"
        assert by_name["Mystere"]["effective_nevo_code"] == "N9"
        assert by_name["Huile Colza"]["source"] == "existing_v2"
        assert by_name["Huile Colza"]["effective_nevo_code"] == "5041"

    def test_gold_and_alias_candidates(self, tmp_path) -> None:
        pkg = self._filled_package(tmp_path)
        validate.main(["--input", str(pkg), "--output-dir", str(tmp_path),
                       "--project-id", _PID])
        gold = json.loads((
            tmp_path / f"nevo_v2_batch_review_gold_candidates_{_PID}_{_RUN}.json"
        ).read_text())["candidates"]
        assert len(gold) == 3  # the 3 non-pending decisions
        alias = list(csv.DictReader((
            tmp_path / f"nevo_v2_batch_review_alias_rule_candidates_{_PID}_{_RUN}.csv"
        ).open()))
        assert len(alias) == 1
        assert alias[0]["status"] == "proposed"


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_clis_write_no_db(self) -> None:
        for mod in (build, validate):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "get_store" not in src
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

    def test_routes_clean(self) -> None:
        api_dir = Path(build.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "build_nevo_v2_batch_review_package" in p.read_text(encoding="utf-8")
            or "validate_nevo_v2_batch_review_package" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
