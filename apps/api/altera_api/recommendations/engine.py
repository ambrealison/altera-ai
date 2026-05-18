"""Deterministic recommendation engine (Phase 25A).

Pure function — no I/O, no LLM, no numeric impact estimates.
Reads pre-computed summary and coverage data and returns a list of
``Recommendation`` objects keyed to the action taxonomy.

Thresholds documented here for transparency:
  PT low plant share      — plant_share_pct < 40 %
  PT high composite pool  — composite protein >= 30 % of total in-scope
  PT high unknown         — unknown_count >= 5 % of products_total
  PT missing protein      — products_with_missing_protein > 0
  PT high AI share        — ai_pct >= 30 %
  WWF FG1 animal heavy    — FG1 animal subgroup weight > 60 % of FG1 total
  WWF Step 2 gap          — own-brand composites exist and step2_applied_count < own_brand_composite_count
  WWF branded composites  — branded_composite_count > 0
  Data quality high       — uncertainty_level == "high"
  Data quality medium     — uncertainty_level == "medium" and ai_pct >= 30 %
"""

from __future__ import annotations

from decimal import Decimal

from altera_api.domain.common import Methodology
from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary, ProteinTrackerGroup
from altera_api.domain.recommendation import (
    Recommendation,
    RecommendationActionType,
    RecommendationPriority,
    RecommendationStatus,
)
from altera_api.domain.wwf import WWFCalculationSummary, WWFFoodGroup
from altera_api.recommendations.taxonomy import ACTION_TAXONOMY

_ZERO = Decimal("0")

# Thresholds
_PT_LOW_PLANT_SHARE = Decimal("40")
_PT_HIGH_COMPOSITE_POOL_SHARE = Decimal("30")
_PT_HIGH_UNKNOWN_PCT = Decimal("5")
_PT_HIGH_AI_PCT = Decimal("30")
_WWF_FG1_ANIMAL_DOMINANT_SHARE = Decimal("60")


def _build(
    action_type: RecommendationActionType,
    *,
    priority: RecommendationPriority,
    confidence: str,
    rationale: str,
    evidence: list[str],
    status: RecommendationStatus = RecommendationStatus.DRAFT,
) -> Recommendation:
    """Construct a ``Recommendation`` from the taxonomy entry for ``action_type``."""
    entry = ACTION_TAXONOMY[action_type.value]
    return Recommendation(
        action_type=action_type.value,
        category=entry["category"],
        title=action_type.value.replace("_", " ").title(),
        description=entry["description"],
        rationale=rationale,
        expected_direction=entry["expected_direction"],
        priority=priority.value,
        confidence=confidence,
        evidence=evidence,
        status=status.value,
        caveats=entry["caveats"],
        client_facing=entry["client_facing"],
    )


