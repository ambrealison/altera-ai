"""
Deterministic mock dataset generator for Altera AI local development.

Usage:
    python scripts/fixtures/generate_mock_datasets.py

Output:
    apps/api/altera_api/sample_data/pt_mock_foodservice_small.csv   (~200 rows)
    apps/api/altera_api/sample_data/wwf_mock_retailer_small.csv     (~200 rows)

Seed is fixed so output is byte-identical across machines.
All brand names and product names are fictional [fixture data].
"""
import csv
import random
from decimal import Decimal
from pathlib import Path

SEED = 42
RNG = random.Random(SEED)

OUT_DIR = Path(__file__).parent.parent.parent / "apps" / "api" / "altera_api" / "sample_data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------
BRANDS = [
    "GreenLeaf [fixture]", "FarmFirst [fixture]", "DairyFarm [fixture]",
    "SeaCatch [fixture]", "HomeChef [fixture]", "BreadCo [fixture]",
    "WokBox [fixture]", "NutBarn [fixture]", "OatlyFix [fixture]",
    "TofuCo [fixture]", "GardenFresh [fixture]", "ButcherBest [fixture]",
    "MedFoods [fixture]", "SpiceCo [fixture]", "QuickEat [fixture]",
]

LANGUAGES = ["en", "en", "en", "en", "fr", "de", "nl"]
COUNTRIES = ["GB", "GB", "GB", "GB", "FR", "DE", "NL"]
RETAIL_CHANNELS = ["fresh", "grocery_ambient", "frozen"]
LABELS_OPTIONS = [
    "", "vegan", "vegetarian", "organic", "free_range",
    "vegan|organic", "vegetarian|organic", "free_range|organic",
    "gluten_free", "vegan|gluten_free",
]

# ---------------------------------------------------------------------------
# Protein Tracker product templates (foodservice context)
# ---------------------------------------------------------------------------
PT_TEMPLATES = [
    # (product_name, retailer_category, retailer_subcategory, is_own_brand,
    #  weight_kg_range, protein_pct_range, protein_source, expected_pt_group, labels)
    ("Grilled Chicken Breast {}g", "Poultry", "Fresh Chicken", False, (0.15, 0.35), (19.0, 23.0), "label", "animal_core", "free_range"),
    ("Beef Burger {}g", "Red Meat", "Burgers", False, (0.10, 0.25), (16.0, 20.0), "label", "animal_core", ""),
    ("Atlantic Salmon {}g", "Fish", "Fresh Fish", False, (0.10, 0.25), (18.0, 22.0), "label", "animal_core", ""),
    ("Red Lentil Soup {}g", "Soups", "Pulse Soups", True, (0.30, 0.50), (4.0, 6.0), "reference_db", "plant_based_core", "vegan"),
    ("Pea Protein Powder {}g", "Sports Nutrition", "Protein Powders", False, (0.50, 1.00), (70.0, 85.0), "label", "plant_based_core", "vegan"),
    ("Mixed Bean Salad {}g", "Salads", "Bean Salads", True, (0.20, 0.35), (5.0, 8.0), "reference_db", "plant_based_core", "vegan"),
    ("Edamame Beans {}g", "Frozen Vegetables", "Edamame", False, (0.30, 0.50), (9.0, 12.0), "reference_db", "plant_based_core", "vegan"),
    ("Cheddar Cheese {}g", "Dairy", "Cheese", False, (0.20, 0.50), (23.0, 26.0), "label", "animal_core", "vegetarian"),
    ("Greek Yoghurt {}g", "Dairy", "Yoghurt", False, (0.15, 0.50), (8.0, 12.0), "label", "animal_core", "vegetarian"),
    ("Oat Milk {}ml", "Dairy Alternatives", "Plant Milks", False, (0.75, 1.50), (0.5, 1.5), "label", "plant_based_non_core", "vegan"),
    ("Soya Mince {}g", "Meat Alternatives", "Soya Products", True, (0.25, 0.40), (45.0, 55.0), "label", "plant_based_core", "vegan"),
    ("Tofu Block {}g", "Meat Alternatives", "Tofu", False, (0.28, 0.40), (7.0, 10.0), "label", "plant_based_core", "vegan"),
    ("Chicken and Vegetable Pie {}g", "Ready Meals", "Pies", True, (0.30, 0.45), (8.0, 12.0), "label", "composite_products", ""),
    ("Beef Lasagna {}g", "Ready Meals", "Pasta Dishes", True, (0.35, 0.50), (7.0, 10.0), "label", "composite_products", ""),
    ("Prawn Stir Fry {}g", "Ready Meals", "Asian", False, (0.25, 0.40), (7.0, 11.0), "label", "composite_products", ""),
    ("Veggie Shepherd Pie {}g", "Ready Meals", "Vegetarian Meals", True, (0.35, 0.50), (6.0, 9.0), "label", "composite_products", "vegetarian"),
    ("Smoked Salmon {}g", "Fish", "Smoked Fish", False, (0.10, 0.20), (20.0, 25.0), "label", "animal_core", ""),
    ("Pork Sausages {}g", "Pork", "Sausages", False, (0.30, 0.45), (12.0, 16.0), "label", "animal_core", ""),
    ("Tempeh {}g", "Meat Alternatives", "Fermented Soy", False, (0.15, 0.25), (16.0, 22.0), "label", "plant_based_core", "vegan"),
    ("Hummus {}g", "Dips", "Hummus", False, (0.15, 0.25), (5.0, 8.0), "label", "plant_based_core", "vegan"),
]

