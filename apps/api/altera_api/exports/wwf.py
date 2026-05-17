"""WWF exporters (CSV / JSON / Markdown)."""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from altera_api.domain.wwf import (
    WWFCalculationRow,
    WWFCalculationSummary,
    WWFCompositeIngredient,
    WWFFoodGroup,
)
from altera_api.exports.common import (
    CSV_ENCODING,
    ExportClassificationMeta,
    ExportProductMaster,
    RunMetadata,
    format_decimal,
)


@dataclass(frozen=True)
class WWFExportContext:
    run: RunMetadata
    summary: WWFCalculationSummary
    rows: tuple[WWFCalculationRow, ...]
    products: Mapping[UUID, ExportProductMaster]
    classifications: Mapping[UUID, ExportClassificationMeta]
    #: Per-row physical quantities for the CSV. Missing → blank.
    items_sold: Mapping[UUID, object] = None  # type: ignore[assignment]
    weights_per_item: Mapping[UUID, object] = None  # type: ignore[assignment]
    ingredients_by_product: Mapping[UUID, Sequence[WWFCompositeIngredient]] = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
_WWF_CSV_COLUMNS: tuple[str, ...] = (
    "run_id",
    "methodology",
    "methodology_version",
    "methodology_source_edition",
    "taxonomy_version",
    "rules_version",
    "reporting_period_label",
    "product_id",
    "external_product_id",
    "product_name",
    "brand",
    "is_own_brand",
    "retail_channel",
    "wwf_food_group",
    "wwf_subgroup",
    "wwf_is_composite",
    "wwf_composite_step1_bucket",
    "weight_per_item_kg",
    "items_sold",
    "weight_kg",
    "weight_kg_dairy_equiv",
    "wwf_step2_ingredient_weights_json",
    "classification_source",
    "classification_confidence",
    "classification_rule_id",
    "classification_ai_model",
    "classification_reviewer_user_id",
)


def _ingredients_to_inline_json(
    ingredients: Sequence[WWFCompositeIngredient],
) -> str:
    payload = [
        {
            "food_group": ing.food_group.value,
            "subgroup": (
                ing.fg1_subgroup.value
                if ing.fg1_subgroup is not None
                else (ing.fg2_subgroup.value if ing.fg2_subgroup is not None else None)
            ),
            "ingredient_weight_kg_per_item": format_decimal(
                ing.ingredient_weight_kg_per_item
            ),
        }
        for ing in ingredients
    ]
    return json.dumps(payload, separators=(",", ":"))


