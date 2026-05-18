"""Protein Tracker exporters (CSV / JSON / Markdown)."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from altera_api.domain.product import ProteinSource
from altera_api.domain.project import PTValidationStatus
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationRow,
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
)
from altera_api.exports.common import (
    CSV_ENCODING,
    ExportClassificationMeta,
    ExportProductMaster,
    RunMetadata,
    format_decimal,
)


@dataclass(frozen=True)
class PTExportContext:
    run: RunMetadata
    summary: ProteinTrackerCalculationSummary
    rows: tuple[ProteinTrackerCalculationRow, ...]
    products: Mapping[UUID, ExportProductMaster]
    classifications: Mapping[UUID, ExportClassificationMeta]
    #: PT validation lifecycle (none / draft / submitted / validated).
    pt_validation_status: PTValidationStatus = PTValidationStatus.NONE
    #: Per-row ``protein_source`` and ``items_purchased`` for the CSV.
    #: Looked up by product_id; missing entries render blank.
    protein_sources: Mapping[UUID, ProteinSource] = None  # type: ignore[assignment]
    items_purchased: Mapping[UUID, object] = None  # type: ignore[assignment]
    weights_per_item: Mapping[UUID, object] = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
_PT_CSV_COLUMNS: tuple[str, ...] = (
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
    "pt_group",
    "weight_per_item_kg",
    "items_purchased",
    "volume_kg",
    "protein_pct",
    "protein_source",
    "protein_kg",
    "used_per_product_split",
    "plant_protein_kg",
    "animal_protein_kg",
    "classification_source",
    "classification_confidence",
    "classification_rule_id",
    "classification_ai_model",
    "classification_reviewer_user_id",
)


def render_pt_csv(ctx: PTExportContext) -> bytes:
    """Render a PT CSV export. UTF-8 with BOM, RFC 4180 quoting."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_PT_CSV_COLUMNS)

    s = ctx.summary
    protein_sources = ctx.protein_sources or {}
    items_purchased = ctx.items_purchased or {}
    weights = ctx.weights_per_item or {}

    for row in ctx.rows:
        product = ctx.products.get(row.product_id)
        classification = ctx.classifications.get(row.product_id)
        protein_source = protein_sources.get(row.product_id)
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
                (product.brand if product and product.brand else "") if product else "",
                row.pt_group.value,
                format_decimal(weights.get(row.product_id)),  # type: ignore[arg-type]
                format_decimal(items_purchased.get(row.product_id)),  # type: ignore[arg-type]
                format_decimal(row.volume_kg),
                format_decimal(row.protein_pct),
                protein_source.value if protein_source else "",
                format_decimal(row.protein_kg),
                "true" if row.used_per_product_split else "false",
                format_decimal(row.plant_protein_kg),
                format_decimal(row.animal_protein_kg),
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
def render_pt_json(ctx: PTExportContext) -> str:
    s = ctx.summary
    by_group = {
        a.pt_group.value: {
            "item_count": a.item_count,
            "volume_kg": format_decimal(a.volume_kg),
            "protein_kg": format_decimal(a.protein_kg),
        }
        for a in s.per_group
    }

    rows_json = []
    protein_sources = ctx.protein_sources or {}
    items_purchased = ctx.items_purchased or {}
    weights = ctx.weights_per_item or {}
    for row in ctx.rows:
        product = ctx.products.get(row.product_id)
        classification = ctx.classifications.get(row.product_id)
        rows_json.append(
            {
                "product_id": str(row.product_id),
                "external_product_id": product.external_product_id if product else None,
                "product_name": product.product_name if product else None,
                "brand": product.brand if product else None,
                "pt_group": row.pt_group.value,
                "in_scope": row.in_scope,
                "weight_per_item_kg": format_decimal(weights.get(row.product_id)) or None,  # type: ignore[arg-type]
                "items_purchased": format_decimal(items_purchased.get(row.product_id)) or None,  # type: ignore[arg-type]
                "volume_kg": format_decimal(row.volume_kg),
                "protein_pct": format_decimal(row.protein_pct),
                "protein_source": (
                    protein_sources.get(row.product_id).value
                    if protein_sources.get(row.product_id)
                    else None
                ),
                "protein_kg": format_decimal(row.protein_kg),
                "used_per_product_split": row.used_per_product_split,
                "plant_protein_kg": format_decimal(row.plant_protein_kg) or None,
                "animal_protein_kg": format_decimal(row.animal_protein_kg) or None,
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
            "total_in_scope_protein_kg": format_decimal(s.total_in_scope_protein_kg),
            "plant_protein_kg": format_decimal(s.plant_protein_kg),
            "animal_protein_kg": format_decimal(s.animal_protein_kg),
            "plant_share_pct": format_decimal(s.plant_share_pct) or None,
            "animal_share_pct": format_decimal(s.animal_share_pct) or None,
            "by_group": by_group,
            "out_of_scope_rows": s.out_of_scope_count,
            "unknown_rows": s.unknown_count,
            "per_product_split_rows": s.rows_with_per_product_split,
            "protein_source_label_rows": s.rows_protein_source_label,
            "protein_source_reference_db_rows": s.rows_protein_source_reference_db,
            "pt_validation_status": ctx.pt_validation_status.value,
        },
        "rows": rows_json,
    }
    return json.dumps(document, indent=2)


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
_PT_GROUP_LABELS = {
    ProteinTrackerGroup.PLANT_BASED_CORE: "Plant-based, core",
    ProteinTrackerGroup.PLANT_BASED_NON_CORE: "Plant-based, non-core",
    ProteinTrackerGroup.COMPOSITE_PRODUCTS: "Composite products",
    ProteinTrackerGroup.ANIMAL_CORE: "Animal-based, core",
}


