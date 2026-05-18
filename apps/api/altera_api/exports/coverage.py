"""Data coverage and uncertainty engine (Phase 22).

Pure functions — no I/O.  The ``build_coverage_section`` assembler reads
pre-computed run rows, product records, classification lookups, and review
queue state to produce a ``CoverageSection``.

Uncertainty labels are deterministic: no LLM is involved.

Thresholds (documented here for transparency):
  HIGH   — any of: blocking upload errors > 0;
                   unknown product share >= 10%;
                   pending review items >= 5% of total products.
  MEDIUM — any of: AI-classified share >= 30%;
                   missing label protein % share >= 10% (PT);
                   missing weight share >= 10%;
                   any pending review items not meeting HIGH threshold.
  LOW    — none of the above.
"""

from __future__ import annotations

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from uuid import UUID

# StoreProtocol and RunRecord are under TYPE_CHECKING to avoid the circular
# import: exports/__init__ → coverage → persistence/__init__ →
# persistence.memory → api.state → api/__init__ → api.routes →
# api.orchestrator → exports/__init__ (already loading).
# At runtime, both are duck-typed; no isinstance checks are performed here.
if TYPE_CHECKING:
    from altera_api.api.state import RunRecord
    from altera_api.persistence.protocol import StoreProtocol

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.enrichment import NutritionEnrichmentSource, NutritionEnrichmentStatus
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary
from altera_api.domain.report import CoverageSection
from altera_api.domain.wwf import WWFCalculationSummary
from altera_api.exports.common import format_decimal

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")
_TWO_DP = Decimal("0.01")

# Uncertainty thresholds
_HIGH_UNKNOWN_PCT = Decimal("10")
_HIGH_PENDING_PCT = Decimal("5")
_MED_AI_PCT = Decimal("30")
_MED_MISSING_PROTEIN_PCT = Decimal("10")
_MED_MISSING_WEIGHT_PCT = Decimal("10")


def _pct(numerator: int, denominator: int) -> str | None:
    """Return a formatted percentage string, or None if denominator is zero."""
    if denominator == 0:
        return None
    result = (Decimal(numerator) / Decimal(denominator) * _HUNDRED).quantize(
        _TWO_DP, rounding=ROUND_HALF_UP
    )
    return format_decimal(result)


def _dec_pct(numerator: int, denominator: int) -> Decimal | None:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator) * _HUNDRED).quantize(
        _TWO_DP, rounding=ROUND_HALF_UP
    )


def _compute_uncertainty(
    *,
    unknown_pct: Decimal | None,
    pending_count: int,
    products_total: int,
    ai_pct: Decimal | None,
    missing_protein_pct: Decimal | None,
    missing_weight_pct: Decimal | None,
    error_count: int | None,
) -> tuple[str, str]:
    """Return ``(level, rationale)`` — both are deterministic strings."""
    high_parts: list[str] = []

    if error_count is not None and error_count > 0:
        high_parts.append(f"{error_count} blocking upload error(s) detected")

    if unknown_pct is not None and unknown_pct >= _HIGH_UNKNOWN_PCT:
        high_parts.append(
            f"{format_decimal(unknown_pct)}% of products could not be classified"
        )

    pending_pct = _dec_pct(pending_count, products_total) or _ZERO
    if pending_count > 0 and pending_pct >= _HIGH_PENDING_PCT:
        high_parts.append(
            f"{pending_count} product(s) pending Altera review "
            f"({format_decimal(pending_pct)}% of total)"
        )

    if high_parts:
        return "high", "; ".join(high_parts) + "."

    med_parts: list[str] = []

    if ai_pct is not None and ai_pct >= _MED_AI_PCT:
        med_parts.append(
            f"{format_decimal(ai_pct)}% of products classified by AI (not rule-matched)"
        )

    if missing_protein_pct is not None and missing_protein_pct >= _MED_MISSING_PROTEIN_PCT:
        med_parts.append(
            f"{format_decimal(missing_protein_pct)}% of products missing label protein %"
        )

    if missing_weight_pct is not None and missing_weight_pct >= _MED_MISSING_WEIGHT_PCT:
        med_parts.append(
            f"{format_decimal(missing_weight_pct)}% of products have zero recorded weight"
        )

    if pending_count > 0:
        med_parts.append(f"{pending_count} product(s) still pending Altera review")

    if med_parts:
        return "medium", "; ".join(med_parts) + "."

    return "low", (
        "Most products were classified deterministically with complete data. "
        "No significant data quality concerns detected."
    )