def render_wwf_csv(ctx: WWFExportContext) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_WWF_CSV_COLUMNS)

    s = ctx.summary
    items_sold = ctx.items_sold or {}
    weights = ctx.weights_per_item or {}
    ingredients_by_product = ctx.ingredients_by_product or {}

    for row in ctx.rows:
        product = ctx.products.get(row.product_id)
        classification = ctx.classifications.get(row.product_id)
        ings = ingredients_by_product.get(row.product_id)
        writer.writerow(
            [
                str(s.run_id),
                s.methodology.value,
                s.methodology_version,
                s.methodology_source_edition,
                s.taxonomy_version,
                s.rules_version,
                s.reporting_period_label,
                str(row.product_id),
                product.external_product_id if product else "",
                product.product_name if product else "",
                (product.brand or "") if product else "",
                (
                    "true"
                    if (product and product.is_own_brand is True)
                    else ("false" if (product and product.is_own_brand is False) else "")
                ),
                (product.retail_channel.value if product and product.retail_channel else ""),
                row.wwf_food_group.value,
                row.wwf_subgroup_label or "",
                "true" if row.wwf_is_composite else "false",
                row.wwf_composite_step1_bucket.value
                if row.wwf_composite_step1_bucket
                else "",
                format_decimal(weights.get(row.product_id)),  # type: ignore[arg-type]
                format_decimal(items_sold.get(row.product_id)),  # type: ignore[arg-type]
                format_decimal(row.weight_kg),
                format_decimal(row.weight_kg_dairy_equiv),
                _ingredients_to_inline_json(ings) if ings else "",
                classification.source.value if classification else "",
                format_decimal(classification.confidence) if classification else "",
                classification.rule_id if classification and classification.rule_id else "",
                classification.ai_model if classification and classification.ai_model else "",
                str(classification.reviewer_user_id)
                if classification and classification.reviewer_user_id
                else "",
            ]
        )
    return buffer.getvalue().encode(CSV_ENCODING)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def render_wwf_json(ctx: WWFExportContext) -> str:
    s = ctx.summary
    by_food_group = {
        a.food_group.value: {
            "weight_kg": format_decimal(a.weight_kg),
            "weight_kg_dairy_equiv": format_decimal(a.weight_kg_dairy_equiv) or None,
            "share_pct": format_decimal(a.share_pct),
            "phd_share_pct": format_decimal(a.phd_reference_share_pct) or None,
        }
        for a in s.per_food_group
    }

    composite_share = (
        format_decimal(
            (s.composites_total_weight_kg * 100) / s.total_sales_weight_in_scope_kg
        )
        if s.total_sales_weight_in_scope_kg > 0
        else None
    )

    items_sold = ctx.items_sold or {}
    weights = ctx.weights_per_item or {}
    ingredients_by_product = ctx.ingredients_by_product or {}
    rows_json = []
    for row in ctx.rows:
        product = ctx.products.get(row.product_id)
        classification = ctx.classifications.get(row.product_id)
        ings = ingredients_by_product.get(row.product_id)
        rows_json.append(
            {
                "product_id": str(row.product_id),
                "external_product_id": product.external_product_id if product else None,
                "product_name": product.product_name if product else None,
                "brand": product.brand if product else None,
                "is_own_brand": product.is_own_brand if product else None,
                "retail_channel": (
                    product.retail_channel.value
                    if product and product.retail_channel
                    else None
                ),
                "wwf_food_group": row.wwf_food_group.value,
                "wwf_subgroup": row.wwf_subgroup_label,
                "wwf_is_composite": row.wwf_is_composite,
                "wwf_composite_step1_bucket": (
                    row.wwf_composite_step1_bucket.value
                    if row.wwf_composite_step1_bucket
                    else None
                ),
                "in_scope": row.in_scope,
                "weight_per_item_kg": format_decimal(weights.get(row.product_id)) or None,
                "items_sold": format_decimal(items_sold.get(row.product_id)) or None,
                "weight_kg": format_decimal(row.weight_kg),
                "weight_kg_dairy_equiv": format_decimal(row.weight_kg_dairy_equiv) or None,
                "step2_ingredient_weights": (
                    [
                        {
                            "food_group": ing.food_group.value,
                            "fg1_subgroup": (
                                ing.fg1_subgroup.value if ing.fg1_subgroup else None
                            ),
                            "fg2_subgroup": (
                                ing.fg2_subgroup.value if ing.fg2_subgroup else None
                            ),
                            "ingredient_weight_kg_per_item": format_decimal(
                                ing.ingredient_weight_kg_per_item
                            ),
                        }
                        for ing in ings
                    ]
                    if ings
                    else None
                ),
                "classification": (
                    {
                        "source": classification.source.value,
                        "confidence": format_decimal(classification.confidence),
                        "rule_id": classification.rule_id,
                        "ai_model": classification.ai_model,
                        "reviewer_user_id": (
                            str(classification.reviewer_user_id)
                            if classification.reviewer_user_id
                            else None
                        ),
                    }
                    if classification
                    else None
                ),
            }
        )

    document = {
        "run": {
            "id": str(ctx.run.run_id),
            "methodology": s.methodology.value,
            "methodology_version": s.methodology_version,
            "methodology_source_edition": s.methodology_source_edition,
            "taxonomy_version": s.taxonomy_version,
            "rules_version": s.rules_version,
            "reporting_period_label": s.reporting_period_label,
            "started_at": ctx.run.started_at.isoformat(),
            "finished_at": ctx.run.finished_at.isoformat() if ctx.run.finished_at else None,
            "triggered_by": str(ctx.run.triggered_by) if ctx.run.triggered_by else None,
        },
        "summary": {
            "total_in_scope_weight_kg": format_decimal(s.total_sales_weight_in_scope_kg),
            "total_composite_weight_kg": format_decimal(s.composites_total_weight_kg),
            "composite_share_of_sales_pct": composite_share,
            "out_of_scope_rows": s.out_of_scope_count,
            "unknown_rows": s.unknown_count,
        },
        "breakdowns": {
            "by_food_group": by_food_group,
            "composites_step1": {
                "meat_based": format_decimal(s.composites_meat_based_kg),
                "seafood_based": format_decimal(s.composites_seafood_based_kg),
                "vegetarian": format_decimal(s.composites_vegetarian_kg),
                "vegan": format_decimal(s.composites_vegan_kg),
            },
            "whole_diet_plant_vs_animal_context": {
                "plant_weight_kg": format_decimal(s.whole_diet_plant_weight_kg),
                "animal_weight_kg": format_decimal(s.whole_diet_animal_weight_kg),
            },
        },
        "rows": rows_json,
    }
    return json.dumps(document, indent=2)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
