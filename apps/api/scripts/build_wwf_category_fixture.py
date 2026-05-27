#!/usr/bin/env python
"""Phase WWF-L — build evaluation fixtures from WWF Category CSV.

Input
-----
The 460-row WWF reference taxonomy (``WWF Category - Feuille 1.csv``)
with columns:

    Food items, Whole product / composite product, Food Group, Food source

Output
------
Four JSON fixtures under ``altera_api/data/audit/``:

  * ``wwf_category_fixture_full.json`` — every parseable row.
  * ``wwf_category_fixture_obvious.json`` — rows where the expected
    food group + subgroup can be inferred with high confidence.
  * ``wwf_category_fixture_edges.json`` — rows where the subgroup is
    ambiguous (top-level only) or the item name is broad.
  * ``wwf_category_fixture_composites.json`` — composite rows.

Each row has the same shape as the curated
``wwf_obvious_fixture.json`` (Phase WWF-D) so it can be fed straight
into ``scripts/evaluate_wwf_classification.py``.

Usage
-----

    .venv/bin/python scripts/build_wwf_category_fixture.py \\
        --src altera_api/data/audit/wwf_category_source.csv

This rebuilds the four output JSON files in
``altera_api/data/audit/``. The script is idempotent.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SRC = _REPO_ROOT / "altera_api" / "data" / "audit" / "wwf_category_source.csv"
_OUT_DIR = _REPO_ROOT / "altera_api" / "data" / "audit"


# ---------------------------------------------------------------------------
# Food-Group / Food-Source mapping
# ---------------------------------------------------------------------------

#: Map CSV ``Food Group`` literal → canonical WWF food group enum value.
_FOOD_GROUP_MAP: dict[str, str] = {
    "food group 1: protein-rich foods": "FG1",
    "food group 2: dairy and dairy alternatives": "FG2",
    "food group 3: fats & oils": "FG3",
    "food group 4: fruits & vegetables": "FG4",
    "food group 5: grains / cereal": "FG5",
    "food group 6: tubers & starchy vegetables": "FG6",
    "food group 7: snacks high in added fats, salt, and sugar": "FG7",
}


# ---------------------------------------------------------------------------
# Subgroup token vocabularies — used to infer the subgroup from the
# Food item name when the CSV doesn't say it directly.
# ---------------------------------------------------------------------------

_FG1_RED_MEAT = (
    "beef",
    "goat",
    "lamb",
    "pork",
    "veal",
    "venison",
    "rabbit",
    "bison",
    "wild boar",
)
_FG1_POULTRY = (
    "chicken",
    "turkey",
    "duck",
    "goose",
    "quail",
    "pheasant",
    "partridge",
    "guinea fowl",
)
_FG1_PROCESSED = (
    "bacon",
    "ham",
    "salami",
    "sausage",
    "saucisse",
    "saucisson",
    "chorizo",
    "merguez",
    "mortadelle",
    "pâté",
    "pate",
    "rillettes",
    "prosciutto",
    "spam",
    "smoked",
    "cured",
    "deli",
    "frankfurters",
    "hot dog",
    "pastrami",
    "corned beef",
)
_FG1_SEAFOOD = (
    "fish",
    "salmon",
    "tuna",
    "cod",
    "trout",
    "sardine",
    "mackerel",
    "anchovy",
    "anchovies",
    "herring",
    "hering",
    "haddock",
    "halibut",
    "sole",
    "prawn",
    "shrimp",
    "crab",
    "lobster",
    "scallop",
    "scallops",
    "mussel",
    "oyster",
    "squid",
    "octopus",
    "calamar",
    "calmar",
    "shellfish",
    "seafood",
    "surimi",
    "saumon",
    "thon",
    "cabillaud",
    "moule",
    "huitre",
    "crevette",
    "poulpe",
    "homard",
)
_FG1_EGGS = ("egg", "eggs", "œuf", "oeuf", "omelette")
_FG1_LEGUMES = (
    "lentil",
    "lentils",
    "lentille",
    "lentilles",
    "chickpea",
    "chickpeas",
    "pois chiche",
    "bean",
    "beans",
    "haricot",
    "fava",
    "faba",
    "fève",
    "feve",
    "lupin",
    "edamame",
    "mung",
    "soy bean",
    "soya bean",
    "soybean",
    "soja",
    "split pea",
    "pulse",
    "pulses",
    "legume",
    "legumes",
)
_FG1_NUTS = (
    "nut",
    "nuts",
    "almond",
    "almonds",
    "amande",
    "cashew",
    "noix",
    "hazelnut",
    "noisette",
    "peanut",
    "cacahuete",
    "pistachio",
    "pistache",
    "macadamia",
    "walnut",
    "pecan",
    "pine nut",
    "pignon",
    "seed",
    "seeds",
    "graine",
    "chia",
    "sesame",
    "flax",
    "lin",
    "sunflower seed",
    "tournesol",
    "pumpkin seed",
    "courge",
    "tahini",
)
_FG1_ALT_PROTEIN = (
    "tofu",
    "tempeh",
    "seitan",
    "mycoprotein",
    "soya texture",
    "soy texture",
    "okara",
    "falafel",
    "hummus",
    "houmous",
)
_FG1_MEAT_ALTERNATIVE = (
    "plant-based",
    "plant based",
    "vegan burger",
    "veggie burger",
    "vegetarian sausage",
    "meat alternative",
    "meat substitute",
    "burger vegan",
    "burger vegetal",
    "vegetable burger",
    "nuggets vegan",
)

_FG2_CHEESE = (
    "cheese",
    "fromage",
    "parmesan",
    "parmigiano",
    "mozzarella",
    "brie",
    "camembert",
    "cheddar",
    "gouda",
    "feta",
    "ricotta",
    "burrata",
    "halloumi",
    "raclette",
    "munster",
    "reblochon",
    "tomme",
    "manchego",
    "pecorino",
    "chèvre",
    "chevre",
    "blue cheese",
    "bleu",
    "fourme",
    "cottage cheese",
    "cream cheese",
    "philadelphia",
    "buffala",
    "stilton",
)
_FG2_OTHER_DAIRY = (
    "milk",
    "lait",
    "yogurt",
    "yoghurt",
    "yaourt",
    "kefir",
    "cream",
    "crème",
    "creme",
    "buttermilk",
    "babeurre",
    "skyr",
    "quark",
    "petit suisse",
    "fromage blanc",
    "fromage frais",
    "sour cream",
    "pudding",
    "custard",
    "creme dessert",
)
_FG2_PLANT_DAIRY = (
    "plant milk",
    "plant-based milk",
    "almond milk",
    "soy milk",
    "soya milk",
    "oat milk",
    "rice milk",
    "coconut milk",
    "lait d'amande",
    "lait de soja",
    "lait d'avoine",
    "lait de coco",
    "lait de riz",
    "boisson amande",
    "boisson soja",
    "boisson avoine",
    "boisson riz",
    "boisson coco",
    "vegan cheese",
    "vegan yogurt",
    "soy yogurt",
    "almond yogurt",
    "coconut yogurt",
    "plant-based cream",
    "plant yogurt",
    "plant cheese",
    "fromage vegan",
    "yaourt vegan",
)

_FG3_ANIMAL_FAT = (
    "butter",
    "beurre",
    "ghee",
    "lard",
    "tallow",
    "suif",
    "duck fat",
    "goose fat",
    "graisse de canard",
    "saindoux",
    "schmaltz",
)
_FG3_PLANT_FAT = (
    "oil",
    "huile",
    "olive oil",
    "sunflower oil",
    "rapeseed oil",
    "canola oil",
    "coconut oil",
    "sesame oil",
    "peanut oil",
    "palm oil",
    "vegetable oil",
    "margarine",
    "margarine vegetale",
    "vegan butter",
    "plant butter",
)

_FG5_WHOLE_GRAIN = (
    "whole grain",
    "whole-grain",
    "wholegrain",
    "wholemeal",
    "whole wheat",
    "whole-wheat",
    "complet",
    "complète",
    "complete",
    "intégral",
    "integrale",
    "brown rice",
    "brown bread",
    "brown",
    "oat",
    "oats",
    "avoine",
    "quinoa",
    "bulgur",
    "boulgour",
    "spelt",
    "épeautre",
    "epeautre",
    "barley",
    "orge",
    "rye",
    "seigle",
    "buckwheat",
    "sarrasin",
    "millet",
    "amaranth",
    "amarante",
    "freekeh",
    "farro",
)
_FG5_REFINED_GRAIN = (
    "white rice",
    "riz blanc",
    "white bread",
    "pain blanc",
    "baguette",
    "spaghetti",
    "penne",
    "tagliatelle",
    "macaroni",
    "fusilli",
    "ravioli",
    "lasagne",
    "lasagna",
    "couscous",
    "semoule",
    "polenta",
    "tortilla",
    "cornflakes",
    "noodle",
    "noodles",
    "udon",
    "soba",
    "ramen",
    "vermicelle",
    "white flour",
    "farine blanche",
    "biscotte",
    "cracker",
    "rice cake",
)

_FG7_ANIMAL_SNACK = (
    "milk chocolate",
    "chocolate milk",
    "chocolat au lait",
    "tablette lait",
    "ice cream",
    "crème glacée",
    "creme glacee",
    "gelato",
    "frozen yogurt",
    "yogurt drink",
    "mousse",
    "panna cotta",
    "custard dessert",
    "creme dessert sucree",
    "tiramisu",
    "cheesecake",
    "honey",
    "miel",
    "marshmallow",
    "guimauve",
    "dulce de leche",
    "nutella",
    "milk-based",
    "butter cookies",
    "biscuit beurre",
    "croissant",
    "pain au chocolat",
    "brioche",
    "doughnut",
    "donut",
    "muffin",
    "cake",
    "cupcake",
    "madeleine",
    "financier",
    "gateau",
    "pancake",
    "waffle",
    "gaufre",
    "crepe",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase + collapse whitespace; preserves accents so the
    French ``è``/``é``/``œ`` checks remain readable."""
    return re.sub(r"\s+", " ", text.lower()).strip()


