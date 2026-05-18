"""Pure run comparison engine (Phase 27A).

Guarantees:
- Run data is never mutated.
- Comparisons are deterministic: same inputs → same output.
- Version mismatches produce warnings, not errors.
- PT and WWF are never merged; each methodology is compared independently.
- No LLM, no randomness, no external calls.

Parameter contracts
-------------------
compare_pt_runs(base, comp)
    Both arguments must be ProteinTrackerCalculationSummary instances.
    Returns (PTComparisonSummary, list[str]).

compare_wwf_runs(base, comp)
    Both arguments must be WWFCalculationSummary instances.
    Returns (WWFComparisonSummary, list[str]).

build_run_comparison(base_run, comp_run)
    Both RunRecords must share the same methodology and project_id.
    Returns RunComparisonResult.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from altera_api.domain.comparison import (
    PTComparisonSummary,
    PTGroupComparison,
    RunComparisonResult,
    WWFComparisonSummary,
    WWFFoodGroupComparison,
)
from altera_api.domain.protein_tracker import ProteinTrackerCalculationSummary

if TYPE_CHECKING:
    from altera_api.api.state import RunRecord
    from altera_api.domain.wwf import WWFCalculationSummary

# Threshold below which a plant-share change is classified as "stable"
_PT_STABLE_THRESHOLD = Decimal("0.1")   # percentage points
_WWF_STABLE_THRESHOLD = Decimal("0.001")  # fraction of total weight


# ---------------------------------------------------------------------------
# Direction helpers
# ---------------------------------------------------------------------------

def _direction_pt(delta_plant_share: Decimal | None) -> str:
    if delta_plant_share is None:
        return "stable"
    if delta_plant_share > _PT_STABLE_THRESHOLD:
        return "improving"
    if delta_plant_share < -_PT_STABLE_THRESHOLD:
        return "declining"
    return "stable"


def _direction_wwf(
    base_plant: Decimal,
    comp_plant: Decimal,
    base_total: Decimal,
    comp_total: Decimal,
) -> str:
    """Direction based on whether the plant weight *fraction* increased."""
    if base_total == Decimal("0") or comp_total == Decimal("0"):
        return "stable"
    delta_fraction = comp_plant / comp_total - base_plant / base_total
    if delta_fraction > _WWF_STABLE_THRESHOLD:
        return "improving"
    if delta_fraction < -_WWF_STABLE_THRESHOLD:
        return "declining"
    return "stable"


# ---------------------------------------------------------------------------
# Version mismatch warnings
# ---------------------------------------------------------------------------

def _version_warnings(
    base_period: str,
    comp_period: str,
    b_meth: str,
    c_meth: str,
    b_tax: str,
    c_tax: str,
    b_rules: str,
    c_rules: str,
) -> list[str]:
    warnings: list[str] = []
    if b_meth != c_meth:
        warnings.append(
            f"Methodology version differs: {base_period!r} uses {b_meth!r}, "
            f"{comp_period!r} uses {c_meth!r}. "
            "Results may not be directly comparable."
        )
    if b_tax != c_tax:
        warnings.append(
            f"Taxonomy version differs: {base_period!r} uses {b_tax!r}, "
            f"{comp_period!r} uses {c_tax!r}. "
            "Classification rules may have changed between periods."
        )
    if b_rules != c_rules:
        warnings.append(
            f"Rules version differs: {base_period!r} uses {b_rules!r}, "
            f"{comp_period!r} uses {c_rules!r}. "
            "Some products may have moved between groups."
        )
    return warnings


# ---------------------------------------------------------------------------
# PT comparison
# ---------------------------------------------------------------------------

def compare_pt_runs(
    base: ProteinTrackerCalculationSummary,
    comp: ProteinTrackerCalculationSummary,
) -> tuple[PTComparisonSummary, list[str]]:
    """Compare two PT summaries.  Returns (PTComparisonSummary, warnings)."""
    warnings = _version_warnings(
        base.reporting_period_label,
        comp.reporting_period_label,
        base.methodology_version,
        comp.methodology_version,
        base.taxonomy_version,
        comp.taxonomy_version,
        base.rules_version,
        comp.rules_version,
    )

    base_by_group: dict[str, Decimal] = {
        agg.pt_group.value: agg.protein_kg for agg in base.per_group
    }
    comp_by_group: dict[str, Decimal] = {
        agg.pt_group.value: agg.protein_kg for agg in comp.per_group
    }
    all_groups = sorted(set(base_by_group) | set(comp_by_group))

    per_group = [
        PTGroupComparison(
            pt_group=g,
            baseline_protein_kg=base_by_group.get(g, Decimal("0")),
            comparison_protein_kg=comp_by_group.get(g, Decimal("0")),
            delta_protein_kg=(
                comp_by_group.get(g, Decimal("0"))
                - base_by_group.get(g, Decimal("0"))
            ),
        )
        for g in all_groups
    ]

    delta_plant_share = (
        comp.plant_share_pct - base.plant_share_pct
        if comp.plant_share_pct is not None and base.plant_share_pct is not None
        else None
    )
    delta_animal_share = (
        comp.animal_share_pct - base.animal_share_pct
        if comp.animal_share_pct is not None and base.animal_share_pct is not None
        else None
    )

    summary = PTComparisonSummary(
        baseline_reporting_period=base.reporting_period_label,
        comparison_reporting_period=comp.reporting_period_label,
        baseline_methodology_version=base.methodology_version,
        comparison_methodology_version=comp.methodology_version,
        baseline_taxonomy_version=base.taxonomy_version,
        comparison_taxonomy_version=comp.taxonomy_version,
        baseline_rules_version=base.rules_version,
        comparison_rules_version=comp.rules_version,
        baseline_plant_protein_kg=base.plant_protein_kg,
        baseline_animal_protein_kg=base.animal_protein_kg,
        baseline_total_protein_kg=base.total_in_scope_protein_kg,
        baseline_plant_share_pct=base.plant_share_pct,
        baseline_animal_share_pct=base.animal_share_pct,
        comparison_plant_protein_kg=comp.plant_protein_kg,
        comparison_animal_protein_kg=comp.animal_protein_kg,
        comparison_total_protein_kg=comp.total_in_scope_protein_kg,
        comparison_plant_share_pct=comp.plant_share_pct,
        comparison_animal_share_pct=comp.animal_share_pct,
        delta_plant_protein_kg=comp.plant_protein_kg - base.plant_protein_kg,
        delta_animal_protein_kg=comp.animal_protein_kg - base.animal_protein_kg,
        delta_total_protein_kg=(
            comp.total_in_scope_protein_kg - base.total_in_scope_protein_kg
        ),
        delta_plant_share_pct=delta_plant_share,
        delta_animal_share_pct=delta_animal_share,
        direction=_direction_pt(delta_plant_share),
        per_group=per_group,
    )
    return summary, warnings


# ---------------------------------------------------------------------------
# WWF comparison
# ---------------------------------------------------------------------------

def compare_wwf_runs(
    base: WWFCalculationSummary,
    comp: WWFCalculationSummary,
) -> tuple[WWFComparisonSummary, list[str]]:
    """Compare two WWF summaries.  Returns (WWFComparisonSummary, warnings)."""
    from altera_api.domain.wwf import WWFCalculationSummary as _WWF  # noqa: F401

    warnings = _version_warnings(
        base.reporting_period_label,
        comp.reporting_period_label,
        base.methodology_version,
        comp.methodology_version,
        base.taxonomy_version,
        comp.taxonomy_version,
        base.rules_version,
        comp.rules_version,
    )

    base_by_fg: dict[str, object] = {
        agg.food_group.value: agg for agg in base.per_food_group
    }
    comp_by_fg: dict[str, object] = {
        agg.food_group.value: agg for agg in comp.per_food_group
    }
    all_fgs = sorted(set(base_by_fg) | set(comp_by_fg))

    per_food_group: list[WWFFoodGroupComparison] = []
    for fg in all_fgs:
        b_agg = base_by_fg.get(fg)
        c_agg = comp_by_fg.get(fg)
        b_weight = b_agg.weight_kg if b_agg else Decimal("0")  # type: ignore[union-attr]
        c_weight = c_agg.weight_kg if c_agg else Decimal("0")  # type: ignore[union-attr]
        b_share = b_agg.share_pct if b_agg else Decimal("0")  # type: ignore[union-attr]
        c_share = c_agg.share_pct if c_agg else Decimal("0")  # type: ignore[union-attr]
        # Prefer baseline PHD reference; fall back to comparison
        phd_ref = (
            b_agg.phd_reference_share_pct  # type: ignore[union-attr]
            if b_agg else None
        ) or (
            c_agg.phd_reference_share_pct  # type: ignore[union-attr]
            if c_agg else None
        )
        per_food_group.append(
            WWFFoodGroupComparison(
                food_group=fg,
                baseline_weight_kg=b_weight,
                comparison_weight_kg=c_weight,
                delta_weight_kg=c_weight - b_weight,
                baseline_share_pct=b_share,
                comparison_share_pct=c_share,
                delta_share_pct=c_share - b_share,
                phd_reference_share_pct=phd_ref,
            )
        )

    direction = _direction_wwf(
        base.whole_diet_plant_weight_kg,
        comp.whole_diet_plant_weight_kg,
        base.total_sales_weight_in_scope_kg,
        comp.total_sales_weight_in_scope_kg,
    )

    summary = WWFComparisonSummary(
        baseline_reporting_period=base.reporting_period_label,
        comparison_reporting_period=comp.reporting_period_label,
        baseline_methodology_version=base.methodology_version,
        comparison_methodology_version=comp.methodology_version,
        baseline_taxonomy_version=base.taxonomy_version,
        comparison_taxonomy_version=comp.taxonomy_version,
        baseline_rules_version=base.rules_version,
        comparison_rules_version=comp.rules_version,
        baseline_total_weight_kg=base.total_sales_weight_in_scope_kg,
        comparison_total_weight_kg=comp.total_sales_weight_in_scope_kg,
        delta_total_weight_kg=(
            comp.total_sales_weight_in_scope_kg - base.total_sales_weight_in_scope_kg
        ),
        baseline_plant_weight_kg=base.whole_diet_plant_weight_kg,
        comparison_plant_weight_kg=comp.whole_diet_plant_weight_kg,
        delta_plant_weight_kg=(
            comp.whole_diet_plant_weight_kg - base.whole_diet_plant_weight_kg
        ),
        baseline_animal_weight_kg=base.whole_diet_animal_weight_kg,
        comparison_animal_weight_kg=comp.whole_diet_animal_weight_kg,
        delta_animal_weight_kg=(
            comp.whole_diet_animal_weight_kg - base.whole_diet_animal_weight_kg
        ),
        direction=direction,
        per_food_group=per_food_group,
    )
    return summary, warnings


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------

def build_run_comparison(
    base_run: RunRecord,
    comp_run: RunRecord,
) -> RunComparisonResult:
    """Build a RunComparisonResult from two RunRecords.

    Caller guarantees:
    - Both runs share the same methodology.
    - Both runs belong to the same project.
    - base_run.id != comp_run.id.
    """
    methodology = base_run.methodology.value
    all_warnings: list[str] = []
    pt_comparison = None
    wwf_comparison = None

    if methodology == "protein_tracker":
        base_summary = ProteinTrackerCalculationSummary.model_validate(
            base_run.summary_payload
        )
        comp_summary = ProteinTrackerCalculationSummary.model_validate(
            comp_run.summary_payload
        )
        pt_comparison, warnings = compare_pt_runs(base_summary, comp_summary)
        all_warnings.extend(warnings)

    elif methodology == "wwf":
        from altera_api.domain.wwf import WWFCalculationSummary

        base_summary_wwf = WWFCalculationSummary.model_validate(
            base_run.summary_payload
        )
        comp_summary_wwf = WWFCalculationSummary.model_validate(
            comp_run.summary_payload
        )
        wwf_comparison, warnings = compare_wwf_runs(base_summary_wwf, comp_summary_wwf)
        all_warnings.extend(warnings)

    return RunComparisonResult(
        baseline_run_id=base_run.id,
        comparison_run_id=comp_run.id,
        project_id=base_run.project_id,
        methodology=methodology,
        pt_comparison=pt_comparison,
        wwf_comparison=wwf_comparison,
        warnings=all_warnings,
        created_at=datetime.now(UTC),
    )
