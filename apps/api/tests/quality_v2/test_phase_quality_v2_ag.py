"""Phase Quality-V2-AG — human-friendly review workbook + normalize/manifest.

The /tmp CSV workflow is unusable for ops, so AG reshapes the machine review
package into a friendly Excel workbook (useful columns first, technical columns
tucked away, priority colours, decision dropdowns) with a CSV + README fallback
when openpyxl is missing — and normalizes a filled file back into the validator
input (the source of truth). No DB writes; V1 default; embeddings off; routes
clean.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import (
    build_nevo_v2_human_review_workbook as B,
)
from altera_api.classification_v2 import (
    normalize_nevo_v2_human_review_workbook as N,
)
from altera_api.classification_v2 import (
    print_nevo_v2_review_artifact_manifest as M,
)
from altera_api.classification_v2 import validate_nevo_v2_batch_review_package
from altera_api.classification_v2.build_nevo_v2_batch_review_package import (
    REVIEW_PACKAGE_COLUMNS,
)

PID = "326c6e1c-46b2-4103-98f1-331afadb721a"


def _pkg_row(**kw):
    row = {c: "" for c in REVIEW_PACKAGE_COLUMNS}
    row.update(project_id=PID)
    row.update(kw)
    return row


_ROWS = [
    _pkg_row(review_source="existing_v2_diff", review_priority="P1",
             product_id="p1", product_name="Huile de Colza Bio 1L",
             category="oil", brand="b", safety_action="route_to_review",
             existing_v2_nevo_code="5041",
             existing_v2_nevo_name="Oil vegetable av",
             batch_nevo_code="606", batch_nevo_name="Oil Becel Blend",
             diff_bucket="safety_downgraded_current_batch"),
    _pkg_row(review_source="safety_downgrade", review_priority="P1",
             product_id="p2", product_name="Riz Basmati Cru", category="rice",
             safety_action="skip_state_mismatch", batch_nevo_code="N-RICE",
             batch_nevo_name="Rice cooked", protein_g_per_100g="2.6"),
    _pkg_row(review_source="needs_review", review_priority="P1",
             product_id="p3", product_name="Muesli Fruits Rouges",
             category="cereal", safety_action="route_to_review",
             batch_nevo_code="N-MUES", batch_nevo_name="Muesli w fruit"),
    _pkg_row(review_source="no_match", review_priority="P2", product_id="p4",
             product_name="Tartiflette Surgelee", category="ready",
             safety_action="skip_no_match",
             top_5_candidate_names="Potato gratin|Cheese dish",
             top_5_candidate_codes="N-1|N-2"),
    _pkg_row(review_source="no_match", review_priority="P3", product_id="p5",
             product_name="Bonbons Acidules", category="candy",
             safety_action="skip_no_match"),
]


def _write_package(out_dir: Path, run: str, rows=_ROWS) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"nevo_v2_batch_review_package_{PID}_{run}.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REVIEW_PACKAGE_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ---------------------------------------------------------------------------
# Part A/B/C/D — workbook builder.
# ---------------------------------------------------------------------------
class TestWorkbookBuilder:
    def test_auto_discovers_newest_review_package(self, tmp_path) -> None:
        old = _write_package(tmp_path, "R1")
        import os
        import time
        # Make R1 clearly older than R2.
        past = time.time() - 100
        os.utime(old, (past, past))
        _write_package(tmp_path, "R2")
        assert B._auto_discover(tmp_path, PID).name.endswith("_R2.csv")

        rc = B.main(["--project-id", PID, "--output-dir", str(tmp_path)])
        assert rc == 0
        summary = json.loads(
            (tmp_path / f"nevo_v2_human_review_summary_{PID}_R2.json"
             ).read_text())
        assert summary["run_id"] == "R2"
        assert summary["review_package_input"].endswith("_R2.csv")

    def test_csv_fallback_when_openpyxl_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(B, "_HAS_OPENPYXL", False)
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        assert summary["xlsx_written"] is False
        paths = summary["output_paths"]
        assert "workbook_xlsx" not in paths
        assert Path(paths["csv_fallback"]).exists()
        assert Path(paths["readme"]).exists()
        assert Path(paths["summary"]).exists()
        # CSV fallback is self-contained: human + technical columns.
        with Path(paths["csv_fallback"]).open(encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        assert header == B.HUMAN_FILE_COLUMNS

    def test_xlsx_has_expected_sheets(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        wb = openpyxl.load_workbook(summary["output_paths"]["workbook_xlsx"])
        assert wb.sheetnames == [
            "Instructions", "Review_All", "P1_Review_First", "Safety_Downgrade",
            "Needs_Review", "No_Match", "Existing_V2_Diffs",
            "Reference_Decisions", "Technical_Raw",
        ]

    def test_human_columns_first_technical_later(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        wb = openpyxl.load_workbook(summary["output_paths"]["workbook_xlsx"])
        header = [c.value for c in wb["Review_All"][1]]
        # Human block first, in the exact Part C order.
        assert header[:len(B.HUMAN_COLUMNS)] == B.HUMAN_COLUMNS
        # Technical columns come strictly after the human block.
        gold_idx = header.index("gold_case_decision")
        for tech in ("project_id", "product_id", "confidence", "diff_bucket"):
            assert header.index(tech) > gold_idx

    def test_technical_raw_sheet_has_raw_columns(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        wb = openpyxl.load_workbook(summary["output_paths"]["workbook_xlsx"])
        raw_header = [c.value for c in wb["Technical_Raw"][1]]
        assert raw_header == REVIEW_PACKAGE_COLUMNS

    def test_p1_review_first_only_p0_p1(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        wb = openpyxl.load_workbook(summary["output_paths"]["workbook_xlsx"])
        ws = wb["P1_Review_First"]
        priorities = {r[0].value for r in ws.iter_rows(min_row=2, max_col=1)}
        assert priorities <= {"P0", "P1"}
        assert priorities == {"P1"}  # the fixture has 3 P1, no P0.

    def test_rows_sorted_by_priority_then_source(self, tmp_path) -> None:
        rows = B.human_rows(_ROWS)
        order = [(r["review_priority"], r["review_source"]) for r in rows]
        # existing_v2_diff sorts before safety_downgrade within P1.
        assert order[0] == ("P1", "existing_v2_diff")
        assert order[-1][0] == "P3"

    def test_dropdowns_added_when_xlsx(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        wb = openpyxl.load_workbook(summary["output_paths"]["workbook_xlsx"])
        dvs = wb["Review_All"].data_validations.dataValidation
        formulas = " ".join(dv.formula1 for dv in dvs)
        assert "approve_existing_candidate" in formulas  # manual_decision
        assert "positive_gold" in formulas               # gold_case_decision

    def test_readme_and_summary_document_vocabulary(self, tmp_path) -> None:
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        readme = Path(summary["output_paths"]["readme"]).read_text()
        for token in ("approve_existing_candidate", "approve_existing_v2",
                      "replace", "reject", "needs_more_info", "out_of_scope",
                      "OVERRIDE_SAFE_STATE", "OVERRIDE_SAFE_PROXY", "OVERRIDE"):
            assert token in readme
        assert summary["manual_decision_values"] == B.MANUAL_DECISIONS
        assert summary["gold_case_decision_values"] == B.GOLD_DECISIONS


# ---------------------------------------------------------------------------
# Part G — normalize back to validator input.
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_csv_passthrough(self, tmp_path) -> None:
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        # Fill a decision in the human CSV fallback.
        csv_path = Path(summary["output_paths"]["csv_fallback"])
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        rows[0]["manual_decision"] = "approve_existing_v2"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=B.HUMAN_FILE_COLUMNS)
            w.writeheader()
            w.writerows(rows)

        res = N.normalize(input_path=csv_path, output_dir=tmp_path,
                          project_id=PID, run_id="RX")
        assert res["rows"] == len(_ROWS)
        assert res["rows_with_decision"] == 1
        out = Path(res["output_path"])
        assert out.name.startswith("nevo_v2_batch_review_package_"
                                   "FILLED_NORMALIZED_")
        with out.open(encoding="utf-8-sig", newline="") as fh:
            norm = list(csv.DictReader(fh))
        assert list(norm[0].keys()) == REVIEW_PACKAGE_COLUMNS
        # Friendly current_batch_* mapped back to canonical batch/nevo columns.
        diff = next(r for r in norm if r["review_source"] == "existing_v2_diff")
        assert diff["batch_nevo_code"] == "606"
        assert diff["nevo_code"] == "606"

    def test_xlsx_normalize_when_openpyxl(self, tmp_path) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        xlsx = Path(summary["output_paths"]["workbook_xlsx"])
        wb = openpyxl.load_workbook(xlsx)
        ws = wb["Review_All"]
        header = [c.value for c in ws[1]]
        col = header.index("manual_decision") + 1
        ws.cell(row=2, column=col).value = "approve_existing_candidate"
        wb.save(xlsx)

        res = N.normalize(input_path=xlsx, output_dir=tmp_path, project_id=PID,
                          run_id="RX")
        assert res["rows"] == len(_ROWS)
        assert res["rows_with_decision"] == 1

    def test_xlsx_fails_clearly_when_openpyxl_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(N, "_HAS_OPENPYXL", False)
        fake = tmp_path / "filled.xlsx"
        fake.write_bytes(b"not really xlsx")
        with pytest.raises(N.NormalizeError, match="export it as CSV"):
            N.normalize(input_path=fake, output_dir=tmp_path, project_id=PID,
                        run_id="RX")

    def test_normalized_output_validates(self, tmp_path) -> None:
        pkg = _write_package(tmp_path, "RX")
        summary = B.build(project_id=PID, review_package=pkg,
                          output_dir=tmp_path)
        csv_path = Path(summary["output_paths"]["csv_fallback"])
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        # An approve that the validator should accept cleanly.
        for r in rows:
            if r["review_source"] == "existing_v2_diff":
                r["manual_decision"] = "approve_existing_v2"
                r["reviewer_notes"] = "confirmed by reviewer"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=B.HUMAN_FILE_COLUMNS)
            w.writeheader()
            w.writerows(rows)

        res = N.normalize(input_path=csv_path, output_dir=tmp_path,
                          project_id=PID, run_id="RX")
        rc = validate_nevo_v2_batch_review_package.main(
            ["--input", res["output_path"], "--output-dir", str(tmp_path),
             "--project-id", PID])
        assert rc == 0
        vsum = json.loads(
            (tmp_path / f"nevo_v2_batch_review_validation_summary_{PID}_RX.json"
             ).read_text())
        assert vsum["error_count"] == 0
        assert vsum["approved_candidate_count"] == 1


# ---------------------------------------------------------------------------
# Part H — manifest.
# ---------------------------------------------------------------------------
class TestManifest:
    def test_manifest_lists_artifacts(self, tmp_path) -> None:
        pkg = _write_package(tmp_path, "RX")
        B.build(project_id=PID, review_package=pkg, output_dir=tmp_path)
        manifest = M.build_manifest(project_id=PID, output_dir=tmp_path)
        assert manifest["review_package"].endswith("_RX.csv")
        assert manifest["readme"] is not None
        lines = M._lines(manifest, project_id=PID)
        text = "\n".join(lines)
        assert "normalize_nevo_v2_human_review_workbook" in text
        assert "validate_nevo_v2_batch_review_package" in text
        # base64 only offered as a last resort.
        assert "LAST RESORT" in text

    def test_manifest_runs_clean(self, tmp_path) -> None:
        rc = M.main(["--project-id", PID, "--output-dir", str(tmp_path)])
        assert rc == 0


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_no_db_writes_in_modules(self) -> None:
        for mod in (B, N, M):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for needle in ("add_enrichment", "update_enrichment", "_store",
                           "get_store", ".insert(", "delete_"):
                assert needle not in src, f"{mod.__name__}:{needle}"

    def test_v1_default_and_embeddings_off(self, monkeypatch) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

    def test_routes_clean(self) -> None:
        api_dir = Path(B.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