def _matches_any(name: str, tokens: tuple[str, ...]) -> bool:
    n = _normalise(name)
    for t in tokens:
        if t in n:
            return True
    return False


def _infer_fg1_subgroup(name: str) -> tuple[str | None, bool]:
    """Return (subgroup, confident). ``confident=False`` marks the
    row as ambiguous — the fixture builder records the row but the
    eval scoring should soften the strict-subgroup check."""
    n = _normalise(name)
    # Order matters: plant-based meat alternative beats the meat
    # token it contains.
    if _matches_any(n, _FG1_MEAT_ALTERNATIVE):
        return "meat_egg_seafood_alternatives", True
    if _matches_any(n, _FG1_ALT_PROTEIN):
        return "alternative_protein_sources", True
    if _matches_any(n, _FG1_PROCESSED):
        return "processed_meats_alternatives", True
    if _matches_any(n, _FG1_SEAFOOD):
        return "seafood", True
    if _matches_any(n, _FG1_POULTRY):
        return "poultry", True
    if _matches_any(n, _FG1_RED_MEAT):
        return "red_meat", True
    if _matches_any(n, _FG1_EGGS):
        return "eggs", True
    if _matches_any(n, _FG1_LEGUMES):
        return "legumes", True
    if _matches_any(n, _FG1_NUTS):
        return "nuts_seeds", True
    return None, False


