"""Phase Product-UX-A — guard the downloadable CSV templates.

The frontend ``lib/templates.ts`` ships three CSV templates whose
headers are chosen to match the parser's auto-mapping synonyms exactly
so a downloaded template imports with zero "missing required field"
warnings. This test mirrors those exact header lists and asserts they
still map cleanly — if a future synonym refresh breaks one of the
templates, this fails instead of the operator's import.

Keep these lists in sync with ``apps/web/lib/templates.ts``.
"""

from __future__ import annotations

import collections

from altera_api.ingestion.mapping import infer_mapping

# Mirror of apps/web/lib/templates.ts (header order).
PT_HEADERS = [
    "product_id",
    "product_name",
    "raw_product_category",
    "ingredient_declaration_simulated",
    "pack_weight_g",
    "unit_purchased",
    "protein_total_g_per_100g",
    "protein_plant_g_per_100g",
    "protein_animal_g_per_100g",
    "protein_split_known",
    "brand_type",
    "label_claims_notes",
]

WWF_HEADERS = [
    "product_id",
    "product_name",
    "raw_product_category",
    "ingredient_declaration_simulated",
    "pack_weight_g",
    "units_sold",
    "retail_channel",
    "brand_type",
    "sales_weight_kg",
    "label_claims_notes",
]

COMBINED_HEADERS = [
    "product_id",
    "product_name",
    "raw_product_category",
    "ingredient_declaration_simulated",
    "pack_weight_g",
    "unit_purchased",
    "units_sold",
    "retail_channel",
    "brand_type",
    "protein_total_g_per_100g",
    "protein_plant_g_per_100g",
    "protein_animal_g_per_100g",
    "protein_split_known",
    "sales_weight_kg",
    "label_claims_notes",
]


def _no_dupes(headers: list[str], methodologies: list[str]) -> list[str]:
    result = infer_mapping(headers, methodologies=methodologies)
    canon = [
        e.canonical_field for e in result.entries if e.canonical_field
    ]
    return [k for k, v in collections.Counter(canon).items() if v > 1]


class TestPtTemplate:
    def test_pt_template_has_no_missing_required(self) -> None:
        r = infer_mapping(PT_HEADERS, methodologies=["protein_tracker"])
        assert r.missing_required_pt == []

    def test_pt_template_no_duplicate_canonicals(self) -> None:
        assert _no_dupes(PT_HEADERS, ["protein_tracker"]) == []

    def test_pt_key_columns_map_as_expected(self) -> None:
        r = infer_mapping(PT_HEADERS, methodologies=["protein_tracker"])
        m = {e.raw_header: e.canonical_field for e in r.entries}
        assert m["unit_purchased"] == "items_purchased"
        assert m["pack_weight_g"] == "weight_per_item_g"
        assert m["protein_total_g_per_100g"] == "protein_pct"


class TestWwfTemplate:
    def test_wwf_template_has_no_missing_required(self) -> None:
        r = infer_mapping(WWF_HEADERS, methodologies=["wwf"])
        assert r.missing_required_wwf == []

    def test_wwf_template_no_duplicate_canonicals(self) -> None:
        assert _no_dupes(WWF_HEADERS, ["wwf"]) == []

    def test_wwf_key_columns_map_as_expected(self) -> None:
        r = infer_mapping(WWF_HEADERS, methodologies=["wwf"])
        m = {e.raw_header: e.canonical_field for e in r.entries}
        assert m["units_sold"] == "items_sold"
        assert m["retail_channel"] == "retail_channel"
        assert m["brand_type"] == "is_own_brand"


class TestCombinedTemplate:
    def test_combined_template_satisfies_both_methodologies(self) -> None:
        r = infer_mapping(
            COMBINED_HEADERS, methodologies=["protein_tracker", "wwf"]
        )
        assert r.missing_required_pt == []
        assert r.missing_required_wwf == []

    def test_combined_template_no_duplicate_canonicals(self) -> None:
        assert (
            _no_dupes(COMBINED_HEADERS, ["protein_tracker", "wwf"]) == []
        )

    def test_combined_has_distinct_pt_and_wwf_volume_columns(self) -> None:
        r = infer_mapping(
            COMBINED_HEADERS, methodologies=["protein_tracker", "wwf"]
        )
        m = {e.raw_header: e.canonical_field for e in r.entries}
        # PT volume and WWF volume must map to different canonicals.
        assert m["unit_purchased"] == "items_purchased"
        assert m["units_sold"] == "items_sold"
