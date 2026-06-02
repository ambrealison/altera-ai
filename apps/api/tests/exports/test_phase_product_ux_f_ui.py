"""Phase Product-UX-F — guard the UI polish (no JS test runner).

Source-level guards over the committed frontend for:
* PART B — no technical-detail link in the guided flow;
* PART C — header no longer renders email / role / org text;
* PART D — report summary numbers go through the format helpers;
* PART E — no "being prepared" copy;
* PART F — the WWF-only summary links the methodology PDF (new tab,
  noopener) and the link is gated on the WWF section;
* PART G — the new summary copy has both FR and EN.
"""

from __future__ import annotations

from pathlib import Path

_API = Path(__file__).resolve().parents[2]
_REPO = _API.parents[1]
_WEB = _REPO / "apps" / "web"
_WF = _WEB / "app" / "projects" / "[id]" / "workflow"

_PAGE = _WF / "page.tsx"
_STEP_REPORT = _WF / "_step-report.tsx"
_RUNREPORT = _WEB / "components" / "RunReport.tsx"
_USERMENU = _WEB / "components" / "UserMenu.tsx"
_REPORT_DICT = _WEB / "lib" / "i18n" / "report.ts"

_PDF = (
    "https://wwfint.awsassets.panda.org/downloads/"
    "wwf-planet-based-diets-retailer-methodology.pdf"
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# PART B — no technical-detail link in the guided flow
# ---------------------------------------------------------------------------


def test_step_report_has_no_technical_link() -> None:
    src = _read(_STEP_REPORT)
    assert "report.step.technicalLink" not in src
    assert "isAltera" not in src  # the admin-gated link block is gone
    # No actual link/href to the technical run page (ignore prose in
    # comments — match a real href template literal).
    assert "/runs/${" not in src
    assert "<Link" not in src


def test_workflow_hero_has_no_technical_detail_link() -> None:
    src = _read(_PAGE)
    # The hero no longer frames its nav as a technical-detail link.
    assert "workflow.hero.technicalDetail" not in src
    # It uses plain back-to-project navigation instead.
    assert "workflow.backToProject" in src


# ---------------------------------------------------------------------------
# PART C — header cleanup
# ---------------------------------------------------------------------------


def test_header_does_not_render_email_or_org_text() -> None:
    src = _read(_USERMENU)
    # The visible email line and the role/org line were removed.
    assert "{currentUser.email}" not in src
    assert "currentUser.role" not in src
    assert "organisation_id.slice" not in src
    assert "· org" not in src
    # Avatar initial + sign out remain.
    assert "currentUser.email?.[0]" in src
    assert "account.signout" in src


# ---------------------------------------------------------------------------
# PART D/E/F — report summary
# ---------------------------------------------------------------------------


def test_report_summary_uses_format_helpers() -> None:
    src = _read(_RUNREPORT)
    # The hero summary is built from structured fields via the helpers,
    # not the raw backend narrative string.
    assert "report.summary.ptRatio" in src
    assert "formatKg(doc.pt_section.plant_protein_kg)" in src
    assert "formatKg(doc.wwf_section.total_in_scope_weight_kg)" in src
    # The raw backend narrative is no longer rendered in the hero.
    assert "{doc.executive_summary}" not in src


def test_report_summary_no_being_prepared_copy() -> None:
    # The user-facing report dictionary carries no "being prepared" /
    # "en préparation" copy (the draft approval phrase was dropped).
    dict_src = _read(_REPORT_DICT)
    assert "being prepared" not in dict_src
    assert "en préparation" not in dict_src


def test_wwf_methodology_pdf_link_present_and_safe() -> None:
    src = _read(_RUNREPORT)
    assert _PDF in src
    assert "report.summary.wwfMethodologyLink" in src
    assert 'target="_blank"' in src
    assert 'rel="noopener noreferrer"' in src
    # The link lives inside the WWF section gate (doc.wwf_section), so it
    # never renders for PT-only.
    assert "doc.wwf_section && (" in src


def test_report_summary_keys_have_fr_and_en() -> None:
    src = _read(_REPORT_DICT)
    for key in (
        "report.summary.ptRatio",
        "report.summary.ptEmpty",
        "report.summary.wwfLead",
        "report.summary.wwfMethodologyLink",
        "report.summary.wwfTail",
    ):
        assert key in src, f"report.ts missing {key}"
    # Both the FR methodology phrasing and the EN one are present.
    assert "méthodologie WWF Planet-Based Diets" in src
    assert "WWF Planet-Based Diets methodology" in src