def _infer_fg2(name: str) -> tuple[str | None, bool]:
    if _matches_any(name, _FG2_PLANT_DAIRY):
        return "dairy_alternative_plant", True
    if _matches_any(name, _FG2_CHEESE):
        return "cheese", True
    if _matches_any(name, _FG2_OTHER_DAIRY):
        return "other_dairy_animal", True
    return None, False


def _infer_fg3(name: str) -> tuple[str | None, bool]:
    if _matches_any(name, _FG3_ANIMAL_FAT):
        return "animal_based_fat", True
    if _matches_any(name, _FG3_PLANT_FAT):
        return "plant_based_fat", True
    return None, False


def _infer_fg5(name: str) -> tuple[str | None, bool]:
    if _matches_any(name, _FG5_WHOLE_GRAIN):
        return "whole_grain", True
    if _matches_any(name, _FG5_REFINED_GRAIN):
        return "refined_grain", True
    return None, False


def _infer_fg7(name: str) -> tuple[str | None, bool]:
    if _matches_any(name, _FG7_ANIMAL_SNACK):
        return "animal_based_snack", True
    # Default plant-based snack for FG7 rows we can't otherwise place.
    return "plant_based_snack", False


def _infer_composite_bucket(name: str) -> tuple[str | None, bool]:
    """Best-effort bucket inference from the dish name. ``confident``
    is True when at least one meaty/seafoody/dairy anchor is present;
    False when we fall through to vegan as a generic default."""
    n = _normalise(name)
    if _matches_any(
        n,
        _FG1_RED_MEAT
        + _FG1_POULTRY
        + _FG1_PROCESSED
        + ("bolognaise", "bolognese", "carbonara", "cassoulet", "parmentier"),
    ):
        return "meat_based", True
    if _matches_any(n, _FG1_SEAFOOD + ("fruits de mer", "frutti di mare")):
        return "seafood_based", True
    if _matches_any(
        n,
        _FG2_CHEESE + _FG2_OTHER_DAIRY + _FG1_EGGS + ("margherita", "à la crème"),
    ):
        return "vegetarian", True
    return "vegan", False


