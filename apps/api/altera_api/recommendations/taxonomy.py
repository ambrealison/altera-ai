"""Static action taxonomy for the recommendation engine (Phase 25A).

Each entry defines the action's description, expected direction, applicable
methodologies, caveats, and audience flags. The engine uses these definitions
to populate recommendation fields deterministically.

No LLM involvement. No numeric impact estimates. All descriptions and
caveats are written to avoid unsupported health or nutrition claims.
"""

from __future__ import annotations

from typing import Final

_PT = "protein_tracker"
_WWF = "wwf"
_BOTH = (_PT, _WWF)

#: Each entry keyed by action_type string.
ACTION_TAXONOMY: Final[dict[str, dict]] = {
    "increase_plant_core_share": {
        "applicable_methodologies": (_PT,),
        "category": "pt_protein_shift",
        "description": (
            "Increase the share of plant-based core protein (legumes, nuts, seeds, "
            "plant-based alternatives) in the product range."
        ),
        "expected_direction": (
            "Likely increases plant-source protein share, "
            "improving the Protein Tracker plant ratio."
        ),
        "caveats": [
            "Impact depends on sales volume of existing and new products.",
            "Range changes require supplier, category, and buyer alignment.",
            "No numeric impact estimate is provided in Phase 25A.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "reduce_animal_core_dependency": {
        "applicable_methodologies": (_PT,),
        "category": "pt_protein_shift",
        "description": (
            "Review the animal-core product range for categories where plant-based "
            "alternatives exist and where reduction or substitution is feasible."
        ),
        "expected_direction": (
            "Likely reduces animal-source protein share, "
            "improving the Protein Tracker plant ratio."
        ),
        "caveats": [
            "Suitable where alternative products exist in the relevant category.",
            "No claim is made about any specific product or supplier.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "improve_composite_breakdown": {
        "applicable_methodologies": (_PT,),
        "category": "composite_quality",
        "description": (
            "Improve classification of composite products by providing per-product "
            "ingredient split data (plant % / animal % of protein)."
        ),
        "expected_direction": (
            "Improves methodological accuracy; removes reliance on the 50/50 "
            "default split for composite products."
        ),
        "caveats": [
            "Requires per-product recipe or formulation data.",
            "Does not change the underlying methodology formula.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "improve_data_quality": {
        "applicable_methodologies": _BOTH,
        "category": "data_quality",
        "description": (
            "Address data quality gaps (high unknown rate, AI classification share, "
            "or blocking upload errors) before setting or reporting against targets."
        ),
        "expected_direction": (
            "Improves methodological confidence and reduces uncertainty level."
        ),
        "caveats": [
            "Recommendations are directional; specific actions depend on the gap type.",
            "Altera can assist with taxonomy rule improvement or manual QA.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "enrich_missing_nutrition": {
        "applicable_methodologies": (_PT,),
        "category": "enrichment",
        "description": (
            "Provide label-level protein % for products currently missing this field, "
            "or apply stored enrichment records in the next calculation run."
        ),
        "expected_direction": (
            "Increases the number of products included in protein totals; "
            "improves calculation completeness."
        ),
        "caveats": [
            "Only applies to Protein Tracker runs.",
            "Enriched values are disclosed in the coverage caveats when applied.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "review_high_impact_unknowns": {
        "applicable_methodologies": _BOTH,
        "category": "data_quality",
        "description": (
            "Manually review unknown-classified products to assign them to a "
            "methodology group, reducing the unknown share."
        ),
        "expected_direction": (
            "Reduces unknown product share, improving coverage and confidence."
        ),
        "caveats": [
            "Manual review requires access to product information.",
            "Items can be queued for Altera methodology team review.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "collect_step2_ingredient_data": {
        "applicable_methodologies": (_WWF,),
        "category": "composite_quality",
        "description": (
            "Upload WWF Step 2 ingredient data for own-brand composite products "
            "to replace whole-product-weight Step 1 attribution with ingredient-level "
            "food group attribution."
        ),
        "expected_direction": (
            "Improves WWF food group breakdown accuracy for own-brand composites; "
            "may affect FG1 plant/animal split and whole-diet figures."
        ),
        "caveats": [
            "Applies only to own-brand composites; branded composites remain at Step 1.",
            "Requires recipe or bill-of-materials data.",
            "Step 1 totals are always reported alongside Step 2.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "promote_legume_products": {
        "applicable_methodologies": (_WWF,),
        "category": "wwf_food_group",
        "description": (
            "Consider expanding the legume and plant-protein assortment within FG1 "
            "to diversify the protein-rich food group towards plant sources."
        ),
        "expected_direction": (
            "Likely increases FG1 plant-source share, "
            "improving the WWF protein transition metric."
        ),
        "caveats": [
            "WWF measures product weight, not protein content.",
            "No claim is made about specific health or nutrition outcomes.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "reformulate_composites": {
        "applicable_methodologies": _BOTH,
        "category": "composite_quality",
        "description": (
            "Explore reformulation opportunities for composite products to reduce "
            "animal-source ingredients and increase plant-source content."
        ),
        "expected_direction": (
            "Directionally improves plant/animal ratio for composite product categories."
        ),
        "caveats": [
            "Reformulation requires supplier and technical collaboration.",
            "No specific product is named; this is a category-level signal.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "replace_or_rebalance_category": {
        "applicable_methodologies": _BOTH,
        "category": "wwf_food_group",
        "description": (
            "Review dominant food groups or subgroups for rebalancing opportunities "
            "towards Planetary Health Diet reference proportions."
        ),
        "expected_direction": (
            "Directionally moves food group shares closer to PHD reference proportions."
        ),
        "caveats": [
            "PHD reference proportions are used as benchmark only.",
            "No specific product or supplier is named.",
        ],
        "client_facing": True,
        "altera_only": False,
    },
    "create_category_target": {
        "applicable_methodologies": _BOTH,
        "category": "data_quality",
        "description": (
            "Once data quality is sufficient (low uncertainty), work with the Altera "
            "methodology team to set a category-level target for the next reporting period."
        ),
        "expected_direction": (
            "Enables tracking of year-on-year improvement against a defined baseline."
        ),
        "caveats": [
            "Target-setting should follow, not precede, data quality resolution.",
            "Altera facilitates; targets are agreed with the client.",
        ],
        "client_facing": False,
        "altera_only": True,
    },
}
