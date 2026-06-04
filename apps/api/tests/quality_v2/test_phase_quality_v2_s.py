"""Phase Quality-V2-S — validate a FILLED NEVO V2 review package, no DB writes.

Read-only offline validation of a reviewer-filled review package CSV (XLSX
optional, only when openpyxl is installed). Produces summary / errors /
warnings / approved-candidates artifacts and a recommendation. No DB writes,
no routes, no Supabase, V2 not activated.
"""

from __future__ import annotations

import builtins
import csv
import json
from pathlib import Path

import pytest

from altera_api.classification_v2 import (
    validate_nevo_v2_review_package as validator,
)

_COLUMNS = [
    "review_bucket", "product_id", "product_name", "matcher_outcome",
    "nevo_code", "nevo_food_name", "enriched_protein_g_per_100g",
    "nutrition_safety_action", "manual_decision", "reviewer_notes",
    "approved_nevo_code", "approved_nevo_name", "approved_protein_g_per_100g",
    "review_priority", "suggested_action", "top_5_candidates",
]


def _row(**kw):
    base = {c: "" for c in _COLUMNS}
    base.update(
        product_id="p", product_name="X", matcher_outcome="match",
        nevo_code="N1", nevo_food_name="Food",
        enriched_protein_g_per_100g="5.0",
        nutrition_safety_action="would_enrich", review_priority="P1",
        suggested_action="approve_auto_candidate",
    )
    base.update(kw)
    return base


def _write_package(path: Path, rows) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ---------------------------------------------------------------------------
# Part A/B — per-row validation rules.
# ---------------------------------------------------------------------------
class TestValidateRow:
    def test_valid_approve_auto_ready(self) -> None:
        errors, warnings = validator.validate_row(_row(manual_decision="approve"))
        assert errors == [] and warnings == []

    def test_replace_requires_code_and_name(self) -> None:
        errors, _ = validator.validate_row(
            _row(manual_decision="replace", approved_nevo_code="N9")
        )
        assert any("replace requires" in e for e in errors)
        errors2, _ = validator.validate_row(
            _row(manual_decision="replace", approved_nevo_code="N9",
                 approved_nevo_name="Other")
        )
        assert errors2 == []

    def test_no_match_approve_without_replacement_errors(self) -> None:
        errors, _ = validator.validate_row(_row(
            manual_decision="approve", matcher_outcome="no_match",
            nutrition_safety_action="skip_no_match",
            suggested_action="review_no_match", nevo_code="",
            nevo_food_name="", enriched_protein_g_per_100g="",
            top_5_candidates="a | b",
        ))
        assert any("no_match" in e for e in errors)

    def test_no_match_approve_with_code_ok(self) -> None:
        errors, _ = validator.validate_row(_row(
            manual_decision="approve", matcher_outcome="no_match",
            nutrition_safety_action="skip_no_match",
            suggested_action="review_no_match", nevo_code="",
            nevo_food_name="", enriched_protein_g_per_100g="",
            approved_nevo_code="N5", approved_nevo_name="Picked",
            approved_protein_g_per_100g="8",
        ))
        assert errors == []

    def test_p0_approve_errors(self) -> None:
        errors, _ = validator.validate_row(_row(
            manual_decision="approve", review_priority="P0",
            suggested_action="reject_non_food",
        ))
        assert any("P0" in e for e in errors)

    def test_state_mismatch_approve_warns(self) -> None:
        errors, warnings = validator.validate_row(_row(
            manual_decision="approve", suggested_action="review_state_mismatch",
            nutrition_safety_action="skip_state_mismatch", nevo_code="N2",
            nevo_food_name="Pasta boiled", enriched_protein_g_per_100g="5",
        ))
        assert errors == []
        assert any("state-mismatch" in w for w in warnings)

    def test_proxy_too_broad_approve_warns(self) -> None:
        errors, warnings = validator.validate_row(_row(
            manual_decision="approve", suggested_action="review_proxy_too_broad",
            nutrition_safety_action="skip_proxy_too_broad", nevo_code="N3",
            nevo_food_name="Apple syrup", enriched_protein_g_per_100g="0.1",
        ))
        assert errors == []
        assert any("proxy" in w for w in warnings)

    def test_generic_proxy_route_to_review_approve_warns(self) -> None:
        errors, warnings = validator.validate_row(_row(
            manual_decision="approve", suggested_action="review_generic_proxy",
            nutrition_safety_action="route_to_review", nevo_code="N4",
            nevo_food_name="Crisps unflavoured", enriched_protein_g_per_100g="6",
        ))
        assert errors == []
        assert warnings

    def test_non_food_approve_without_override_errors(self) -> None:
        errors, _ = validator.validate_row(_row(
            manual_decision="approve", review_priority="P3",
            suggested_action="reject_non_food",
            nutrition_safety_action="skip_no_match", matcher_outcome="no_match",
            nevo_code="", nevo_food_name="", enriched_protein_g_per_100g="",
        ))
        assert any("OVERRIDE" in e for e in errors)

    def test_non_food_approve_with_override_warns(self) -> None:
        errors, warnings = validator.validate_row(_row(
            manual_decision="approve", review_priority="P3",
            suggested_action="reject_non_food",
            nutrition_safety_action="skip_no_match", matcher_outcome="no_match",
            nevo_code="", nevo_food_name="", enriched_protein_g_per_100g="",
            reviewer_notes="OVERRIDE: confirmed edible product",
            approved_nevo_code="N7", approved_nevo_name="Real food",
            approved_protein_g_per_100g="4",
        ))
        assert errors == []
        assert any("OVERRIDE" in w for w in warnings)

    def test_approved_protein_must_be_numeric(self) -> None:
        errors, _ = validator.validate_row(_row(
            manual_decision="replace", approved_nevo_code="N9",
            approved_nevo_name="Other", approved_protein_g_per_100g="abc",
        ))
        assert any("numeric" in e for e in errors)

    def test_blank_is_pending_and_accepted(self) -> None:
        errors, warnings = validator.validate_row(_row(manual_decision=""))
        assert errors == [] and warnings == []

    def test_unknown_decision_errors(self) -> None:
        errors, _ = validator.validate_row(_row(manual_decision="maybe"))
        assert any("invalid manual_decision" in e for e in errors)