_FOOD_GROUP_LABELS = {
    WWFFoodGroup.FG1: "FG1 — Protein sources",
    WWFFoodGroup.FG2: "FG2 — Dairy and alternatives",
    WWFFoodGroup.FG3: "FG3 — Fats and oils",
    WWFFoodGroup.FG4: "FG4 — Fruit and vegetables",
    WWFFoodGroup.FG5: "FG5 — Grains and cereals",
    WWFFoodGroup.FG6: "FG6 — Tubers / starchy",
    WWFFoodGroup.FG7: "FG7 — Snacks",
}


def render_wwf_markdown(ctx: WWFExportContext) -> str:
    s = ctx.summary
    parts: list[str] = []
    parts.append("# WWF Planet-Based Diets report")
    parts.append("")
    parts.append(f"**Reporting period:** {s.reporting_period_label}")
    parts.append(f"**Methodology version:** {s.methodology_version}")
    parts.append(f"**Source edition:** {s.methodology_source_edition}")
    parts.append(f"**Taxonomy version:** {s.taxonomy_version}")
    parts.append(f"**Rules version:** {s.rules_version}")
    parts.append("")

    parts.append("## Per food group")
    parts.append("")
    parts.append("| Food group | Weight (kg) | Share % | PHD reference % |")
    parts.append("|---|---:|---:|---:|")
    for a in s.per_food_group:
        phd = format_decimal(a.phd_reference_share_pct) if a.phd_reference_share_pct else "—"
        parts.append(
            f"| {_FOOD_GROUP_LABELS[a.food_group]} | "
            f"{format_decimal(a.weight_kg)} | {format_decimal(a.share_pct)} | {phd} |"
        )
    parts.append("")

    if s.composites_total_weight_kg > 0:
        parts.append("## Composite products (Step 1)")
        parts.append("")
        parts.append("| Bucket | Weight (kg) |")
        parts.append("|---|---:|")
        parts.append(f"| Meat-based | {format_decimal(s.composites_meat_based_kg)} |")
        parts.append(f"| Seafood-based | {format_decimal(s.composites_seafood_based_kg)} |")
        parts.append(f"| Vegetarian | {format_decimal(s.composites_vegetarian_kg)} |")
        parts.append(f"| Vegan | {format_decimal(s.composites_vegan_kg)} |")
        parts.append(f"| **Total** | **{format_decimal(s.composites_total_weight_kg)}** |")
        parts.append("")

    parts.append("## Whole-diet plant vs animal (context only)")
    parts.append("")
    parts.append(
        "_The methodology calls this a context line, not a headline — "
        "see docs/methodologies/wwf.md._"
    )
    parts.append("")
    parts.append(
        f"- Plant-attributed weight: {format_decimal(s.whole_diet_plant_weight_kg)} kg"
    )
    parts.append(
        f"- Animal-attributed weight (with FG2 dairy equivalents): "
        f"{format_decimal(s.whole_diet_animal_weight_kg)} kg"
    )
    parts.append("")

    parts.append("## Data quality")
    parts.append("")
    parts.append(f"- Total in-scope sales weight: {format_decimal(s.total_sales_weight_in_scope_kg)} kg")
    parts.append(f"- Out-of-scope rows: {s.out_of_scope_count}")
    parts.append(f"- Unknown rows: {s.unknown_count}")
    parts.append("")

    parts.append("---")
    parts.append(
        "_Methodology: WWF Planet-Based Diets Retailer Methodology (2024). "
        "Dairy is reported in equivalents (cheese ×10, other ×1); composite "
        "products are reported at Step 1, and at Step 2 when own-brand "
        "ingredient data is available._"
    )
    return "\n".join(parts) + "\n"
