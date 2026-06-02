"""Phase Product-UX-D — guard the guided-report display + WWF Step 1 copy.

There is no JS test runner in this repo, so (as with the shipped-template
test) these checks read the committed frontend source and methodology
docs to lock in the Phase Product-UX-D guarantees:

* the guided result step renders the full report and no longer *silently*
  falls back to the old compact summary — it has explicit loading / error
  states and surfaces the backend error;
* the technical detail link is gated behind ``isAltera`` (hidden from the
  normal client flow);
* the bespoke technical report page formats numbers through the helpers
  (no raw ``8145.02491000 kg`` strings);
* WWF copy states Step 1 product-level scope and that Step 2 is not
  enabled, and NEVO is not presented as a Step 2 source;
* the WWF Step 1/Step 2 scope doc exists with the required content.

These are intentionally source-level string guards: they catch an
accidental revert of the copy or the fallback logic, not pixel layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_API = Path(__file__).resolve().parents[2]
_REPO = _API.parents[1]
_WEB = _REPO / "apps" / "web"

_WORKFLOW = _WEB / "app" / "projects" / "[id]" / "workflow" / "page.tsx"
_RUNREPORT = _WEB / "components" / "RunReport.tsx"
# Phase Product-UX-E extracted the Result step into its own component +
# a cache hook; Phase Product-UX-F removed the technical link.
_STEP_REPORT = _WEB / "app" / "projects" / "[id]" / "workflow" / "_step-report.tsx"
_USE_RUN_REPORT = _WEB / "lib" / "use-run-report.ts"
_REPORT_DICT = _WEB / "lib" / "i18n" / "report.ts"
_I18N = _WEB / "lib" / "i18n.tsx"
_TECH_REPORT = (
    _WEB / "app" / "projects" / "[id]" / "runs" / "[runId]" / "report" / "page.tsx"
)
_TEMPLATES = _WEB / "app" / "templates" / "page.tsx"
_SCOPE_DOC = _REPO / "docs" / "methodologies" / "wwf-step1-step2-current-scope.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Guided result step (StepReport)
# ---------------------------------------------------------------------------


def test_step_report_renders_runreport() -> None:
    # Phase Product-UX-E — the Result step lives in _step-report.tsx now.
    src = _read(_STEP_REPORT)
    assert "<RunReport doc={report} />" in src


def test_step_report_has_loading_and_error_states() -> None:
    # Loading/error states + non-silent error handling now live in the
    # extracted component + the run-report cache hook (Phase Product-UX-E).
    step = _read(_STEP_REPORT)
    hook = _read(_USE_RUN_REPORT)
    assert "report.step.loadErrorTitle" in step  # actionable error
    assert "report.step.preparing" in step  # loading skeleton
    assert "loading" in hook and "error" in hook
    # The backend error must be surfaced (logged), not swallowed.
    assert "console.error" in hook


def test_step_report_does_not_silently_show_old_compact_summary() -> None:
    # The old fallback headline used a local ``fmtKg`` 1-decimal helper;
    # it must not reappear in the extracted Result step.
    assert "const fmtKg" not in _read(_STEP_REPORT)


def test_no_technical_link_in_guided_result_step() -> None:
    # Phase Product-UX-F — the guided flow has NO technical-detail link
    # (for any methodology). The Result step renders only the report.
    src = _read(_STEP_REPORT)
    assert "report.step.technicalLink" not in src
    assert "/runs/${" not in src
    assert "<Link" not in src


# ---------------------------------------------------------------------------
# Number formatting on the bespoke technical report page
# ---------------------------------------------------------------------------


def test_tech_report_uses_format_helpers() -> None:
    src = _read(_TECH_REPORT)
    assert 'from "@/lib/format"' in src
    # Headline kg figures go through formatKg, not raw string interpolation.
    assert "formatKg(s.plant_protein_kg)" in src
    assert "formatKg(s.total_in_scope_weight_kg)" in src


def test_tech_report_has_no_raw_kg_interpolation() -> None:
    src = _read(_TECH_REPORT)
    # These raw bindings produced "8145.02491000 kg" before Product-UX-D.
    assert "value={s.plant_protein_kg}" not in src
    assert "value={s.total_in_scope_weight_kg}" not in src
    assert "{g.weight_kg}<" not in src


# ---------------------------------------------------------------------------
# WWF Step 1 / Step 2 / NEVO copy
# ---------------------------------------------------------------------------


def test_runreport_states_wwf_step1_scope() -> None:
    # Phase Product-UX-E moved the WWF Step 1/Step 2 scope copy into the
    # i18n dictionary; RunReport renders it via the report.wwf.step1* keys.
    src = _read(_RUNREPORT)
    assert "report.wwf.step1Label" in src
    assert "report.wwf.step1Body" in src
    dict_src = _read(_REPORT_DICT)
    assert "Step 1" in dict_src
    assert "Step 2" in dict_src


@pytest.mark.parametrize(
    "needle",
    [
        "templates.wwfScope.title",
        "templates.wwfScope.body",
        "templates.nevoNote.title",
        "templates.nevoNote.body",
    ],
)
def test_templates_reference_scope_and_nevo_keys(needle: str) -> None:
    page = _read(_TEMPLATES)
    i18n = _read(_I18N)
    assert needle in page, f"{needle} not used on Templates page"
    assert needle in i18n, f"{needle} missing from i18n dictionary"


def test_i18n_wwf_scope_copy_mentions_step1_and_step2() -> None:
    i18n = _read(_I18N)
    # Both FR and EN scope strings name Step 1 and say Step 2 is not enabled.
    assert "Step 1" in i18n
    assert "Step 2" in i18n
    assert "n'est pas encore activé" in i18n or "not enabled yet" in i18n


def test_i18n_nevo_note_is_not_step2_source() -> None:
    i18n = _read(_I18N)
    # NEVO is nutrition enrichment, not retailer recipe data.
    assert "reference food composition" in i18n
    assert "recipe-level ingredient weights" in i18n


# ---------------------------------------------------------------------------
# Scope documentation
# ---------------------------------------------------------------------------


def test_scope_doc_exists_with_required_sections() -> None:
    assert _SCOPE_DOC.exists(), "missing wwf-step1-step2-current-scope.md"
    doc = _read(_SCOPE_DOC)
    for needle in (
        "Step 1",
        "Step 2",
        "is_own_brand",
        "ingredient percentage",
        "NEVO",
        "reference food composition",
        "Branded products remain product-level",
    ):
        assert needle in doc, f"scope doc missing: {needle}"
