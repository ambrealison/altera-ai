#!/usr/bin/env python
"""Phase WWF-Q2 — build an evaluation fixture from the operator's
100-product CSV (`dataset_100_produits_avant_categorisation_*.csv`).

The CSV's ``raw_product_category`` column carries the expected WWF
classification at coarse granularity — we map each category string
to the canonical WWF food group + subgroup + composite bucket.

Output
------
``altera_api/data/audit/dataset_100_fixture.json``

Each case has the same shape as the curated Phase WWF-D fixture so
it can be fed straight into ``scripts/evaluate_wwf_classification.py``.

Usage
-----

    .venv/bin/python scripts/build_dataset_100_fixture.py

Then evaluate:

    .venv/bin/python scripts/evaluate_wwf_classification.py \
        --fixture altera_api/data/audit/dataset_100_fixture.json \
        --target 0.0 \
        --mismatches-csv /tmp/dataset_100_mismatches.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SRC = (
    _REPO_ROOT / "altera_api" / "data" / "audit" / "dataset_100_source.csv"
)
_DEFAULT_OUT = (
    _REPO_ROOT / "altera_api" / "data" / "audit" / "dataset_100_fixture.json"
)


# raw_product_category → (food_group, subgroup_field, subgroup_value,
#                         is_composite, composite_bucket).
# When subgroup is None the WWF guard fixture doesn't enforce it.
_CATEGORY_MAP: dict[str, tuple[str, str | None, str | None, bool, str | None]] = {
    # FG1
    "Pulses": ("FG1", "expected_fg1_subgroup", "legumes", False, None),
    "Pulses/soy": ("FG1", "expected_fg1_subgroup", "legumes", False, None),
    "Alternative proteins": (
        "FG1",
        "expected_fg1_subgroup",
        "alternative_protein_sources",
        False,
        None,
    ),
    "Pulses/nuts spreads": (
        "FG1",
        "expected_fg1_subgroup",
        "alternative_protein_sources",
        False,
        None,
    ),
    "Nuts and seeds": (
        "FG1",
        "expected_fg1_subgroup",
        "nuts_seeds",
        False,
        None,
    ),
    "Meat alternatives": (
        "FG1",
        "expected_fg1_subgroup",
        "meat_egg_seafood_alternatives",
        False,
        None,
    ),
    "Seafood alternatives": (
        "FG1",
        "expected_fg1_subgroup",
        "meat_egg_seafood_alternatives",
        False,
        None,
    ),
    "Eggs": ("FG1", "expected_fg1_subgroup", "eggs", False, None),
    "Poultry": ("FG1", "expected_fg1_subgroup", "poultry", False, None),
    "Red meat": ("FG1", "expected_fg1_subgroup", "red_meat", False, None),
    "Processed meat": (
        "FG1",
        "expected_fg1_subgroup",
        "processed_meats_alternatives",
        False,
        None,
    ),
    "Fish and shellfish": (
        "FG1",
        "expected_fg1_subgroup",
        "seafood",
        False,
        None,
    ),
    # FG2
    "Milk": ("FG2", "expected_fg2_subgroup", "other_dairy_animal", False, None),
    "Yogurt": ("FG2", "expected_fg2_subgroup", "other_dairy_animal", False, None),
    "Cream": ("FG2", "expected_fg2_subgroup", "other_dairy_animal", False, None),
    "Cheese": ("FG2", "expected_fg2_subgroup", "cheese", False, None),
    "Milk alternatives": (
        "FG2",
        "expected_fg2_subgroup",
        "dairy_alternative_plant",
        False,
        None,
    ),
    "Yogurt alternatives": (
        "FG2",
        "expected_fg2_subgroup",
        "dairy_alternative_plant",
        False,
        None,
    ),
    "Cheese alternatives": (
        "FG2",
        "expected_fg2_subgroup",
        "dairy_alternative_plant",
        False,
        None,
    ),
    # FG3
    "Plant oils": (
        "FG3",
        "expected_fg3_subgroup",
        "plant_based_fat",
        False,
        None,
    ),
    "Plant fats": (
        "FG3",
        "expected_fg3_subgroup",
        "plant_based_fat",
        False,
        None,
    ),
    "Animal fats": (
        "FG3",
        "expected_fg3_subgroup",
        "animal_based_fat",
        False,
        None,
    ),
    # FG4
    "Fresh vegetables": ("FG4", None, None, False, None),
    "Frozen vegetables": ("FG4", None, None, False, None),
    "Prepared vegetables": ("FG4", None, None, False, None),
    "Fresh fruit": ("FG4", None, None, False, None),
    "Fresh fruit/vegetable": ("FG4", None, None, False, None),
    "Frozen fruit": ("FG4", None, None, False, None),
    "Dried fruit": ("FG4", None, None, False, None),
    "Canned vegetables": ("FG4", None, None, False, None),
    # FG5
    "Whole grains": (
        "FG5",
        "expected_fg5_grain_kind",
        "whole_grain",
        False,
        None,
    ),
    "Refined grains": (
        "FG5",
        "expected_fg5_grain_kind",
        "refined_grain",
        False,
        None,
    ),
    # FG6
    "Potatoes": ("FG6", None, None, False, None),
    "Sweet potatoes": ("FG6", None, None, False, None),
    "Cassava": ("FG6", None, None, False, None),
    # FG7
    "Potato products": (
        "FG7",
        "expected_fg7_kind",
        "plant_based_snack",
        False,
        None,
    ),
    "Savoury snacks": (
        "FG7",
        "expected_fg7_kind",
        "plant_based_snack",
        False,
        None,
    ),
    "Confectionery": (
        "FG7",
        "expected_fg7_kind",
        "plant_based_snack",
        False,
        None,
    ),
    "Baked goods": (
        "FG7",
        "expected_fg7_kind",
        "animal_based_snack",
        False,
        None,
    ),
    "Desserts": (
        "FG7",
        "expected_fg7_kind",
        "animal_based_snack",
        False,
        None,
    ),
    "Snack bars": (
        "FG7",
        "expected_fg7_kind",
        "plant_based_snack",
        False,
        None,
    ),
}


def _composite_from_category(cat: str) -> tuple[str, str] | None:
    """Composite categories carry the bucket in their name."""
    if not cat.startswith("Prepared composite meal/snack"):
        return None
    if "Meat-based" in cat:
        return ("FG1", "meat_based")
    if "Seafood-based" in cat:
        return ("FG1", "seafood_based")
    if "Vegetarian" in cat:
        return ("FG1", "vegetarian")
    if "Vegan" in cat:
        return ("FG1", "vegan")
    return ("FG1", "vegan")  # default fallback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    cases: list[dict] = []
    skipped: list[tuple[str, str]] = []
    distribution: Counter[str] = Counter()
    with args.src.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("product_name") or "").strip()
            raw_cat = (row.get("raw_product_category") or "").strip()
            if not name or not raw_cat:
                continue
            # Composite?
            composite = _composite_from_category(raw_cat)
            if composite is not None:
                fg, bucket = composite
                cases.append(
                    {
                        "product_name": name,
                        "expected_food_group": fg,
                        "expected_is_composite": True,
                        "expected_composite_step1_bucket": bucket,
                        "source_category": raw_cat,
                    }
                )
                distribution[f"composite_{bucket}"] += 1
                continue
            mapping = _CATEGORY_MAP.get(raw_cat)
            if mapping is None:
                skipped.append((name, raw_cat))
                continue
            fg, sub_field, sub_value, is_composite, bucket = mapping
            case: dict = {
                "product_name": name,
                "expected_food_group": fg,
                "expected_is_composite": is_composite,
                "source_category": raw_cat,
            }
            if sub_field and sub_value:
                case[sub_field] = sub_value
            if bucket:
                case["expected_composite_step1_bucket"] = bucket
            cases.append(case)
            distribution[fg] += 1

    payload = {
        "name": "Dataset 100-product (Phase WWF-Q2)",
        "source": args.src.name,
        "cases": cases,
    }
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(cases)} cases to {args.out.name}")
    for k in sorted(distribution):
        print(f"  {k}: {distribution[k]}")
    if skipped:
        print(f"  skipped: {len(skipped)} rows with unknown raw_product_category")
        for n, c in skipped[:5]:
            print(f"    - {n} ({c!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
