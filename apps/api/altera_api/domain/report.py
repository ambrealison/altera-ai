"""Client-facing report domain models (Phase 21).

A ReportDocument is assembled at request time from a RunRecord, a
Project, review queue items, and an optional ExportRecord (for approval
metadata). It is not stored separately — the calculation data is the
source of truth, and the report is a view over it.

No commercial fields appear here; the model exposes only methodology
outputs, version metadata, review summary, and approval status.
"""

from __future__ import annotations

from pydantic import BaseModel


class ClassificationSources(BaseModel):
    """How many products were resolved by each classification method."""

    deterministic: int
    ai: int
    manual_review: int
    total: int


class ReviewSummary(BaseModel):
    """Aggregate counts from the manual review queue for this run's project.

    ``pending`` = items still in_queue or being_reviewed at report time.
    ``top_reasons`` = up to 5 most common queue reasons by frequency.
    """

    total_reviewed: int
    accepted: int
    changed: int
    deferred: int
    pending: int
    top_reasons: list[str]


class PTGroupData(BaseModel):
    """Aggregate figures for one PT group."""

    pt_group: str
    item_count: int
    volume_kg: str
    protein_kg: str


class PTReportSection(BaseModel):
    """Protein Tracker methodology section of the report."""

    methodology_version: str
    methodology_source_edition: str
    taxonomy_version: str
    rules_version: str
    reporting_period_label: str
    # Headline protein figures
    plant_protein_kg: str
    animal_protein_kg: str
    total_in_scope_protein_kg: str
    plant_share_pct: str | None
    animal_share_pct: str | None
    # Four-group breakdown
    groups: list[PTGroupData]
    composite_note: str
    # Data quality
    out_of_scope_count: int
    unknown_count: int
    rows_with_per_product_split: int
    rows_protein_source_label: int
    rows_protein_source_reference_db: int
    # Classification provenance
    classification_sources: ClassificationSources
    pt_validation_status: str


class WWFFoodGroupData(BaseModel):
    """Aggregate figures for one WWF food group."""

    food_group: str
    weight_kg: str
    share_pct: str
    phd_reference_share_pct: str | None


class WWFReportSection(BaseModel):
    """WWF Planet-Based Diets methodology section of the report."""

    methodology_version: str
    methodology_source_edition: str
    taxonomy_version: str
    rules_version: str
    reporting_period_label: str
    # Headline weight
    total_in_scope_weight_kg: str
    # FG1–FG7 breakdown
    per_food_group: list[WWFFoodGroupData]
    # Composite Step 1 buckets
    composites_meat_based_kg: str
    composites_seafood_based_kg: str
    composites_vegetarian_kg: str
    composites_vegan_kg: str
    composites_total_weight_kg: str
    # Whole-diet context
    whole_diet_plant_weight_kg: str
    whole_diet_animal_weight_kg: str
    # Data quality
    out_of_scope_count: int
    unknown_count: int
    # Classification provenance
    classification_sources: ClassificationSources


class ReportMeta(BaseModel):
    """Header metadata shared across all report sections."""

    run_id: str
    project_name: str
    organisation_id: str
    reporting_period: str
    methodology: str
    generated_at: str
    # Approval lifecycle (from the associated ExportRecord)
    approval_status: str
    approved_by: str | None
    approved_at: str | None
    delivered_at: str | None
    export_id: str | None


class ReportDocument(BaseModel):
    """Full client-facing report for one run.

    Exactly one of ``pt_section`` / ``wwf_section`` is populated,
    matching ``meta.methodology``.
    """

    meta: ReportMeta
    executive_summary: str
    pt_section: PTReportSection | None
    wwf_section: WWFReportSection | None
    review_summary: ReviewSummary