# ---------------------------------------------------------------------------
# Row → fixture-case conversion
# ---------------------------------------------------------------------------


@dataclass
class FixtureCase:
    product_name: str
    expected_food_group: str
    expected_is_composite: bool = False
    expected_fg1_subgroup: str | None = None
    expected_fg2_subgroup: str | None = None
    expected_fg3_subgroup: str | None = None
    expected_fg5_grain_kind: str | None = None
    expected_fg7_kind: str | None = None
    expected_composite_step1_bucket: str | None = None
    expected_source: str = "unknown"
    confidence_of_label: str = "exact"
    bucket_ambiguous: bool = False
    source_row_index: int = 0
    source_food_item: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "product_name": self.product_name,
            "expected_food_group": self.expected_food_group,
            "expected_is_composite": self.expected_is_composite,
            "expected_source": self.expected_source,
            "confidence_of_label": self.confidence_of_label,
            "source_row_index": self.source_row_index,
            "source_food_item": self.source_food_item,
        }
        for key, value in (
            ("expected_fg1_subgroup", self.expected_fg1_subgroup),
            ("expected_fg2_subgroup", self.expected_fg2_subgroup),
            ("expected_fg3_subgroup", self.expected_fg3_subgroup),
            ("expected_fg5_grain_kind", self.expected_fg5_grain_kind),
            ("expected_fg7_kind", self.expected_fg7_kind),
            ("expected_composite_step1_bucket", self.expected_composite_step1_bucket),
        ):
            if value is not None:
                d[key] = value
        if self.bucket_ambiguous:
            d["bucket_ambiguous"] = True
        return d