def _review_completion_note(
    sent_to_review: int,
    reviewed: int,
    pending: int,
) -> str:
    if sent_to_review == 0:
        return "No products required manual review."
    if pending == 0:
        return (
            f"All {reviewed} manual review item(s) were resolved "
            "by the Altera methodology team."
        )
    return (
        f"{reviewed} of {sent_to_review} manual review item(s) resolved; "
        f"{pending} still pending."
    )


def _pt_caveats(
    s: ProteinTrackerCalculationSummary,
    products_with_missing_protein: int,
) -> list[str]:
    caveats: list[str] = []

    composite_count = next(
        (a.item_count for a in s.per_group if a.pt_group.value == "composite_products"),
        0,
    )
    if composite_count > 0:
        if s.rows_with_per_product_split > 0:
            remainder = composite_count - s.rows_with_per_product_split
            caveats.append(
                f"Per-product ingredient split applied to "
                f"{s.rows_with_per_product_split} composite row(s); "
                f"50/50 default split applied to the remaining {remainder} composite row(s)."
            )
        else:
            caveats.append(
                f"50/50 default protein split applied to all {composite_count} "
                "composite product row(s)."
            )

    if products_with_missing_protein > 0:
        caveats.append(
            f"{products_with_missing_protein} product(s) had no label-level protein %; "
            "reference database values were substituted where available."
        )

    return caveats


def _wwf_caveats(s: WWFCalculationSummary) -> list[str]:
    caveats: list[str] = [
        "WWF Planet-Based Diets measures product weight, not protein content. "
        "Results should not be compared directly with protein-based metrics."
    ]

    fg2 = next(
        (a for a in s.per_food_group if a.food_group.value == "FG2"), None
    )
    if fg2 is not None and fg2.weight_kg > _ZERO:
        caveats.append(
            "FG2 (dairy) products are expressed in dairy equivalents "
            "(cheese ×10, other dairy products ×1). "
            "Raw weight and dairy-equivalent weight diverge for cheese-heavy assortments."
        )

    if s.composites_total_weight_kg > _ZERO:
        caveats.append(
            f"Composite products ({format_decimal(s.composites_total_weight_kg)} kg total) "
            "were classified using Step 1 ingredient buckets. "
            "Step 2 own-brand ingredient attribution was not applied."
        )

    return caveats


def _enrichment_caveats(store: StoreProtocol, project_id: UUID) -> list[str]:
    """Disclose enrichment usage and gaps for PT runs (Phase 23A/23B).

    Returns caveats only when enrichment records exist for the project.
    Products with retailer-provided protein data produce no caveats.
    Phase 23B adds per-source breakdown for manual and category-average.
    """
    try:
        enrichment_records = store.list_enrichment_records_for_project(project_id)
    except AttributeError:
        # Store does not yet implement enrichment methods — safe no-op.
        return []

    protein_records = [r for r in enrichment_records if r.nutrient == "protein_pct"]

    needed = sum(1 for r in protein_records if r.status is NutritionEnrichmentStatus.NEEDED)
    manual_enriched = sum(
        1
        for r in protein_records
        if r.status is NutritionEnrichmentStatus.ENRICHED
        and r.source is NutritionEnrichmentSource.MANUAL_ALTERA
    )
    category_enriched = sum(
        1
        for r in protein_records
        if r.status is NutritionEnrichmentStatus.ENRICHED
        and r.source is NutritionEnrichmentSource.CATEGORY_AVERAGE
    )
    other_enriched = sum(
        1
        for r in protein_records
        if r.status is NutritionEnrichmentStatus.ENRICHED
        and r.source not in (
            NutritionEnrichmentSource.MANUAL_ALTERA,
            NutritionEnrichmentSource.CATEGORY_AVERAGE,
        )
    )

    caveats: list[str] = []
    if needed > 0:
        caveats.append(
            f"{needed} product(s) are missing label protein %; "
            "enrichment from an external or manual source is recommended."
        )
    if manual_enriched > 0:
        caveats.append(
            f"{manual_enriched} product(s) used manually-entered protein % values "
            "(Altera methodology team override). "
            "Enriched values are stored separately from retailer labels and are "
            "not yet applied to the calculation."
        )
    if category_enriched > 0:
        caveats.append(
            f"{category_enriched} product(s) used category-average protein % values "
            "as a statistical fallback (confidence ≤ 0.60). "
            "Enriched values are stored separately from retailer labels and are "
            "not yet applied to the calculation."
        )
    if other_enriched > 0:
        caveats.append(
            f"{other_enriched} product(s) used enriched protein % values "
            "(not from retailer labels). Enrichment source recorded per product. "
            "Enriched values are stored separately and are not yet applied to the calculation."
        )
    return caveats