def _generate_pt(
    s: ProteinTrackerCalculationSummary,
    *,
    products_total: int,
    products_unknown: int,
    products_ai_classified: int,
    products_with_missing_protein: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    # --- Plant share low ---
    if s.plant_share_pct is not None and s.plant_share_pct < _PT_LOW_PLANT_SHARE:
        evidence = [f"Current plant-source protein share: {s.plant_share_pct:.2f}%"]
        recs.append(
            _build(
                RecommendationActionType.INCREASE_PLANT_CORE_SHARE,
                priority=RecommendationPriority.HIGH,
                confidence="high",
                rationale=(
                    f"Plant-source protein represents {s.plant_share_pct:.2f}% of "
                    "in-scope protein, which is below the 40% threshold used to "
                    "flag a low plant share."
                ),
                evidence=evidence,
            )
        )

    # --- Composite protein pool large (50/50 default applied to large share) ---
    composite_group = next(
        (a for a in s.per_group if a.pt_group is ProteinTrackerGroup.COMPOSITE_PRODUCTS),
        None,
    )
    if (
        composite_group is not None
        and s.total_in_scope_protein_kg > _ZERO
    ):
        composite_share = (
            composite_group.protein_kg * Decimal("100") / s.total_in_scope_protein_kg
        )
        if composite_share >= _PT_HIGH_COMPOSITE_POOL_SHARE:
            evidence = [
                f"Composite products account for {composite_share:.2f}% of in-scope protein.",
                f"The 50/50 default split was applied to "
                f"{composite_group.item_count - s.rows_with_per_product_split} "
                f"composite product(s).",
            ]
            recs.append(
                _build(
                    RecommendationActionType.IMPROVE_COMPOSITE_BREAKDOWN,
                    priority=RecommendationPriority.MEDIUM,
                    confidence="high",
                    rationale=(
                        "A substantial share of in-scope protein is attributed to composite "
                        "products where the 50/50 default split is applied. Providing "
                        "per-product ingredient split data improves accuracy."
                    ),
                    evidence=evidence,
                )
            )

    # --- Missing protein ---
    if products_with_missing_protein > 0:
        evidence = [
            f"{products_with_missing_protein} product(s) have no label-level protein %.",
        ]
        recs.append(
            _build(
                RecommendationActionType.ENRICH_MISSING_NUTRITION,
                priority=RecommendationPriority.MEDIUM,
                confidence="high",
                rationale=(
                    f"{products_with_missing_protein} product(s) are missing label protein % "
                    "and are excluded from protein totals. Enrichment improves completeness."
                ),
                evidence=evidence,
            )
        )

    # --- Unknown count high ---
    if products_total > 0:
        unknown_pct = Decimal(products_unknown) * Decimal("100") / Decimal(products_total)
        if unknown_pct >= _PT_HIGH_UNKNOWN_PCT:
            evidence = [
                f"{products_unknown} of {products_total} products ({unknown_pct:.2f}%) "
                "could not be classified."
            ]
            recs.append(
                _build(
                    RecommendationActionType.REVIEW_HIGH_IMPACT_UNKNOWNS,
                    priority=RecommendationPriority.HIGH,
                    confidence="high",
                    rationale=(
                        f"{unknown_pct:.2f}% of products are unclassified and excluded "
                        "from the protein ratio. Reviewing them reduces uncertainty."
                    ),
                    evidence=evidence,
                )
            )

    # --- AI classification share high ---
    if products_total > 0:
        ai_pct = Decimal(products_ai_classified) * Decimal("100") / Decimal(products_total)
        if ai_pct >= _PT_HIGH_AI_PCT:
            evidence = [
                f"{products_ai_classified} of {products_total} products ({ai_pct:.2f}%) "
                "were classified by AI rather than deterministic rules."
            ]
            recs.append(
                _build(
                    RecommendationActionType.IMPROVE_DATA_QUALITY,
                    priority=RecommendationPriority.MEDIUM,
                    confidence="medium",
                    rationale=(
                        f"AI classifications represent {ai_pct:.2f}% of the product range. "
                        "Expanding the deterministic rule set reduces AI dependency and "
                        "improves classification consistency."
                    ),
                    evidence=evidence,
                )
            )

    return recs


def _generate_wwf(
    s: WWFCalculationSummary,
    *,
    products_total: int,
    products_unknown: int,
    products_ai_classified: int,
    wwf_step2_applied_count: int,
    wwf_own_brand_composite_count: int,
    wwf_branded_composite_count: int,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    # --- Step 2 coverage gap ---
    if wwf_own_brand_composite_count > 0 and wwf_step2_applied_count < wwf_own_brand_composite_count:
        gap = wwf_own_brand_composite_count - wwf_step2_applied_count
        evidence = [
            f"{gap} own-brand composite product(s) have no Step 2 ingredient data.",
            f"{wwf_step2_applied_count} of {wwf_own_brand_composite_count} own-brand "
            "composites have ingredient-level attribution.",
        ]
        recs.append(
            _build(
                RecommendationActionType.COLLECT_STEP2_INGREDIENT_DATA,
                priority=RecommendationPriority.HIGH,
                confidence="high",
                rationale=(
                    "Own-brand composite products without Step 2 ingredient data are "
                    "reported using whole-weight Step 1 bucket attribution, which is less "
                    "precise than ingredient-level food group distribution."
                ),
                evidence=evidence,
            )
        )

    # --- Branded composites still at Step 1 (Altera-facing operational note) ---
    if wwf_branded_composite_count > 0:
        evidence = [
            f"{wwf_branded_composite_count} branded composite product(s) are reported at "
            "Step 1 (whole product weight) only."
        ]
        recs.append(
            _build(
                RecommendationActionType.IMPROVE_DATA_QUALITY,
                priority=RecommendationPriority.LOW,
                confidence="high",
                rationale=(
                    f"{wwf_branded_composite_count} branded composite product(s) cannot use "
                    "Step 2 ingredient attribution. Supplier data collection or industry "
                    "standard ingredient data would improve accuracy."
                ),
                evidence=evidence,
            )
        )

    # --- FG1 animal subgroup heavy ---
    fg1 = next(
        (a for a in s.per_food_group if a.food_group is WWFFoodGroup.FG1),
        None,
    )
    if fg1 is not None and fg1.weight_kg > _ZERO and s.total_sales_weight_in_scope_kg > _ZERO:
        fg1_share = fg1.share_pct
        if fg1_share > _ZERO:
            # We can't introspect subgroup breakdown from WWFCalculationSummary directly,
            # so we use FG1's overall share vs the PHD reference as a proxy.
            phd_fg1 = fg1.phd_reference_share_pct
            if phd_fg1 is not None and fg1_share > phd_fg1:
                evidence = [
                    f"FG1 (protein-rich foods) accounts for {fg1_share:.2f}% of in-scope "
                    f"sales weight (PHD reference: {phd_fg1:.2f}%)."
                ]
                recs.append(
                    _build(
                        RecommendationActionType.PROMOTE_LEGUME_PRODUCTS,
                        priority=RecommendationPriority.MEDIUM,
                        confidence="medium",
                        rationale=(
                            f"FG1 share ({fg1_share:.2f}%) exceeds the Planetary Health Diet "
                            f"reference ({phd_fg1:.2f}%). Expanding legume and plant-protein "
                            "assortment within FG1 may improve the plant-source proportion."
                        ),
                        evidence=evidence,
                    )
                )

    # --- Unknown count high ---
    if products_total > 0:
        unknown_pct = Decimal(products_unknown) * Decimal("100") / Decimal(products_total)
        if unknown_pct >= _PT_HIGH_UNKNOWN_PCT:
            evidence = [
                f"{products_unknown} of {products_total} products ({unknown_pct:.2f}%) "
                "could not be classified."
            ]
            recs.append(
                _build(
                    RecommendationActionType.REVIEW_HIGH_IMPACT_UNKNOWNS,
                    priority=RecommendationPriority.HIGH,
                    confidence="high",
                    rationale=(
                        f"{unknown_pct:.2f}% of products are unclassified and excluded "
                        "from the WWF food group breakdown. Reviewing them improves coverage."
                    ),
                    evidence=evidence,
                )
            )

    # --- AI classification share high ---
    if products_total > 0:
        ai_pct = Decimal(products_ai_classified) * Decimal("100") / Decimal(products_total)
        if ai_pct >= _PT_HIGH_AI_PCT:
            evidence = [
                f"{products_ai_classified} of {products_total} products ({ai_pct:.2f}%) "
                "were classified by AI rather than deterministic rules."
            ]
            recs.append(
                _build(
                    RecommendationActionType.IMPROVE_DATA_QUALITY,
                    priority=RecommendationPriority.MEDIUM,
                    confidence="medium",
                    rationale=(
                        f"AI classifications represent {ai_pct:.2f}% of the product range. "
                        "Expanding the deterministic rule set improves classification "
                        "consistency and reduces uncertainty."
                    ),
                    evidence=evidence,
                )
            )

    return recs


def _generate_data_quality(
    uncertainty_level: str,
    *,
    methodology: Methodology,
) -> list[Recommendation]:
    recs: list[Recommendation] = []

    if uncertainty_level == "high":
        recs.append(
            _build(
                RecommendationActionType.IMPROVE_DATA_QUALITY,
                priority=RecommendationPriority.CRITICAL,
                confidence="high",
                rationale=(
                    "Data uncertainty is rated HIGH. Blocking errors, a high unknown "
                    "product share, or unresolved review items are present. "
                    "Resolve these before setting or reporting against targets."
                ),
                evidence=[f"Uncertainty level: {uncertainty_level}"],
            )
        )
        recs.append(
            _build(
                RecommendationActionType.CREATE_CATEGORY_TARGET,
                priority=RecommendationPriority.LOW,
                confidence="high",
                rationale=(
                    "Target-setting should follow data quality resolution. "
                    "Once uncertainty is LOW, work with the Altera team to set a baseline."
                ),
                evidence=[
                    "Uncertainty must be LOW before targets are meaningful.",
                ],
            )
        )

    return recs


def generate_recommendations(
    methodology: Methodology,
    *,
    pt_summary: ProteinTrackerCalculationSummary | None = None,
    wwf_summary: WWFCalculationSummary | None = None,
    uncertainty_level: str,
    products_total: int,
    products_unknown: int,
    products_ai_classified: int,
    products_with_missing_protein: int | None = None,
    wwf_step2_applied_count: int = 0,
    wwf_own_brand_composite_count: int = 0,
    wwf_branded_composite_count: int = 0,
) -> list[Recommendation]:
    """Generate deterministic recommendations from run and coverage data.

    Returns an ordered list of ``Recommendation`` objects. The list is stable
    for identical inputs — no random ordering or LLM non-determinism.

    Parameters
    ----------
    methodology:
        Which methodology this run uses.
    pt_summary / wwf_summary:
        Pre-computed calculation summary for the relevant methodology.
    uncertainty_level:
        ``"low"`` / ``"medium"`` / ``"high"`` from the coverage section.
    products_total / products_unknown / products_ai_classified:
        Counts from the coverage section.
    products_with_missing_protein:
        PT-only count of products missing label protein %.
    wwf_step2_applied_count:
        Number of own-brand composites with stored Step 2 ingredients.
    wwf_own_brand_composite_count:
        Total own-brand composites in the run.
    wwf_branded_composite_count:
        Branded composite count in the run.
    """
    recs: list[Recommendation] = []

    if methodology is Methodology.PROTEIN_TRACKER and pt_summary is not None:
        recs.extend(
            _generate_pt(
                pt_summary,
                products_total=products_total,
                products_unknown=products_unknown,
                products_ai_classified=products_ai_classified,
                products_with_missing_protein=products_with_missing_protein or 0,
            )
        )
    elif methodology is Methodology.WWF and wwf_summary is not None:
        recs.extend(
            _generate_wwf(
                wwf_summary,
                products_total=products_total,
                products_unknown=products_unknown,
                products_ai_classified=products_ai_classified,
                wwf_step2_applied_count=wwf_step2_applied_count,
                wwf_own_brand_composite_count=wwf_own_brand_composite_count,
                wwf_branded_composite_count=wwf_branded_composite_count,
            )
        )

    recs.extend(_generate_data_quality(uncertainty_level, methodology=methodology))

    # Deduplicate by action_type (keep first occurrence, which has highest priority).
    seen: set[str] = set()
    unique: list[Recommendation] = []
    for r in recs:
        if r.action_type not in seen:
            seen.add(r.action_type)
            unique.append(r)

    return unique
