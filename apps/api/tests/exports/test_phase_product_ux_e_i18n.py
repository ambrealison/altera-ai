"""Phase Product-UX-E — guard the EN translation + report-cache work.

No JS test runner exists, so (as in prior phases) these checks read the
committed frontend source and pin the invariants that matter:

* the modular i18n dictionary is wired and every per-surface module
  exports balanced fr/en entries (no half-translated key);
* the report/result-step + key surfaces actually go through i18n
  (`useT` is imported);
* PART C — switching language CANNOT change canonical mapping values:
  ``CANONICAL_FIELDS`` and the dropdown ``value=`` attributes are the
  stable canonical identifiers; only the *labels* moved to i18n keys;
* the report cache is keyed by ``run_id``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_API = Path(__file__).resolve().parents[2]
_REPO = _API.parents[1]
_WEB = _REPO / "apps" / "web"
_I18N = _WEB / "lib" / "i18n"
_WF = _WEB / "app" / "projects" / "[id]" / "workflow"

_INLINE_UPLOAD = _WF / "_inline-upload.tsx"
_USE_RUN_REPORT = _WEB / "lib" / "use-run-report.ts"

_DICT_MODULES = [
    "common",
    "report",
    "workflow",
    "upload",
    "validation",
    "nutrition",
    "review",
    "correction",
    "projectsExtra",
]

# Surfaces that must route their visible text through i18n.
_I18N_COMPONENTS = [
    _WEB / "components" / "RunReport.tsx",
    _WF / "_step-report.tsx",
    _WF / "page.tsx",
    _WF / "_validation-table.tsx",
    _WF / "_inline-upload.tsx",
    _WF / "_nutrition-table.tsx",
    _WF / "_inline-review.tsx",
    _WF / "_wwf-correction-modal.tsx",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dictionary wiring + completeness
# ---------------------------------------------------------------------------


def test_i18n_merges_all_surface_modules() -> None:
    src = _read(_I18N.parent / "i18n.tsx")
    for mod in _DICT_MODULES:
        assert f'from "./i18n/{mod}"' in src, f"i18n.tsx does not import {mod}"
        assert f"...{mod}" in src, f"i18n.tsx does not merge {mod}"


@pytest.mark.parametrize("mod", _DICT_MODULES)
def test_dict_module_has_balanced_fr_en(mod: str) -> None:
    src = _read(_I18N / f"{mod}.ts")
    fr = len(re.findall(r"\bfr:", src))
    en = len(re.findall(r"\ben:", src))
    assert fr == en, f"{mod}.ts has {fr} fr: vs {en} en: entries (a key is missing a language)"
    # common/report are authored with content; surface modules may vary
    # but must not be empty for the big surfaces.
    if mod in {"common", "report", "workflow", "upload", "validation"}:
        assert fr > 0, f"{mod}.ts is unexpectedly empty"


def test_report_dict_has_part_d_keys() -> None:
    src = _read(_I18N / "report.ts")
    for key in (
        "report.step.title",
        "report.step.loadErrorTitle",
        "report.step.preparing",
        "report.kpi.plantProtein",
        "report.pt.topPositive",
        "report.wwf.topAligned",
        "report.wwf.step1Label",
    ):
        assert key in src, f"report.ts missing {key}"


def test_report_dict_translates_part_d_strings_to_english() -> None:
    src = _read(_I18N / "report.ts")
    # Spot-check the explicit PART D English wordings.
    assert '"Results / report"' in src or "Results / report" in src
    assert "could not be loaded" in src
    assert "Plant protein" in src


# ---------------------------------------------------------------------------
# Components are wired to i18n
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _I18N_COMPONENTS, ids=lambda p: p.name)
def test_component_uses_i18n(path: Path) -> None:
    src = _read(path)
    assert 'from "@/lib/i18n"' in src, f"{path.name} does not import from i18n"
    assert "useT(" in src, f"{path.name} does not call useT()"


# ---------------------------------------------------------------------------
# PART C — language must NOT change canonical mapping values
# ---------------------------------------------------------------------------


def test_canonical_fields_are_stable_identifiers() -> None:
    src = _read(_INLINE_UPLOAD)
    # The canonical field list is the source of truth for mapping targets.
    assert "const CANONICAL_FIELDS = [" in src
    block = src.split("const CANONICAL_FIELDS = [", 1)[1].split("]", 1)[0]
    for canonical in (
        "external_product_id",
        "product_name",
        "weight_per_item_kg",
        "weight_per_item_g",
        "items_purchased",
        "items_sold",
        "retail_channel",
    ):
        assert f'"{canonical}"' in block, f"CANONICAL_FIELDS lost {canonical}"


def test_mapping_dropdown_submits_canonical_values() -> None:
    src = _read(_INLINE_UPLOAD)
    # The <select> option values stay canonical; only the displayed label
    # is translated. These attributes must remain verbatim.
    assert "value={f}" in src
    assert 'value="__none__"' in src
    assert 'value="ignore"' in src


def test_mapping_labels_moved_to_i18n_keys() -> None:
    inline = _read(_INLINE_UPLOAD)
    upload = _read(_I18N / "upload.ts")
    # The canonical->label map now maps to i18n KEYS, not French strings.
    assert "CANONICAL_FIELD_LABEL_KEYS" in inline
    assert '"upload.field.items_purchased"' in inline
    assert '"upload.field.items_sold"' in inline
    # And those keys are defined in the upload dict with fr + en.
    for key in ("upload.field.items_purchased", "upload.field.items_sold"):
        assert key in upload, f"upload.ts missing {key}"


# ---------------------------------------------------------------------------
# PART F — report cache keyed by run_id
# ---------------------------------------------------------------------------


def test_report_cache_is_keyed_by_run_id() -> None:
    src = _read(_USE_RUN_REPORT)
    assert "new Map<string, ReportDocument>()" in src
    assert "_reportCache.get(runId)" in src
    assert "_reportCache.set(runId" in src
    # Cache hit short-circuits the fetch (instant render, no reload).
    assert "getReport" in src
