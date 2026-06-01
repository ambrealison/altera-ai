"""Report document builder (Phase 21).

Assembles a ReportDocument from a RunRecord, Project, review items,
and an optional ExportRecord (for approval metadata).

No arithmetic is performed here — we read pre-computed summaries from
the run's ``summary_payload``.  Classification source counts are
derived by looking up each product's active classification from the
store.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from altera_api.api.state import ExportRecord, PersistedRecommendation, RunRecord
from altera_api.domain.common import Methodology
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
)
from altera_api.domain.recommendation import Recommendation
from altera_api.domain.report import (
    ClassificationSources,
    PTGroupData,
    PTReportSection,
    ReportDocument,
    ReportMeta,
    ReviewSummary,
    WWFFoodGroupData,
    WWFReportSection,
)
from altera_api.domain.review import ManualReviewItem, ManualReviewStatus
from altera_api.domain.wwf import WWFCalculationSummary
from altera_api.exports.common import format_decimal
from altera_api.exports.contributors import pt_contributors, wwf_contributors
from altera_api.exports.coverage import build_coverage_section
from altera_api.persistence.protocol import StoreProtocol
from altera_api.recommendations.engine import generate_recommendations

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_APPROVAL_PHRASES: dict[str, str] = {
    "draft": "is being prepared",
    "under_review": "is under review by the Altera methodology team",
    "approved": "has been approved by the Altera methodology team",
    "rejected": "has been returned for revision by the Altera methodology team",
    "delivered": "has been reviewed, approved, and delivered by the Altera methodology team",
}


def _classification_sources(store: StoreProtocol, run: RunRecord) -> ClassificationSources:
    """Count how many products in this run were resolved by each source."""
    counts: Counter[str] = Counter()
    for row in run.rows_payload:
        raw_pid = row.get("product_id")
        if raw_pid is None:
            continue
        pid = UUID(raw_pid) if isinstance(raw_pid, str) else raw_pid
        if run.methodology is Methodology.PROTEIN_TRACKER:
            clf = store.get_pt_classification(pid)
        else:
            clf = store.get_wwf_classification(pid)
        counts[clf.source.value if clf else "unknown"] += 1
    return ClassificationSources(
        deterministic=counts.get("deterministic", 0),
        ai=counts.get("ai", 0),
        manual_review=counts.get("manual_review", 0),
        total=sum(counts.values()),
    )


def _review_summary(items: list[ManualReviewItem]) -> ReviewSummary:
    terminal = [i for i in items if i.status.is_terminal]
    pending = [i for i in items if not i.status.is_terminal]
    reason_counts: Counter[str] = Counter(i.reason.value for i in items)
    return ReviewSummary(
        total_reviewed=len(terminal),
        accepted=sum(1 for i in terminal if i.status is ManualReviewStatus.ACCEPTED),
        changed=sum(1 for i in terminal if i.status is ManualReviewStatus.CHANGED),
        deferred=sum(1 for i in terminal if i.status is ManualReviewStatus.DEFERRED),
        pending=len(pending),
        top_reasons=[r for r, _ in reason_counts.most_common(5)],
    )


def _pt_section(run: RunRecord, store: StoreProtocol, project: Project) -> PTReportSection:
    s = ProteinTrackerCalculationSummary.model_validate(run.summary_payload)
    sources = _classification_sources(store, run)

    if s.rows_with_per_product_split > 0:
        composite_note = (
            f"The 50/50 default protein split was applied to composite products, "
            f"except for {s.rows_with_per_product_split} row(s) that used a "
            f"per-product ingredient split."
        )
    else:
        composite_note = (
            "The 50/50 default protein split was applied to all composite products."
        )

    pt_positive, pt_watchout = pt_contributors(
        run.rows_payload, store.list_products_for_project(project.id)
    )

    by_group = {a.pt_group: a for a in s.per_group}
    groups = [
        PTGroupData(
            pt_group=g.value,
            item_count=by_group[g].item_count,
            volume_kg=format_decimal(by_group[g].volume_kg),
            protein_kg=format_decimal(by_group[g].protein_kg),
        )
        for g in (
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            ProteinTrackerGroup.ANIMAL_CORE,
        )
    ]

    return PTReportSection(
        methodology_version=s.methodology_version,
        methodology_source_edition=s.methodology_source_edition,
        taxonomy_version=s.taxonomy_version,
        rules_version=s.rules_version,
        reporting_period_label=s.reporting_period_label,
        plant_protein_kg=format_decimal(s.plant_protein_kg),
        animal_protein_kg=format_decimal(s.animal_protein_kg),
        total_in_scope_protein_kg=format_decimal(s.total_in_scope_protein_kg),
        plant_share_pct=format_decimal(s.plant_share_pct) if s.plant_share_pct is not None else None,
        animal_share_pct=format_decimal(s.animal_share_pct) if s.animal_share_pct is not None else None,
        groups=groups,
        composite_note=composite_note,
        out_of_scope_count=s.out_of_scope_count,
        unknown_count=s.unknown_count,
        rows_with_per_product_split=s.rows_with_per_product_split,
        rows_protein_source_label=s.rows_protein_source_label,
        rows_protein_source_reference_db=s.rows_protein_source_reference_db,
        classification_sources=sources,
        pt_validation_status=project.pt_validation_status.value,
        top_positive_contributors=pt_positive,
        top_watchout_contributors=pt_watchout,
    )


def _wwf_section(run: RunRecord, store: StoreProtocol, project: Project) -> WWFReportSection:
    s = WWFCalculationSummary.model_validate(run.summary_payload)
    sources = _classification_sources(store, run)

    wwf_positive, wwf_watchout = wwf_contributors(
        run.rows_payload, store.list_products_for_project(project.id)
    )

    per_food_group = [
        WWFFoodGroupData(
            food_group=a.food_group.value,
            weight_kg=format_decimal(a.weight_kg),
            share_pct=format_decimal(a.share_pct),
            phd_reference_share_pct=(
                format_decimal(a.phd_reference_share_pct)
                if a.phd_reference_share_pct is not None
                else None
            ),
        )
        for a in s.per_food_group
    ]

    return WWFReportSection(
        methodology_version=s.methodology_version,
        methodology_source_edition=s.methodology_source_edition,
        taxonomy_version=s.taxonomy_version,
        rules_version=s.rules_version,
        reporting_period_label=s.reporting_period_label,
        total_in_scope_weight_kg=format_decimal(s.total_sales_weight_in_scope_kg),
        per_food_group=per_food_group,
        composites_meat_based_kg=format_decimal(s.composites_meat_based_kg),
        composites_seafood_based_kg=format_decimal(s.composites_seafood_based_kg),
        composites_vegetarian_kg=format_decimal(s.composites_vegetarian_kg),
        composites_vegan_kg=format_decimal(s.composites_vegan_kg),
        composites_total_weight_kg=format_decimal(s.composites_total_weight_kg),
        whole_diet_plant_weight_kg=format_decimal(s.whole_diet_plant_weight_kg),
        whole_diet_animal_weight_kg=format_decimal(s.whole_diet_animal_weight_kg),
        out_of_scope_count=s.out_of_scope_count,
        unknown_count=s.unknown_count,
        classification_sources=sources,
        top_positive_contributors=wwf_positive,
        top_watchout_contributors=wwf_watchout,
    )


def _pt_executive_summary(
    s: ProteinTrackerCalculationSummary, status: str, project_name: str
) -> str:
    phrase = _APPROVAL_PHRASES.get(status, f"has status '{status}'")
    if s.plant_share_pct is not None:
        ratio_line = (
            f"a plant-source protein ratio of {format_decimal(s.plant_share_pct)}% "
            f"({format_decimal(s.plant_protein_kg)} kg plant-source, "
            f"{format_decimal(s.animal_protein_kg)} kg animal-source, "
            f"{format_decimal(s.total_in_scope_protein_kg)} kg total in-scope protein)"
        )
    else:
        ratio_line = "no in-scope protein products"
    return (
        f"For the {s.reporting_period_label} reporting period, {project_name} achieved "
        f"{ratio_line}. "
        f"The Protein Tracker methodology (version {s.methodology_version}, "
        f"{s.methodology_source_edition}) was applied. "
        f"This report {phrase}."
    )


def _wwf_executive_summary(
    s: WWFCalculationSummary, status: str, project_name: str
) -> str:
    phrase = _APPROVAL_PHRASES.get(status, f"has status '{status}'")
    return (
        f"For the {s.reporting_period_label} reporting period, {project_name}'s product range "
        f"covered {format_decimal(s.total_sales_weight_in_scope_kg)} kg of in-scope sales weight "
        f"across 7 WWF food groups. "
        f"The WWF Planet-Based Diets methodology (version {s.methodology_version}, "
        f"{s.methodology_source_edition}) was applied. "
        f"Note: this methodology measures product weight, not protein content. "
        f"This report {phrase}."
    )


# ---------------------------------------------------------------------------
# Recommendation helpers
# ---------------------------------------------------------------------------

_CLIENT_VISIBLE_REC_STATUSES = {"proposed", "accepted"}


def _persisted_to_recommendation(rec: PersistedRecommendation) -> Recommendation:
    return Recommendation(
        id=rec.id,
        run_id=rec.run_id,
        action_type=rec.action_type,
        category=rec.category,
        title=rec.title,
        description=rec.description,
        rationale=rec.rationale,
        expected_direction=rec.expected_direction,
        priority=rec.priority,
        confidence=rec.confidence,
        evidence=rec.evidence,
        status=rec.status,
        caveats=rec.caveats,
        client_facing=rec.client_facing,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_report_document(
    store: StoreProtocol,
    run: RunRecord,
    project: Project,
    export: ExportRecord | None = None,
    *,
    is_altera: bool = True,
) -> ReportDocument:
    """Build a ReportDocument from a run, project, and optional export.

    ``export`` supplies the approval metadata (status, approver, delivery
    timestamp).  When ``None``, the report is treated as draft.
    """
    status = export.approval_status if export else "draft"

    items = store.list_review_items_for_project(project.id, methodology=run.methodology)
    rev_summary = _review_summary(items)

    if run.methodology is Methodology.PROTEIN_TRACKER:
        pt = _pt_section(run, store, project)
        wwf = None
        s_pt = ProteinTrackerCalculationSummary.model_validate(run.summary_payload)
        exec_summary = _pt_executive_summary(s_pt, status, project.name)
        reporting_period = s_pt.reporting_period_label
    else:
        pt = None
        wwf = _wwf_section(run, store, project)
        s_wwf = WWFCalculationSummary.model_validate(run.summary_payload)
        exec_summary = _wwf_executive_summary(s_wwf, status, project.name)
        reporting_period = s_wwf.reporting_period_label

    meta = ReportMeta(
        run_id=str(run.id),
        project_name=project.name,
        organisation_id=str(project.organisation_id),
        reporting_period=reporting_period,
        methodology=run.methodology.value,
        generated_at=datetime.now(UTC).isoformat(),
        approval_status=status,
        approved_by=str(export.approved_by) if export and export.approved_by else None,
        approved_at=export.approved_at.isoformat() if export and export.approved_at else None,
        delivered_at=export.delivered_at.isoformat() if export and export.delivered_at else None,
        export_id=str(export.id) if export else None,
    )

    coverage = build_coverage_section(store, run, project)

    # --- Recommendations ---
    # Prefer persisted recommendations (Phase 25B).  Fall back to the engine
    # for Altera users when nothing is persisted yet; return empty for clients.
    persisted = store.list_recommendations_for_run(run.id)
    if persisted:
        if is_altera:
            recs = [_persisted_to_recommendation(r) for r in persisted]
        else:
            recs = [
                _persisted_to_recommendation(r)
                for r in persisted
                if r.status in _CLIENT_VISIBLE_REC_STATUSES
            ]
    elif is_altera:
        # Ephemeral engine fallback for Altera preview before any generate call.
        if run.methodology is Methodology.PROTEIN_TRACKER:
            s_pt_for_recs = ProteinTrackerCalculationSummary.model_validate(run.summary_payload)
            recs = generate_recommendations(
                Methodology.PROTEIN_TRACKER,
                pt_summary=s_pt_for_recs,
                uncertainty_level=coverage.uncertainty_level,
                products_total=coverage.products_total,
                products_unknown=coverage.products_unknown,
                products_ai_classified=coverage.products_ai_classified,
                products_with_missing_protein=coverage.products_with_missing_protein,
            )
        else:
            s_wwf_for_recs = WWFCalculationSummary.model_validate(run.summary_payload)
            step2_map = store.get_wwf_ingredients_by_project(project.id)
            wwf_step2_applied = len(step2_map)
            product_ids_in_run = {
                row["product_id"] for row in run.rows_payload if row.get("product_id")
            }
            products_in_run_list = [
                p
                for p in store.list_products_for_project(project.id)
                if str(p.id) in product_ids_in_run or p.id in product_ids_in_run
            ]
            own_brand_composite_count = 0
            branded_composite_count = 0
            for p in products_in_run_list:
                if p.wwf_fields is None:
                    continue
                clf = store.get_wwf_classification(p.id)
                if clf is None or not clf.wwf_is_composite:
                    continue
                if p.wwf_fields.is_own_brand:
                    own_brand_composite_count += 1
                else:
                    branded_composite_count += 1

            recs = generate_recommendations(
                Methodology.WWF,
                wwf_summary=s_wwf_for_recs,
                uncertainty_level=coverage.uncertainty_level,
                products_total=coverage.products_total,
                products_unknown=coverage.products_unknown,
                products_ai_classified=coverage.products_ai_classified,
                wwf_step2_applied_count=wwf_step2_applied,
                wwf_own_brand_composite_count=own_brand_composite_count,
                wwf_branded_composite_count=branded_composite_count,
            )
    else:
        # Client with no persisted (proposed/accepted) recommendations yet.
        recs = []

    return ReportDocument(
        meta=meta,
        executive_summary=exec_summary,
        pt_section=pt,
        wwf_section=wwf,
        review_summary=rev_summary,
        coverage=coverage,
        recommendations=recs,
    )