# ---------------------------------------------------------------------------
# WWF product templates (grocery retail context)
# ---------------------------------------------------------------------------
WWF_TEMPLATES = [
    # (product_name, retailer_category, retailer_subcategory, is_own_brand,
    #  retail_channel, weight_kg_range, wwf_is_composite, wwf_food_group, labels)
    ("Beef Mince {}g", "Red Meat", "Fresh Beef", False, "fresh", (0.40, 0.75), False, "FG1_red_meat", ""),
    ("Chicken Breast {}g", "Poultry", "Fresh Chicken", False, "fresh", (0.30, 0.60), False, "FG1_poultry", "free_range"),
    ("Atlantic Salmon {}g", "Fish", "Fresh Fish", False, "fresh", (0.15, 0.25), False, "FG1_seafood", ""),
    ("Free Range Eggs {}pk", "Eggs", "Free Range", False, "grocery_ambient", (0.25, 0.45), False, "FG1_eggs", "free_range|vegetarian"),
    ("Whole Milk {}L", "Dairy", "Fresh Milk", False, "fresh", (1.00, 2.00), False, "FG2_dairy_other", "vegetarian"),
    ("Cheddar Cheese {}g", "Dairy", "Hard Cheese", False, "fresh", (0.20, 0.50), False, "FG2_cheese", "vegetarian"),
    ("Brie {}g", "Dairy", "Soft Cheese", False, "fresh", (0.15, 0.25), False, "FG2_cheese", "vegetarian"),
    ("Oat Milk {}L", "Dairy Alternatives", "Plant Milks", False, "grocery_ambient", (0.75, 1.50), False, "FG2_plant_alt", "vegan"),
    ("Soy Yoghurt {}g", "Dairy Alternatives", "Plant Yoghurts", False, "grocery_ambient", (0.35, 0.50), False, "FG2_plant_alt", "vegan"),
    ("Olive Oil {}ml", "Oils", "Olive Oil", False, "grocery_ambient", (0.40, 0.75), False, "FG3_plant_fat", "vegan"),
    ("Butter {}g", "Dairy", "Butter", False, "fresh", (0.20, 0.50), False, "FG3_animal_fat", "vegetarian"),
    ("Frozen Peas {}g", "Frozen Vegetables", "Peas", True, "frozen", (0.75, 1.25), False, "FG4", "vegan"),
    ("Broccoli {}g", "Fresh Vegetables", "Brassicas", True, "fresh", (0.30, 0.60), False, "FG4", "vegan"),
    ("Sliced Wholegrain Bread {}g", "Bakery", "Sliced Bread", True, "grocery_ambient", (0.60, 0.90), False, "FG5_whole", "vegan"),
    ("White Pasta {}g", "Dry Goods", "Pasta", False, "grocery_ambient", (0.35, 0.60), False, "FG5_refined", "vegan"),
    ("Red Kidney Beans {}g", "Tinned Pulses", "Kidney Beans", True, "grocery_ambient", (0.35, 0.45), False, "FG1_legumes", "vegan"),
    ("Mixed Nuts {}g", "Snacks", "Nuts", False, "grocery_ambient", (0.15, 0.25), False, "FG1_nuts_seeds", "vegan"),
    ("Dark Chocolate {}g", "Confectionery", "Dark Chocolate", False, "grocery_ambient", (0.09, 0.12), False, "FG7_plant_snack", "vegan"),
    ("Beef and Potato Stew {}g", "Ready Meals", "Stews", True, "fresh", (0.35, 0.50), True, "composite_meat_based", ""),
    ("Chicken Curry {}g", "Ready Meals", "Curry", True, "fresh", (0.30, 0.45), True, "composite_meat_based", ""),
    ("Vegan Lasagna {}g", "Ready Meals", "Vegan Meals", True, "fresh", (0.35, 0.45), True, "composite_vegan", "vegan"),
    ("Vegetable Biryani {}g", "Ready Meals", "Rice Dishes", True, "grocery_ambient", (0.30, 0.45), True, "composite_vegetarian", "vegan"),
    ("Tuna Pasta Bake {}g", "Ready Meals", "Pasta Dishes", False, "grocery_ambient", (0.35, 0.42), True, "composite_seafood_based", ""),
    ("Prawn Noodles {}g", "Ready Meals", "Asian", False, "fresh", (0.28, 0.38), True, "composite_seafood_based", ""),
]

