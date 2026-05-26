"""Phase WWF-I hotfix #2 — 100-product dataset regression.

A user-supplied CSV with 100 rows and the following columns:

    product_id, product_name, brand_type, retail_category,
    raw_product_category, ingredient_declaration_simulated,
    pack_weight_g, units_sold, sales_weight_kg,
    drained_weight_g_if_applicable, protein_total_g_per_100g,
    protein_plant_g_per_100g, protein_animal_g_per_100g,
    protein_split_known, main_ingredient_origin_hint,
    label_claims_notes

imported as **zero** products despite the file being well-formed.
Three independent normalisation bugs combined to drop every row:

  1. ``brand_type`` values like ``"Own brand"`` / ``"Branded"`` /
     ``"Private label"`` failed ``_coerce_bool`` (only literal
     true/false/yes/no/oui/non were accepted).
  2. ``retail_category`` values like ``"Grocery/Ambient"`` /
     ``"Fresh"`` / ``"Frozen"`` / ``"Surgelé"`` failed
     ``RetailChannel("...")`` (the enum only accepted ``fresh`` /
     ``grocery_ambient`` / ``frozen``).
  3. The dataset's ``pack_weight_g`` / ``protein_total_g_per_100g``
     / ``protein_plant_g_per_100g`` / ``protein_animal_g_per_100g``
     / ``ingredient_declaration_simulated`` / ``label_claims_notes``
     / ``raw_product_category`` headers had no synonym entries.

After the hotfix the same file ingests cleanly under WWF-only,
PT-only, and PT+WWF projects, with grams auto-converted to kg and
brand-type / retail-category free-text values resolved to the
canonical enums.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from altera_api.domain.common import Methodology
from altera_api.domain.product import RetailChannel
from altera_api.ingestion.parser import _coerce_bool
from altera_api.ingestion.pipeline import ingest_csv_bytes


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


def _ingest(
    csv_bytes: bytes, methodologies: frozenset[Methodology]
):
    return ingest_csv_bytes(
        csv_bytes,
        upload_id=uuid4(),
        project_id=uuid4(),
        organisation_id=uuid4(),
        methodologies_enabled=methodologies,
        now=datetime.now(UTC),
    )


# A representative slice of the 100-product dataset. Real values copied
# from the user's bug report ("Own brand", "Branded", "Grocery/Ambient",
# "Fresh", "Frozen", "500", "240", "2624", etc.).
_DATASET_ROWS: list[dict[str, str]] = [
    {
        "product_id": "P-001",
        "product_name": "Red Lentils 500g",
        "brand_type": "Own brand",
        "retail_category": "Grocery/Ambient",
        "raw_product_category": "Pulses",
        "ingredient_declaration_simulated": "red lentils, water",
        "pack_weight_g": "500",
        "units_sold": "2624",
        "sales_weight_kg": "1312.0",
        "drained_weight_g_if_applicable": "",
        "protein_total_g_per_100g": "24.0",
        "protein_plant_g_per_100g": "24.0",
        "protein_animal_g_per_100g": "0.0",
        "protein_split_known": "Yes",
        "main_ingredient_origin_hint": "Plant",
        "label_claims_notes": "organic|vegan",
    },
    {
        "product_id": "P-002",
        "product_name": "Chicken Breast Fillets",
        "brand_type": "Branded",
        "retail_category": "Fresh",
        "raw_product_category": "Poultry",
        "ingredient_declaration_simulated": "chicken breast",
        "pack_weight_g": "240",
        "units_sold": "1850",
        "sales_weight_kg": "444.0",
        "drained_weight_g_if_applicable": "",
        "protein_total_g_per_100g": "22.0",
        "protein_plant_g_per_100g": "0.0",
        "protein_animal_g_per_100g": "22.0",
        "protein_split_known": "Yes",
        "main_ingredient_origin_hint": "Animal",
        "label_claims_notes": "",
    },
    {
        "product_id": "P-003",
        "product_name": "Frozen Mixed Vegetables",
        "brand_type": "Private label",
        "retail_category": "Frozen",
        "raw_product_category": "Frozen Veg",
        "ingredient_declaration_simulated": "carrots, peas, sweetcorn",
        "pack_weight_g": "1000",
        "units_sold": "920",
        "sales_weight_kg": "920.0",
        "drained_weight_g_if_applicable": "",
        "protein_total_g_per_100g": "3.5",
        "protein_plant_g_per_100g": "3.5",
        "protein_animal_g_per_100g": "0.0",
        "protein_split_known": "Yes",
        "main_ingredient_origin_hint": "Plant",
        "label_claims_notes": "vegan",
    },
]


# ---------------------------------------------------------------------------
# Part E.1 — WWF-only project imports the dataset
# ---------------------------------------------------------------------------


class TestDatasetWWFOnly:
    def test_wwf_only_imports_rows(self) -> None:
        result = _ingest(
            _csv(_DATASET_ROWS),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert result.read_error is None
        assert len(result.products) == 3, [
            (e.field, e.code, e.message) for e in result.report.errors
        ]
        p = result.products[0]
        assert p.wwf_fields is not None
        # ``pack_weight_g`` converted to kg via the alias map.
        assert p.weight_per_item_kg == Decimal("0.5")
        # ``brand_type=Own brand`` resolved to True.
        assert p.wwf_fields.is_own_brand is True
        # ``retail_category=Grocery/Ambient`` resolved via alias.
        assert p.wwf_fields.retail_channel is RetailChannel.GROCERY_AMBIENT

    def test_wwf_only_imports_branded_fresh_row(self) -> None:
        result = _ingest(
            _csv([_DATASET_ROWS[1]]),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert len(result.products) == 1
        p = result.products[0]
        assert p.weight_per_item_kg == Decimal("0.24")
        assert p.wwf_fields is not None
        assert p.wwf_fields.is_own_brand is False  # "Branded"
        assert p.wwf_fields.retail_channel is RetailChannel.FRESH

    def test_wwf_only_imports_frozen_private_label_row(self) -> None:
        result = _ingest(
            _csv([_DATASET_ROWS[2]]),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert len(result.products) == 1
        p = result.products[0]
        assert p.weight_per_item_kg == Decimal("1.0")
        assert p.wwf_fields is not None
        assert p.wwf_fields.is_own_brand is True  # "Private label"
        assert p.wwf_fields.retail_channel is RetailChannel.FROZEN


# ---------------------------------------------------------------------------
# Part E.2 — PT-only project imports the dataset
# ---------------------------------------------------------------------------
#
# The dataset uses ``units_sold`` (WWF semantic), not ``items_purchased``
# (PT semantic). PT-only projects with this CSV ingest rows but the
# rows are downgraded to "no PT block" (per the WWF-I hotfix). The user
# sees an explicit per-row warning, NOT a silent "0 products" result.


class TestDatasetPTOnly:
    def test_pt_only_dataset_does_not_silently_empty_csv(self) -> None:
        result = _ingest(
            _csv(_DATASET_ROWS),
            methodologies=frozenset({Methodology.PROTEIN_TRACKER}),
        )
        # The brief's hard requirement: the CSV must NEVER look empty.
        # In this scenario the rows can't satisfy PT (no items_purchased)
        # and the project doesn't enable WWF, so each row is a structured
        # row-level error — there's no silent drop.
        if len(result.products) == 0:
            assert any(
                e.code == "no_methodology_satisfiable"
                for e in result.report.errors
            ), [(e.field, e.code) for e in result.report.errors]
        else:
            # If we ever extend PT to accept units_sold as items_purchased
            # (Phase WWF-I-hotfix2 PART B.5 deliberately leaves this as
            # a separate decision), every row's PT block is populated.
            assert all(p.pt_fields is not None for p in result.products)


# ---------------------------------------------------------------------------
# Part E.3 — PT+WWF project imports the dataset
# ---------------------------------------------------------------------------


class TestDatasetPTWWF:
    def test_pt_wwf_imports_rows_as_wwf_only(self) -> None:
        """Dataset has WWF data (units_sold + retail_category + brand_type)
        but no PT quantity (items_purchased). On a PT+WWF project the
        Phase WWF-I hotfix downgrades each row to WWF-only and emits
        warnings — but every row is ingested, the CSV is never empty."""
        result = _ingest(
            _csv(_DATASET_ROWS),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert result.read_error is None
        assert len(result.products) == 3
        for p in result.products:
            assert p.wwf_fields is not None
            assert p.pt_fields is None
        # User gets per-row warnings about the missing PT quantity.
        codes = {w.code for w in result.report.warnings}
        assert "missing_for_methodology" in codes
        pt_missing = {
            w.field
            for w in result.report.warnings
            if w.code == "missing_for_methodology"
        }
        assert "items_purchased" in pt_missing


# ---------------------------------------------------------------------------
# Part E.4 — All rows failed value parsing → diagnostic, not empty CSV
# ---------------------------------------------------------------------------


class TestDiagnosticOnAllRowsFailedParsing:
    def test_unknown_retail_category_value_is_structured_error(self) -> None:
        """If a row's ``retail_category`` is something we genuinely don't
        recognise (after the alias map), the user sees a structured
        ``invalid_enum`` error per row — NOT a silent zero-product
        result."""
        broken = [
            {
                **_DATASET_ROWS[0],
                "retail_category": "WeirdUnknownValue",
            }
        ]
        result = _ingest(
            _csv(broken),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert len(result.products) == 0
        assert any(
            e.code == "invalid_enum" and e.field == "retail_channel"
            for e in result.report.errors
        )


# ---------------------------------------------------------------------------
# Part E.5 — retail_channel synonym coverage
# ---------------------------------------------------------------------------


class TestRetailChannelSynonyms:
    """Pin the alias map. Each value must resolve to the expected
    canonical RetailChannel."""

    EXPECTED: list[tuple[str, RetailChannel]] = [
        ("Fresh", RetailChannel.FRESH),
        ("fresh", RetailChannel.FRESH),
        ("Frais", RetailChannel.FRESH),
        ("Chilled", RetailChannel.FRESH),
        ("Grocery/Ambient", RetailChannel.GROCERY_AMBIENT),
        ("Grocery Ambient", RetailChannel.GROCERY_AMBIENT),
        ("Ambient", RetailChannel.GROCERY_AMBIENT),
        ("Grocery", RetailChannel.GROCERY_AMBIENT),
        ("Épicerie", RetailChannel.GROCERY_AMBIENT),
        ("epicerie", RetailChannel.GROCERY_AMBIENT),
        ("Sec", RetailChannel.GROCERY_AMBIENT),
        ("Frozen", RetailChannel.FROZEN),
        ("Surgelé", RetailChannel.FROZEN),
        ("surgele", RetailChannel.FROZEN),
        ("Congelé", RetailChannel.FROZEN),
    ]

    def test_each_alias_resolves(self) -> None:
        for raw, expected in self.EXPECTED:
            row = {**_DATASET_ROWS[0], "retail_category": raw}
            result = _ingest(
                _csv([row]),
                methodologies=frozenset({Methodology.WWF}),
            )
            assert (
                len(result.products) == 1
            ), f"alias {raw!r} should resolve; errors={[e.message for e in result.report.errors]}"
            assert (
                result.products[0].wwf_fields is not None
                and result.products[0].wwf_fields.retail_channel is expected
            ), f"alias {raw!r} expected {expected}, got {result.products[0].wwf_fields.retail_channel if result.products[0].wwf_fields else None}"


# ---------------------------------------------------------------------------
# Part E.6 — is_own_brand free-text synonyms
# ---------------------------------------------------------------------------


class TestBrandTypeSynonyms:
    """Test the enhanced ``_coerce_bool`` directly so we don't have to
    spin up a whole CSV per case."""

    TRUE_CASES: list[str] = [
        "Own brand",
        "own brand",
        "OWN BRAND",
        "Own-brand",
        "Private label",
        "private-label",
        "Store brand",
        "Store label",
        "MDD",
        "mdd",
        "Marque distributeur",
        "Marque propre",
        "PL",
    ]

    FALSE_CASES: list[str] = [
        "Branded",
        "Brand",
        "National brand",
        "Manufacturer brand",
        "name brand",
        "Marque nationale",
        "Marque fabricant",
        "Marque",
    ]

    def test_true_values(self) -> None:
        for v in self.TRUE_CASES:
            assert _coerce_bool(v) is True, f"{v!r} should be True"

    def test_false_values(self) -> None:
        for v in self.FALSE_CASES:
            assert _coerce_bool(v) is False, f"{v!r} should be False"

    def test_existing_boolean_tokens_unchanged(self) -> None:
        # Non-regression — original tokens still resolve.
        for v in ("true", "False", "1", "0", "yes", "non", "oui"):
            assert _coerce_bool(v) is not None


# ---------------------------------------------------------------------------
# Part E.7 — grams-to-kg conversion via the alias map
# ---------------------------------------------------------------------------


class TestGramWeightConversion:
    def test_pack_weight_g_500_converts_to_0_5_kg(self) -> None:
        row = {**_DATASET_ROWS[0], "pack_weight_g": "500"}
        result = _ingest(
            _csv([row]),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert len(result.products) == 1
        assert result.products[0].weight_per_item_kg == Decimal("0.5")

    def test_pack_weight_g_240_converts_to_0_24_kg(self) -> None:
        row = {**_DATASET_ROWS[0], "pack_weight_g": "240"}
        result = _ingest(
            _csv([row]),
            methodologies=frozenset({Methodology.WWF}),
        )
        assert len(result.products) == 1
        assert result.products[0].weight_per_item_kg == Decimal("0.24")


# ---------------------------------------------------------------------------
# Part E.8 — protein synonyms map cleanly
# ---------------------------------------------------------------------------


class TestProteinHeaderSynonyms:
    def test_protein_total_g_per_100g_maps_to_protein_pct(self) -> None:
        """When the user has full protein data (incl. items_purchased),
        the new ``protein_total_g_per_100g`` synonym wires through to
        the PT block."""
        row = {
            "product_id": "X-1",
            "product_name": "Test",
            "brand_type": "Branded",
            "retail_category": "Fresh",
            "pack_weight_g": "500",
            "items_purchased": "100",
            "units_sold": "100",
            "protein_total_g_per_100g": "24.0",
            "protein_plant_g_per_100g": "0.0",
            "protein_animal_g_per_100g": "24.0",
        }
        result = _ingest(
            _csv([row]),
            methodologies=frozenset(
                {Methodology.PROTEIN_TRACKER, Methodology.WWF}
            ),
        )
        assert len(result.products) == 1
        p = result.products[0]
        assert p.pt_fields is not None
        assert p.pt_fields.protein_pct == Decimal("24.0")
        assert p.pt_fields.plant_protein_pct == Decimal("0.0")
        assert p.pt_fields.animal_protein_pct == Decimal("24.0")
