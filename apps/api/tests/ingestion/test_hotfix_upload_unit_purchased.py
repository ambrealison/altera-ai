"""Hotfix-Upload — ``unit_purchased`` mapping + error cascade.

Operator's 17-column / 100-row file silently mis-mapped ``unit_purchased``
to ``weight_per_item_g`` and the import surfaced as
"0 produit(s) inséré(s) · 7800 erreur(s)" — 78 advance retries on the
same failing chunk × 100 rows.

Covered:
  A. ``unit_purchased`` (singular) auto-maps to ``items_purchased``,
     never to ``weight_per_item_g``. Plural and French variants too.
  B. Quantity-token guard: any header containing
     ``purchased``/``sold``/``vendu``/``achete`` cannot land on a
     weight canonical even when no specific synonym matches.
  C. The 100-row dataset schema (the exact 17-column shape the
     operator uploads) maps every required field correctly with no
     missing-required-pt warning.
"""

from __future__ import annotations

from altera_api.ingestion.mapping import infer_mapping


def _canonical_for(headers: list[str], header: str) -> tuple[str | None, str]:
    """Run the auto-mapper and return ``(canonical, confidence)`` for
    the named header."""
    result = infer_mapping(headers, methodologies=["protein_tracker", "wwf"])
    entry = next(e for e in result.entries if e.raw_header == header)
    return entry.canonical_field, entry.confidence


# ---------------------------------------------------------------------------
# A. unit_purchased synonyms map to items_purchased
# ---------------------------------------------------------------------------


class TestUnitPurchasedMapsToItems:
    def test_unit_purchased_singular_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(["unit_purchased"], "unit_purchased")
        assert canonical == "items_purchased"

    def test_units_purchased_plural_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(["units_purchased"], "units_purchased")
        assert canonical == "items_purchased"

    def test_quantity_purchased_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(
            ["quantity_purchased"], "quantity_purchased"
        )
        assert canonical == "items_purchased"

    def test_purchased_units_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(["purchased_units"], "purchased_units")
        assert canonical == "items_purchased"

    def test_volume_achats_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(["volume_achats"], "volume_achats")
        assert canonical == "items_purchased"

    def test_quantite_achetee_maps_to_items_purchased(self) -> None:
        canonical, _conf = _canonical_for(
            ["quantite_achetee"], "quantite_achetee"
        )
        assert canonical == "items_purchased"


# ---------------------------------------------------------------------------
# B. Quantity-token guard — never weight
# ---------------------------------------------------------------------------


class TestQuantityTokenGuard:
    def test_unit_purchased_never_weight(self) -> None:
        canonical, _conf = _canonical_for(["unit_purchased"], "unit_purchased")
        assert canonical != "weight_per_item_g"
        assert canonical != "weight_per_item_kg"

    def test_units_sold_never_weight(self) -> None:
        canonical, _conf = _canonical_for(["units_sold"], "units_sold")
        # Should map to items_sold via existing synonym or the guard.
        assert canonical == "items_sold"

    def test_unrecognised_purchased_header_still_routes_to_items(self) -> None:
        # Even a header the synonym list doesn't know about should land
        # on items_purchased thanks to the token guard.
        canonical, _conf = _canonical_for(
            ["xyz_qty_purchased_total"], "xyz_qty_purchased_total"
        )
        assert canonical == "items_purchased"

    def test_pack_weight_g_still_weight(self) -> None:
        # Non-regression — weight headers stay weight.
        canonical, _conf = _canonical_for(["pack_weight_g"], "pack_weight_g")
        assert canonical == "weight_per_item_g"


# ---------------------------------------------------------------------------
# C. 100-row dataset schema (the operator's exact headers)
# ---------------------------------------------------------------------------


class TestDataset100Schema:
    HEADERS = [
        "product_id",
        "product_name",
        "brand_type",
        "retail_category",
        "raw_product_category",
        "ingredient_declaration_simulated",
        "pack_weight_g",
        "units_sold",
        "unit_purchased",
        "sales_weight_kg",
        "drained_weight_g_if_applicable",
        "protein_total_g_per_100g",
        "protein_plant_g_per_100g",
        "protein_animal_g_per_100g",
        "protein_split_known",
        "main_ingredient_origin_hint",
        "label_claims_notes",
    ]

    def test_units_sold_and_unit_purchased_map_to_different_canonicals(
        self,
    ) -> None:
        result = infer_mapping(
            self.HEADERS, methodologies=["protein_tracker", "wwf"]
        )
        by_header = {e.raw_header: e.canonical_field for e in result.entries}
        assert by_header["unit_purchased"] == "items_purchased"
        assert by_header["units_sold"] == "items_sold"
        assert by_header["pack_weight_g"] == "weight_per_item_g"

    def test_no_missing_required_pt(self) -> None:
        # The 100-row dataset maps every PT requirement (product_name +
        # weight + items_purchased) so the preflight should not flag
        # any missing required PT fields.
        result = infer_mapping(
            self.HEADERS, methodologies=["protein_tracker"]
        )
        assert result.missing_required_pt == [], (
            f"unexpected missing required PT: {result.missing_required_pt}"
        )

    def test_no_missing_required_wwf(self) -> None:
        result = infer_mapping(self.HEADERS, methodologies=["wwf"])
        assert result.missing_required_wwf == [], (
            f"unexpected missing required WWF: {result.missing_required_wwf}"
        )

    def test_protein_total_maps_correctly(self) -> None:
        result = infer_mapping(
            self.HEADERS, methodologies=["protein_tracker"]
        )
        by_header = {e.raw_header: e.canonical_field for e in result.entries}
        assert by_header["protein_total_g_per_100g"] == "protein_pct"