def _row_to_case(row: dict[str, str], row_index: int) -> FixtureCase | None:
    """Convert one CSV row into a fixture case. Returns ``None`` for
    rows we can't usefully classify (Food Group = ``n/a`` etc.)."""
    raw_name = (row.get("Food items") or "").strip()
    if not raw_name:
        return None
    raw_group = (row.get("Food Group") or "").strip().lower()
    raw_kind = (row.get("Whole product / composite product") or "").strip().lower()
    raw_source = (row.get("Food source") or "").strip().lower()

    # Composite rows have Food Group blank or list of buckets in
    # Food source ("Meat-based, seafood-based, vegetarian or vegan").
    is_composite = "composite" in raw_kind

    if is_composite:
        bucket, bucket_confident = _infer_composite_bucket(raw_name)
        # By WWF convention composites attach to FG1 in our domain.
        return FixtureCase(
            product_name=raw_name,
            expected_food_group="FG1",
            expected_is_composite=True,
            expected_composite_step1_bucket=bucket,
            expected_source="composite",
            confidence_of_label="partial" if not bucket_confident else "exact",
            bucket_ambiguous=not bucket_confident,
            source_row_index=row_index,
            source_food_item=raw_name,
        )

    fg = _FOOD_GROUP_MAP.get(raw_group)
    if fg is None:
        # "n/a" — out_of_scope reference rows.
        if raw_group == "n/a":
            return FixtureCase(
                product_name=raw_name,
                expected_food_group="out_of_scope",
                expected_source=raw_source,
                confidence_of_label="exact",
                source_row_index=row_index,
                source_food_item=raw_name,
            )
        return None

    case = FixtureCase(
        product_name=raw_name,
        expected_food_group=fg,
        expected_source=raw_source,
        confidence_of_label="exact",
        source_row_index=row_index,
        source_food_item=raw_name,
    )

    # Per-food-group subgroup inference.
    if fg == "FG1":
        sub, confident = _infer_fg1_subgroup(raw_name)
        case.expected_fg1_subgroup = sub
        case.confidence_of_label = "exact" if confident else "ambiguous"
    elif fg == "FG2":
        sub, confident = _infer_fg2(raw_name)
        case.expected_fg2_subgroup = sub
        case.confidence_of_label = "exact" if confident else "ambiguous"
    elif fg == "FG3":
        sub, confident = _infer_fg3(raw_name)
        case.expected_fg3_subgroup = sub
        case.confidence_of_label = "exact" if confident else "ambiguous"
    elif fg == "FG5":
        sub, confident = _infer_fg5(raw_name)
        case.expected_fg5_grain_kind = sub
        case.confidence_of_label = "exact" if confident else "ambiguous"
    elif fg == "FG7":
        sub, confident = _infer_fg7(raw_name)
        case.expected_fg7_kind = sub
        case.confidence_of_label = "exact" if confident else "partial"
    # FG4 and FG6 have no subgroup.

    return case


# ---------------------------------------------------------------------------
# Bucketing — split the cases into the four output fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Buckets:
    full: list[FixtureCase] = field(default_factory=list)
    obvious: list[FixtureCase] = field(default_factory=list)
    edges: list[FixtureCase] = field(default_factory=list)
    composites: list[FixtureCase] = field(default_factory=list)


def _bucket(case: FixtureCase, buckets: _Buckets) -> None:
    buckets.full.append(case)
    if case.expected_is_composite:
        buckets.composites.append(case)
        return
    if case.confidence_of_label == "exact":
        buckets.obvious.append(case)
    else:
        buckets.edges.append(case)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_fixture(path: Path, name: str, cases: list[FixtureCase]) -> None:
    payload = {
        "name": name,
        "source": "WWF Category - Feuille 1.csv",
        "cases": [c.to_dict() for c in cases],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=_DEFAULT_SRC)
    parser.add_argument("--out-dir", type=Path, default=_OUT_DIR)
    args = parser.parse_args()

    src: Path = args.src
    if not src.exists():
        print(f"ERR: source CSV not found at {src}", flush=True)
        return 2

    buckets = _Buckets()
    distribution: Counter[str] = Counter()
    with src.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader, start=2):
            case = _row_to_case(row, idx)
            if case is None:
                continue
            _bucket(case, buckets)
            distribution[case.expected_food_group] += 1

    out: Path = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    _write_fixture(
        out / "wwf_category_fixture_full.json",
        "WWF Category — full (Phase WWF-L)",
        buckets.full,
    )
    _write_fixture(
        out / "wwf_category_fixture_obvious.json",
        "WWF Category — obvious subgroup-confident (Phase WWF-L)",
        buckets.obvious,
    )
    _write_fixture(
        out / "wwf_category_fixture_edges.json",
        "WWF Category — ambiguous subgroup (Phase WWF-L)",
        buckets.edges,
    )
    _write_fixture(
        out / "wwf_category_fixture_composites.json",
        "WWF Category — composites (Phase WWF-L)",
        buckets.composites,
    )

    total = len(buckets.full)
    print(f"Built fixtures from {src.name}: {total} parsable rows")
    for fg in sorted(distribution):
        print(f"  {fg}: {distribution[fg]}")
    print(f"  obvious cases : {len(buckets.obvious)}")
    print(f"  edge cases    : {len(buckets.edges)}")
    print(f"  composites    : {len(buckets.composites)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
