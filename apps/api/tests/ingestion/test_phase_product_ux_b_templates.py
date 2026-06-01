"""Phase Product-UX-B — verify the SHIPPED template assets import.

The Templates page links to static CSV/XLSX files under
``apps/web/public/templates/``. This test reads the committed CSV
headers (the source of truth the user downloads) and asserts each
maps with no missing-required field and no duplicate canonical — so a
parser synonym change or a hand-edit to an asset is caught here, not
by the operator's failed import.
"""

from __future__ import annotations

import collections
import csv
from pathlib import Path

import pytest

from altera_api.ingestion.mapping import infer_mapping

_PUBLIC = (
    Path(__file__).resolve().parents[3]
    / "web"
    / "public"
    / "templates"
)

_CASES = [
    ("altera_template_protein_tracker.csv", ["protein_tracker"]),
    ("altera_template_wwf.csv", ["wwf"]),
    ("altera_template_combined.csv", ["protein_tracker", "wwf"]),
]


def _headers(name: str) -> list[str]:
    path = _PUBLIC / name
    with path.open(encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh))


@pytest.mark.parametrize("filename,methodologies", _CASES)
def test_shipped_template_maps_cleanly(
    filename: str, methodologies: list[str]
) -> None:
    headers = _headers(filename)
    result = infer_mapping(headers, methodologies=methodologies)
    if "protein_tracker" in methodologies:
        assert result.missing_required_pt == [], (
            f"{filename}: missing PT {result.missing_required_pt}"
        )
    if "wwf" in methodologies:
        assert result.missing_required_wwf == [], (
            f"{filename}: missing WWF {result.missing_required_wwf}"
        )
    canon = [e.canonical_field for e in result.entries if e.canonical_field]
    dups = [k for k, v in collections.Counter(canon).items() if v > 1]
    assert dups == [], f"{filename}: duplicate canonicals {dups}"


def test_all_six_assets_present() -> None:
    for name, _ in _CASES:
        assert (_PUBLIC / name).exists(), f"missing {name}"
        xlsx = name.replace(".csv", ".xlsx")
        assert (_PUBLIC / xlsx).exists(), f"missing {xlsx}"


def test_combined_has_distinct_pt_and_wwf_volume_columns() -> None:
    headers = _headers("altera_template_combined.csv")
    result = infer_mapping(
        headers, methodologies=["protein_tracker", "wwf"]
    )
    m = {e.raw_header: e.canonical_field for e in result.entries}
    assert m.get("unit_purchased") == "items_purchased"
    assert m.get("units_sold") == "items_sold"