def render_pt_markdown(ctx: PTExportContext) -> str:
    s = ctx.summary
    parts: list[str] = []
    parts.append("# Protein Tracker report")
    parts.append("")
    parts.append(f"**Reporting period:** {s.reporting_period_label}")
    parts.append(f"**Methodology version:** {s.methodology_version}")
    parts.append(f"**Source edition:** {s.methodology_source_edition}")
    parts.append(f"**Taxonomy version:** {s.taxonomy_version}")
    parts.append(f"**Rules version:** {s.rules_version}")
    parts.append(f"**PT validation status:** `{ctx.pt_validation_status.value}`")
    parts.append("")

    parts.append("## Headline")
    parts.append("")
    if s.plant_share_pct is None or s.animal_share_pct is None:
        parts.append("_No in-scope protein found._")
    else:
        parts.append(
            f"- **Plant-source protein:** {format_decimal(s.plant_protein_kg)} kg "
            f"({format_decimal(s.plant_share_pct)} %)"
        )
        parts.append(
            f"- **Animal-source protein:** {format_decimal(s.animal_protein_kg)} kg "
            f"({format_decimal(s.animal_share_pct)} %)"
        )
        parts.append(
            f"- **Total in-scope protein:** {format_decimal(s.total_in_scope_protein_kg)} kg"
        )
    parts.append("")

    parts.append("## Four-group breakdown")
    parts.append("")
    parts.append("| Group | Items | Volume (kg) | Protein (kg) |")
    parts.append("|---|---:|---:|---:|")
    by_group = {a.pt_group: a for a in s.per_group}
    for group in (
        ProteinTrackerGroup.PLANT_BASED_CORE,
        ProteinTrackerGroup.PLANT_BASED_NON_CORE,
        ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        ProteinTrackerGroup.ANIMAL_CORE,
    ):
        a = by_group[group]
        parts.append(
            f"| {_PT_GROUP_LABELS[group]} | {a.item_count} | "
            f"{format_decimal(a.volume_kg)} | {format_decimal(a.protein_kg)} |"
        )
    parts.append("")

    parts.append("## Data quality")
    parts.append("")
    parts.append(f"- Rows with per-product composite split: {s.rows_with_per_product_split}")
    parts.append(f"- Protein source = label: {s.rows_protein_source_label}")
    parts.append(f"- Protein source = reference DB: {s.rows_protein_source_reference_db}")
    parts.append(f"- Out-of-scope rows: {s.out_of_scope_count}")
    parts.append(f"- Unknown rows: {s.unknown_count}")
    parts.append("")

    parts.append("---")
    parts.append(
        "_Methodology: The Protein Tracker — Foodservice, Green Protein Alliance "
        "& ProVeg. The 50/50 composite default is applied at the group level."
    )
    parts.append(
        "Generated by Altera AI. Numbers preserve full `Decimal` precision; "
        "round at the display layer if needed._"
    )
    return "\n".join(parts) + "\n"