STEP1_BUCKET_MAP = {
    "composite_meat_based":     "meat_based",
    "composite_seafood_based":  "seafood_based",
    "composite_vegetarian":     "vegetarian",
    "composite_vegan":          "vegan",
}


def rnd_weight(lo: float, hi: float) -> float:
    raw = RNG.uniform(lo, hi)
    # Round to nearest 50g for realism
    return round(round(raw / 0.05) * 0.05, 3)


def rnd_items(lo: int, hi: int) -> int:
    return RNG.randint(lo, hi)


# ---------------------------------------------------------------------------
# Generate PT mock
# ---------------------------------------------------------------------------
def generate_pt(n: int = 200) -> None:
    fieldnames = [
        "external_product_id", "product_name", "brand",
        "retailer_category", "retailer_subcategory",
        "ingredients_text", "labels", "language", "country",
        "is_own_brand", "weight_per_item_kg", "items_purchased",
        "protein_pct", "protein_source",
    ]
    rows = []
    for i in range(n):
        tmpl = PT_TEMPLATES[i % len(PT_TEMPLATES)]
        name_tpl, cat, subcat, own, w_range, prot_range, p_src, _, labels = tmpl
        w = rnd_weight(*w_range)
        weight_g = int(w * 1000)
        lang_idx = RNG.randint(0, len(LANGUAGES) - 1)
        prot = round(RNG.uniform(*prot_range), 1)
        rows.append({
            "external_product_id": f"PT-MOCK-{i+1:04d}",
            "product_name": name_tpl.format(weight_g),
            "brand": RNG.choice(BRANDS),
            "retailer_category": cat,
            "retailer_subcategory": subcat,
            "ingredients_text": "",
            "labels": labels,
            "language": LANGUAGES[lang_idx],
            "country": COUNTRIES[lang_idx],
            "is_own_brand": str(own).lower(),
            "weight_per_item_kg": w,
            "items_purchased": rnd_items(200, 15000),
            "protein_pct": prot,
            "protein_source": p_src,
        })
    path = OUT_DIR / "pt_mock_foodservice_small.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written {len(rows)} rows → {path}")


# ---------------------------------------------------------------------------
# Generate WWF mock
# ---------------------------------------------------------------------------
def generate_wwf(n: int = 200) -> None:
    fieldnames = [
        "external_product_id", "product_name", "brand",
        "retailer_category", "retailer_subcategory",
        "ingredients_text", "labels", "language", "country",
        "is_own_brand", "retail_channel", "weight_per_item_kg",
        "items_sold", "wwf_is_composite",
    ]
    rows = []
    for i in range(n):
        tmpl = WWF_TEMPLATES[i % len(WWF_TEMPLATES)]
        name_tpl, cat, subcat, own, channel, w_range, is_composite, _, labels = tmpl
        w = rnd_weight(*w_range)
        weight_g = int(w * 1000)
        lang_idx = RNG.randint(0, len(LANGUAGES) - 1)
        rows.append({
            "external_product_id": f"WWF-MOCK-{i+1:04d}",
            "product_name": name_tpl.format(weight_g),
            "brand": RNG.choice(BRANDS),
            "retailer_category": cat,
            "retailer_subcategory": subcat,
            "ingredients_text": "",
            "labels": labels,
            "language": LANGUAGES[lang_idx],
            "country": COUNTRIES[lang_idx],
            "is_own_brand": str(own).lower(),
            "retail_channel": channel,
            "weight_per_item_kg": w,
            "items_sold": rnd_items(300, 20000),
            "wwf_is_composite": str(is_composite).lower(),
        })
    path = OUT_DIR / "wwf_mock_retailer_small.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written {len(rows)} rows → {path}")


if __name__ == "__main__":
    generate_pt()
    generate_wwf()
    print("Done.")