def build_coverage_section(
    store: StoreProtocol,
    run: RunRecord,
    project: Project,
) -> CoverageSection:
    """Assemble a ``CoverageSection`` from live store state.

    All inputs are read-only.  No arithmetic beyond counting and
    percentage computation is performed.
    """
    is_pt = run.methodology is Methodology.PROTEIN_TRACKER

    # ------------------------------------------------------------------ #
    # Upload / validation tier
    # ------------------------------------------------------------------ #
    upload_records = store.list_uploads_for_project(project.id)
    total_uploaded: int | None = None
    total_valid: int | None = None
    total_errors: int | None = None
    total_warnings: int | None = None

    for urec in upload_records:
        vr = urec.validation_report
        if vr is None:
            continue
        if total_uploaded is None:
            total_uploaded = total_valid = total_errors = total_warnings = 0
        total_uploaded += vr.total_rows
        total_valid += vr.total_rows - vr.rows_with_errors
        total_errors += vr.error_count
        total_warnings += vr.warning_count

    invalid_rows = (
        (total_uploaded - total_valid)
        if total_uploaded is not None and total_valid is not None
        else None
    )

    # ------------------------------------------------------------------ #
    # Product tier (from run rows_payload + product records)
    # ------------------------------------------------------------------ #
    group_key = "pt_group" if is_pt else "wwf_food_group"

    products_total = len(run.rows_payload)
    products_unknown = sum(
        1 for row in run.rows_payload if row.get(group_key) == "unknown"
    )
    products_out_of_scope = sum(
        1 for row in run.rows_payload if row.get(group_key) == "out_of_scope"
    )
    products_classified = products_total - products_unknown - products_out_of_scope

    # ------------------------------------------------------------------ #
    # Classification sources
    # ------------------------------------------------------------------ #
    source_counts: Counter[str] = Counter()
    for row in run.rows_payload:
        raw_pid = row.get("product_id")
        if raw_pid is None:
            continue
        pid = UUID(raw_pid) if isinstance(raw_pid, str) else raw_pid
        clf = (
            store.get_pt_classification(pid)
            if is_pt
            else store.get_wwf_classification(pid)
        )
        source_counts[clf.source.value if clf else "unclassified"] += 1

    products_rule_classified = source_counts.get(ClassificationSource.DETERMINISTIC.value, 0)
    products_ai_classified = source_counts.get(ClassificationSource.AI.value, 0)
    products_manual_classified = source_counts.get(ClassificationSource.MANUAL_REVIEW.value, 0)

    # ------------------------------------------------------------------ #
    # Review queue
    # ------------------------------------------------------------------ #
    review_items = store.list_review_items_for_project(project.id, methodology=run.methodology)
    products_sent_to_review = len(review_items)
    terminal_items = [i for i in review_items if i.status.is_terminal]
    pending_items = [i for i in review_items if not i.status.is_terminal]
    products_reviewed_by_altera = len(terminal_items)

    # ------------------------------------------------------------------ #
    # Missing data (from NormalizedProduct records)
    # ------------------------------------------------------------------ #
    product_ids_in_run: set[UUID] = set()
    for row in run.rows_payload:
        raw_pid = row.get("product_id")
        if raw_pid:
            product_ids_in_run.add(UUID(raw_pid) if isinstance(raw_pid, str) else raw_pid)

    products_in_run = [
        p
        for p in store.list_products_for_project(project.id)
        if p.id in product_ids_in_run
    ]

    products_with_missing_weight = sum(
        1 for p in products_in_run if p.weight_per_item_kg == _ZERO
    )
    products_with_missing_category = sum(
        1 for p in products_in_run if p.retailer_category is None
    )
    products_with_missing_ingredients: int | None = sum(
        1 for p in products_in_run if p.ingredients_text is None
    )
    products_with_missing_protein: int | None = None
    if is_pt:
        products_with_missing_protein = sum(
            1 for p in products_in_run if p.pt_fields is None or p.pt_fields.protein_pct is None
        )

    # ------------------------------------------------------------------ #
    # Percentages
    # ------------------------------------------------------------------ #
    valid_row_share_pct = _pct(total_valid or 0, total_uploaded or 0) if total_uploaded else None
    classified_product_share_pct = _pct(products_classified, products_total)
    ai_classified_share_pct = _pct(products_ai_classified, products_total)
    manual_review_share_pct = _pct(products_sent_to_review, products_total)
    unknown_product_share_pct = _pct(products_unknown, products_total)
    missing_weight_share_pct = _pct(products_with_missing_weight, products_total)
    missing_protein_share_pct = (
        _pct(products_with_missing_protein, products_total)
        if products_with_missing_protein is not None
        else None
    )

    # ------------------------------------------------------------------ #
    # Uncertainty
    # ------------------------------------------------------------------ #
    unk_pct = _dec_pct(products_unknown, products_total)
    ai_pct_dec = _dec_pct(products_ai_classified, products_total)
    mp_pct_dec = (
        _dec_pct(products_with_missing_protein, products_total)
        if products_with_missing_protein is not None
        else None
    )
    mw_pct_dec = _dec_pct(products_with_missing_weight, products_total)

    uncertainty_level, uncertainty_rationale = _compute_uncertainty(
        unknown_pct=unk_pct,
        pending_count=len(pending_items),
        products_total=products_total,
        ai_pct=ai_pct_dec,
        missing_protein_pct=mp_pct_dec,
        missing_weight_pct=mw_pct_dec,
        error_count=total_errors,
    )

    # ------------------------------------------------------------------ #
    # Caveats (methodology-specific + enrichment disclosure)
    # ------------------------------------------------------------------ #
    if is_pt:
        s_pt = ProteinTrackerCalculationSummary.model_validate(run.summary_payload)
        caveats = _pt_caveats(s_pt, products_with_missing_protein or 0)
        caveats = caveats + _enrichment_caveats(store, project.id)
    else:
        s_wwf = WWFCalculationSummary.model_validate(run.summary_payload)
        caveats = _wwf_caveats(s_wwf)

    review_note = _review_completion_note(
        sent_to_review=products_sent_to_review,
        reviewed=products_reviewed_by_altera,
        pending=len(pending_items),
    )

    return CoverageSection(
        uploaded_rows=total_uploaded,
        valid_rows=total_valid,
        invalid_rows=invalid_rows,
        warning_count=total_warnings,
        error_count=total_errors,
        products_total=products_total,
        products_classified=products_classified,
        products_unknown=products_unknown,
        products_out_of_scope=products_out_of_scope,
        products_sent_to_review=products_sent_to_review,
        products_reviewed_by_altera=products_reviewed_by_altera,
        products_ai_classified=products_ai_classified,
        products_rule_classified=products_rule_classified,
        products_manual_classified=products_manual_classified,
        products_with_missing_weight=products_with_missing_weight,
        products_with_missing_protein=products_with_missing_protein,
        products_with_missing_category=products_with_missing_category,
        products_with_missing_ingredients=products_with_missing_ingredients,
        valid_row_share_pct=valid_row_share_pct,
        classified_product_share_pct=classified_product_share_pct,
        ai_classified_share_pct=ai_classified_share_pct,
        manual_review_share_pct=manual_review_share_pct,
        unknown_product_share_pct=unknown_product_share_pct,
        missing_weight_share_pct=missing_weight_share_pct,
        missing_protein_share_pct=missing_protein_share_pct,
        uncertainty_level=uncertainty_level,
        uncertainty_rationale=uncertainty_rationale,
        caveats=caveats,
        review_completion_note=review_note,
    )
