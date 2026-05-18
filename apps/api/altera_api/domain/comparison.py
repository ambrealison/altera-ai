"""Run comparison domain models (Phase 27A).

Comparisons are deterministic, read-only projections that measure the
delta between two runs of the **same methodology** for the same project.

Phase 27A supports:
  - Protein Tracker: per-group protein deltas, headline share deltas
  - WWF: food-group weight/share deltas, whole-diet plant/animal delta

Comparisons are never persisted; they are computed on demand from the
stored run summary payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PTGroupComparison(BaseModel):
    """Per-group protein delta for a single Protein Tracker group."""

    pt_group: str
    baseline_protein_kg: Decimal
    comparison_protein_kg: Decimal
    delta_protein_kg: Decimal  # comparison − baseline


class PTComparisonSummary(BaseModel):
    """Full Protein Tracker run-to-run comparison."""

    # Reporting period labels (human-readable labels, e.g. "2023", "2024")
    baseline_reporting_period: str
    comparison_reporting_period: str

    # Version strings — populated so callers can detect methodology/rules drift
    baseline_methodology_version: str
    comparison_methodology_version: str
    baseline_taxonomy_version: str
    comparison_taxonomy_version: str
    baseline_rules_version: str
    comparison_rules_version: str

    # Baseline headline figures
    baseline_plant_protein_kg: Decimal
    baseline_animal_protein_kg: Decimal
    baseline_total_protein_kg: Decimal
    baseline_plant_share_pct: Decimal | None
    baseline_animal_share_pct: Decimal | None

    # Comparison headline figures
    comparison_plant_protein_kg: Decimal
    comparison_animal_protein_kg: Decimal
    comparison_total_protein_kg: Decimal
    comparison_plant_share_pct: Decimal | None
    comparison_animal_share_pct: Decimal | None

    # Deltas (comparison − baseline).  None when either operand is None.
    delta_plant_protein_kg: Decimal
    delta_animal_protein_kg: Decimal
    delta_total_protein_kg: Decimal
    delta_plant_share_pct: Decimal | None
    delta_animal_share_pct: Decimal | None

    # Directional signal derived from delta_plant_share_pct.
    # "improving"  → plant share increased by > 0.1 pp
    # "declining"  → plant share decreased by > 0.1 pp
    # "stable"     → delta within ±0.1 pp or shares unavailable
    direction: str

    per_group: list[PTGroupComparison]


class WWFFoodGroupComparison(BaseModel):
    """Weight and share delta for one WWF food group."""

    food_group: str
    baseline_weight_kg: Decimal
    comparison_weight_kg: Decimal
    delta_weight_kg: Decimal  # comparison − baseline
    baseline_share_pct: Decimal
    comparison_share_pct: Decimal
    delta_share_pct: Decimal  # comparison − baseline
    phd_reference_share_pct: Decimal | None  # from baseline; fallback to comparison


class WWFComparisonSummary(BaseModel):
    """Full WWF run-to-run comparison."""

    baseline_reporting_period: str
    comparison_reporting_period: str
    baseline_methodology_version: str
    comparison_methodology_version: str
    baseline_taxonomy_version: str
    comparison_taxonomy_version: str
    baseline_rules_version: str
    comparison_rules_version: str

    baseline_total_weight_kg: Decimal
    comparison_total_weight_kg: Decimal
    delta_total_weight_kg: Decimal

    baseline_plant_weight_kg: Decimal
    comparison_plant_weight_kg: Decimal
    delta_plant_weight_kg: Decimal

    baseline_animal_weight_kg: Decimal
    comparison_animal_weight_kg: Decimal
    delta_animal_weight_kg: Decimal

    # Direction based on whether the plant weight fraction increased.
    # Threshold: 0.1 % of total weight.
    direction: str

    per_food_group: list[WWFFoodGroupComparison]


class RunComparisonResult(BaseModel):
    """Top-level on-demand comparison between two runs of the same methodology."""

    baseline_run_id: UUID
    comparison_run_id: UUID
    project_id: UUID
    methodology: str  # "protein_tracker" | "wwf"
    pt_comparison: PTComparisonSummary | None = None
    wwf_comparison: WWFComparisonSummary | None = None
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
