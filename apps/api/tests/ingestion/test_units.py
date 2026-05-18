from __future__ import annotations

from decimal import Decimal

import pytest

from altera_api.ingestion.units import (
    G_TO_KG,
    LB_TO_KG,
    OZ_TO_KG,
    normalise_protein_pct,
    normalise_weight_kg,
)


class TestWeight:
    def test_kg_passthrough(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_kg": "0.400"})
        assert err is None
        assert v == Decimal("0.400")

    def test_g_conversion(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_g": "300"})
        assert err is None
        assert v == Decimal("300") * G_TO_KG  # 0.300

    def test_lb_conversion(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_lb": "1.0"})
        assert err is None
        assert v == Decimal("1.0") * LB_TO_KG

    def test_oz_conversion(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_oz": "16"})
        assert err is None
        assert v == Decimal("16") * OZ_TO_KG

    def test_missing_returns_none_none(self) -> None:
        assert normalise_weight_kg({}) == (None, None)
        assert normalise_weight_kg({"weight_per_item_kg": ""}) == (None, None)

    def test_mixed_units_rejected(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_kg": "0.4", "weight_per_item_g": "400"})
        assert v is None
        assert err == "mixed_weight_units"

    def test_non_positive_rejected(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_kg": "0"})
        assert v is None
        assert err == "weight_non_positive"
        v, err = normalise_weight_kg({"weight_per_item_kg": "-1"})
        assert err == "weight_non_positive"

    def test_too_large_rejected(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_kg": "100"})
        assert err == "weight_too_large"

    def test_garbage_input_rejected(self) -> None:
        v, err = normalise_weight_kg({"weight_per_item_kg": "abc"})
        assert err == "invalid_type"


class TestProtein:
    def test_pct_direct(self) -> None:
        v, err = normalise_protein_pct({"protein_pct": "22.5"})
        assert err is None
        assert v == Decimal("22.5")

    def test_g_per_100g_synonym(self) -> None:
        v, err = normalise_protein_pct({"protein_g_per_100g": "22.5"})
        assert err is None
        assert v == Decimal("22.5")

    def test_g_per_100ml_with_density(self) -> None:
        # 3.4 g protein per 100ml, milk density ~1.03 g/ml → 3.4/1.03 ≈ 3.30097
        v, err = normalise_protein_pct({"protein_g_per_100ml": "3.4", "density_g_per_ml": "1.03"})
        assert err is None
        assert v is not None
        assert v == Decimal("3.4") / Decimal("1.03")

    def test_g_per_100ml_without_density(self) -> None:
        v, err = normalise_protein_pct({"protein_g_per_100ml": "3.4"})
        assert err == "missing_density"

    def test_g_per_serving_with_serving_g(self) -> None:
        v, err = normalise_protein_pct({"protein_g_per_serving": "20", "serving_g": "100"})
        assert err is None
        assert v == Decimal("20") / Decimal("100") * Decimal("100")

    def test_g_per_serving_without_serving_g(self) -> None:
        v, err = normalise_protein_pct({"protein_g_per_serving": "20"})
        assert err == "missing_serving_g"

    def test_energy_units_rejected(self) -> None:
        for col in ("protein_kj", "protein_kcal"):
            v, err = normalise_protein_pct({col: "300"})
            assert err == "energy_not_protein"

    @pytest.mark.parametrize("value", ["-0.1", "100.1", "200"])
    def test_out_of_range_rejected(self, value: str) -> None:
        v, err = normalise_protein_pct({"protein_pct": value})
        assert err == "protein_out_of_range"

    def test_mixed_sources_rejected(self) -> None:
        v, err = normalise_protein_pct({"protein_pct": "10", "protein_g_per_100g": "11"})
        assert err == "mixed_protein_inputs"

    def test_missing_returns_none_none(self) -> None:
        assert normalise_protein_pct({}) == (None, None)

    def test_garbage_input_rejected(self) -> None:
        v, err = normalise_protein_pct({"protein_pct": "abc"})
        assert err == "invalid_type"