# ---------------------------------------------------------------------------
# Part C — whole-package validation + artifacts (CSV input is primary).
# ---------------------------------------------------------------------------
class TestPackageValidation:
    def test_csv_input_produces_artifacts_and_recommendation(self, tmp_path) -> None:
        path = tmp_path / "nevo_v2_enrich_review_package_proj-7.csv"
        _write_package(path, [
            _row(product_id="1", product_name="Chocolat",
                 manual_decision="approve"),
            _row(product_id="2", product_name="Pates",
                 manual_decision="approve",
                 suggested_action="review_state_mismatch",
                 nutrition_safety_action="skip_state_mismatch",
                 nevo_code="N2", nevo_food_name="Pasta boiled",
                 enriched_protein_g_per_100g="5"),
            _row(product_id="3", product_name="Truc", manual_decision="replace",
                 matcher_outcome="no_match", nevo_code="", nevo_food_name="",
                 enriched_protein_g_per_100g="", approved_nevo_code="N3",
                 approved_nevo_name="Picked", approved_protein_g_per_100g="9",
                 suggested_action="review_no_match",
                 nutrition_safety_action="skip_no_match"),
            _row(product_id="4", product_name="Liquide Vaisselle",
                 manual_decision="reject", review_priority="P3",
                 suggested_action="reject_non_food", matcher_outcome="no_match",
                 nevo_code="", nevo_food_name="",
                 enriched_protein_g_per_100g="",
                 nutrition_safety_action="skip_no_match"),
        ])
        rc = validator.main(["--input", str(path), "--output-dir", str(tmp_path)])
        assert rc == 0
        summary = json.loads(
            (tmp_path / "nevo_v2_review_validation_summary_proj-7.json").read_text()
        )
        assert summary["project_id"] == "proj-7"
        assert summary["total_rows"] == 4
        assert summary["approved_count"] == 2
        assert summary["replace_count"] == 1
        assert summary["rejected_count"] == 1
        assert summary["error_count"] == 0
        assert summary["warning_count"] == 1  # state-mismatch approve
        assert summary["apply_ready_count"] == 3  # 2 approve + 1 replace
        assert summary["blocked_count"] == 0
        assert summary["recommendation"] == "ready_for_apply_planning"
        # artifacts exist.
        for name in ("errors", "warnings"):
            assert (tmp_path / f"nevo_v2_review_validation_{name}_proj-7.csv").exists()
        approved = list(csv.DictReader(
            (tmp_path / "nevo_v2_review_approved_candidates_proj-7.csv").open()
        ))
        assert len(approved) == 3
        assert {a["source"] for a in approved} == {"existing", "replacement"}

    def test_blocked_by_errors_recommendation(self, tmp_path) -> None:
        path = tmp_path / "nevo_v2_enrich_review_package_proj-bad.csv"
        _write_package(path, [
            _row(product_id="1", product_name="Box",
                 manual_decision="approve", matcher_outcome="no_match",
                 nevo_code="", nevo_food_name="",
                 enriched_protein_g_per_100g="",
                 nutrition_safety_action="skip_no_match",
                 suggested_action="review_no_match"),
        ])
        rc = validator.main(["--input", str(path), "--output-dir", str(tmp_path)])
        assert rc == 0
        summary = json.loads(
            (tmp_path / "nevo_v2_review_validation_summary_proj-bad.json").read_text()
        )
        assert summary["error_count"] >= 1
        assert summary["blocked_count"] == 1
        assert summary["apply_ready_count"] == 0
        assert summary["recommendation"] == "blocked_by_errors"
        errors = list(csv.DictReader(
            (tmp_path / "nevo_v2_review_validation_errors_proj-bad.csv").open()
        ))
        assert errors and errors[0]["product_id"] == "1"

    def test_review_incomplete_when_pending(self, tmp_path) -> None:
        path = tmp_path / "nevo_v2_enrich_review_package_proj-p.csv"
        _write_package(path, [
            _row(product_id="1", manual_decision="approve"),
            _row(product_id="2", manual_decision=""),  # pending
        ])
        validator.main(["--input", str(path), "--output-dir", str(tmp_path)])
        summary = json.loads(
            (tmp_path / "nevo_v2_review_validation_summary_proj-p.json").read_text()
        )
        assert summary["pending_count"] == 1
        assert summary["recommendation"] == "review_incomplete"

    def test_missing_input_fails_clearly(self, tmp_path, capsys) -> None:
        rc = validator.main(
            ["--input", str(tmp_path / "nope.csv"), "--output-dir", str(tmp_path)]
        )
        assert rc == 2
        assert "not found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# XLSX support is optional — clear error when openpyxl is missing.
# ---------------------------------------------------------------------------
class TestXlsxOptional:
    def test_xlsx_without_openpyxl_fails_clearly(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        xlsx = tmp_path / "nevo_v2_enrich_review_package_proj-x.xlsx"
        xlsx.write_bytes(b"not a real workbook")  # never parsed: import fails first
        real_import = builtins.__import__

        def _no_openpyxl(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("simulated: openpyxl unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _no_openpyxl)
        rc = validator.main(["--input", str(xlsx), "--output-dir", str(tmp_path)])
        assert rc == 2
        out = capsys.readouterr().out
        assert "openpyxl" in out and "csv" in out.lower()

    def test_xlsx_round_trip_when_openpyxl_present(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        path = tmp_path / "nevo_v2_enrich_review_package_proj-xl.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "Summary"
        wb.active.append(["metric", "value"])  # meta sheet, ignored
        ws = wb.create_sheet("Auto_Ready")
        ws.append(_COLUMNS)
        ws.append([
            "auto_ready", "1", "Chocolat", "match", "N1", "Chocolate dark",
            "7", "would_enrich", "approve", "", "", "", "", "P1",
            "approve_auto_candidate", "",
        ])
        instr = wb.create_sheet("Instructions")
        instr.append(["how to use"])
        wb.save(path)

        rc = validator.main(["--input", str(path), "--output-dir", str(tmp_path)])
        assert rc == 0
        summary = json.loads(
            (tmp_path / "nevo_v2_review_validation_summary_proj-xl.json").read_text()
        )
        assert summary["total_rows"] == 1
        assert summary["approved_count"] == 1
        assert summary["apply_ready_count"] == 1


# ---------------------------------------------------------------------------
# Safety — read-only, no DB, defaults unchanged, routes clean.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_validator_does_not_touch_db(self) -> None:
        src = Path(validator.__file__).read_text(encoding="utf-8")
        assert "get_store" not in src
        assert "store_factory" not in src
        assert "add_enrichment_record" not in src

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

    def test_routes_do_not_import_validator(self) -> None:
        api_dir = Path(validator.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "validate_nevo_v2_review_package" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
