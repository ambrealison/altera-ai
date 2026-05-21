"""Phase 34A — guided retailer-workflow state aggregator.

Reads the per-project picture across uploads, products, classifications,
review queue, and nutrition enrichment records, and emits a single
``WorkflowStatus`` payload the frontend uses to render the stepper +
"next recommended action" + "is calculation ready?" gate.

This module is pure aggregation: it does not write to the store and
does not call out to AI/Postgres beyond the StoreProtocol read API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from altera_api.domain.common import Methodology
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)
from altera_api.domain.project import Project
from altera_api.domain.review import ManualReviewStatus
from altera_api.persistence.protocol import StoreProtocol

# Step keys are stable identifiers consumed by the frontend stepper.
# Display labels live on the frontend so non-English locales can be
# swapped without a backend deploy. We do, however, ship a default
# French label here so the API response stands alone for documentation
# and for the workflow-status response payload.
StepStatus = Literal[
    "complete",
    "ready",
    "needs_action",
    "blocked",
    "available",
    "locked",
    "not_needed",
    "disabled",
]


@dataclass(frozen=True)
class BlockingReason:
    code: str
    label: str
    count: int = 0
    next_action: str | None = None


@dataclass(frozen=True)
class NextAction:
    label: str
    action: str           # machine-readable identifier
    href: str | None = None


@dataclass(frozen=True)
class WorkflowStep:
    key: str
    label: str
    status: StepStatus
    progress_pct: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    blocking_reasons: list[BlockingReason] = field(default_factory=list)
    # Phase 34B — wizard fields
    accessible: bool = False    # user can navigate to this step
    editable: bool = False       # user can re-run / re-edit this step
    summary: str | None = None   # one-liner shown on completed steps


@dataclass(frozen=True)
class WorkflowStatus:
    project_id: str
    methodologies_enabled: list[str]
    overall_progress_pct: int
    current_step: str
    next_action: NextAction | None
    steps: list[WorkflowStep]
    # Phase 34B alias — frontend wizard uses active_step
    active_step: str | None = None


# ---------------------------------------------------------------------------
# Internal counters
# ---------------------------------------------------------------------------


@dataclass
class _Counts:
    total_uploads: int = 0
    total_products: int = 0
    pt_products: int = 0
    pt_classified: int = 0
    pt_unknown: int = 0           # classified but pt_group = UNKNOWN
    pt_needs_review: int = 0      # in IN_QUEUE or REVIEWING
    pt_protein_retailer: int = 0  # has retailer protein_pct
    pt_protein_enriched: int = 0  # protein_pct via enrichment record
    pt_nevo_records: int = 0
    pt_nevo_with_split: int = 0
    pt_ciqual_records: int = 0
    pt_missing_nutrition: int = 0  # no retailer, no enrichment
    pt_eligible: int = 0           # classified + has usable nutrition


def _gather_counts(store: StoreProtocol, project: Project) -> _Counts:
    counts = _Counts()
    counts.total_uploads = len(store.list_uploads_for_project(project.id))
    products = store.list_products_for_project(project.id)
    counts.total_products = len(products)

    pt_enabled_for_project = Methodology.PROTEIN_TRACKER in project.methodologies_enabled

    # Quick lookup for review items.
    review_items = {
        item.product_id: item
        for item in store.list_review_items_for_project(
            project.id,
            methodology=Methodology.PROTEIN_TRACKER if pt_enabled_for_project else None,
        )
    }

    for product in products:
        if (
            not pt_enabled_for_project
            or product.pt_fields is None
            or Methodology.PROTEIN_TRACKER not in product.methodologies_enabled
        ):
            continue
        counts.pt_products += 1

        classification = store.get_pt_classification(product.id)
        has_unknown = (
            classification is not None
            and classification.pt_group.value == "unknown"
        )
        review = review_items.get(product.id)
        review_is_open = (
            review is not None
            and review.status in (ManualReviewStatus.IN_QUEUE, ManualReviewStatus.REVIEWING)
        )

        if classification is None:
            # Not classified yet at all.
            pass
        else:
            counts.pt_classified += 1
            if has_unknown:
                counts.pt_unknown += 1
            if review_is_open:
                counts.pt_needs_review += 1

        # Nutrition resolution: retailer first, then any ENRICHED record.
        if product.pt_fields.protein_pct is not None:
            counts.pt_protein_retailer += 1
        else:
            records = store.get_enrichment_records_for_product(product.id)
            protein_records = [
                r
                for r in records
                if r.nutrient == "protein_pct"
                and r.status is NutritionEnrichmentStatus.ENRICHED
                and r.enriched_value is not None
            ]
            if protein_records:
                counts.pt_protein_enriched += 1
                # Record source provenance for the per-source counters.
                # A product may have records from multiple sources; we
                # count each source once per product to keep totals
                # meaningful in the UI.
                sources = {r.source for r in protein_records}
                if NutritionEnrichmentSource.NEVO in sources:
                    counts.pt_nevo_records += 1
                    has_plant = any(
                        r.nutrient == "plant_protein_pct"
                        and r.source is NutritionEnrichmentSource.NEVO
                        and r.status is NutritionEnrichmentStatus.ENRICHED
                        and r.enriched_value is not None
                        for r in records
                    )
                    has_animal = any(
                        r.nutrient == "animal_protein_pct"
                        and r.source is NutritionEnrichmentSource.NEVO
                        and r.status is NutritionEnrichmentStatus.ENRICHED
                        and r.enriched_value is not None
                        for r in records
                    )
                    if has_plant and has_animal:
                        counts.pt_nevo_with_split += 1
                elif NutritionEnrichmentSource.CIQUAL in sources:
                    counts.pt_ciqual_records += 1
            else:
                counts.pt_missing_nutrition += 1

    # A row is eligible for calculation iff: classified (any group),
    # NOT in open review, AND has usable nutrition (retailer or
    # enrichment). The calculation skips out_of_scope / unknown groups
    # when summing, but they still count as "eligible" structurally.
    if pt_enabled_for_project:
        for product in products:
            if (
                product.pt_fields is None
                or Methodology.PROTEIN_TRACKER not in product.methodologies_enabled
            ):
                continue
            classification = store.get_pt_classification(product.id)
            if classification is None:
                continue
            review = review_items.get(product.id)
            if review is not None and review.status in (
                ManualReviewStatus.IN_QUEUE,
                ManualReviewStatus.REVIEWING,
            ):
                continue
            if product.pt_fields.protein_pct is not None:
                counts.pt_eligible += 1
                continue
            records = store.get_enrichment_records_for_product(product.id)
            if any(
                r.nutrient == "protein_pct"
                and r.status is NutritionEnrichmentStatus.ENRICHED
                and r.enriched_value is not None
                for r in records
            ):
                counts.pt_eligible += 1
    return counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_workflow_status(store: StoreProtocol, project: Project) -> WorkflowStatus:
    """Aggregate per-step status for the guided retailer workflow.

    The returned ``WorkflowStatus`` mirrors the shape documented in
    docs/development/workflow.md. Only the Protein Tracker pipeline is
    enumerated step-by-step; WWF will be added in a future phase.
    """
    counts = _gather_counts(store, project)
    methodologies = sorted(m.value for m in project.methodologies_enabled)

    steps: list[WorkflowStep] = []
    has_runs = len(store.list_runs_for_project(project.id)) > 0

    # 1. upload
    upload_status: StepStatus = "complete" if counts.total_uploads > 0 else "needs_action"
    steps.append(
        WorkflowStep(
            key="upload",
            label="Import du fichier",
            status=upload_status,
            progress_pct=100 if counts.total_uploads > 0 else 0,
            counts={"uploads": counts.total_uploads, "products": counts.total_products},
            accessible=True,
            editable=True,
            summary=(
                f"{counts.total_products} produit(s) importé(s)"
                if counts.total_uploads > 0
                else None
            ),
        )
    )

    # 2. methodology — selected at project creation; treated complete.
    methodology_status: StepStatus = "complete" if methodologies else "needs_action"
    steps.append(
        WorkflowStep(
            key="methodology",
            label="Méthodologie",
            status=methodology_status,
            progress_pct=100 if methodologies else 0,
            counts={},
            accessible=True,
            editable=not has_runs,
            summary=(
                ", ".join(m.replace("_", " ").title() for m in methodologies)
                if methodologies
                else None
            ),
        )
    )

    # 3. mapping — implicit; ingestion only completes when mapping is OK.
    mapping_status: StepStatus = "complete" if counts.total_products > 0 else (
        "needs_action" if counts.total_uploads > 0 else "locked"
    )
    steps.append(
        WorkflowStep(
            key="mapping",
            label="Mapping des colonnes",
            status=mapping_status,
            progress_pct=100 if counts.total_products > 0 else 0,
            counts={},
            accessible=mapping_status != "locked",
            editable=mapping_status != "locked",
            summary="Colonnes mappées" if counts.total_products > 0 else None,
        )
    )

    # 4. ingestion
    ingestion_status: StepStatus = "complete" if counts.total_products > 0 else "locked"
    steps.append(
        WorkflowStep(
            key="ingestion",
            label="Ingestion",
            status=ingestion_status,
            progress_pct=100 if counts.total_products > 0 else 0,
            counts={"products": counts.total_products},
            accessible=ingestion_status != "locked",
            editable=False,
            summary=(
                f"{counts.total_products} produit(s)"
                if counts.total_products > 0
                else None
            ),
        )
    )

    pt_total = counts.pt_products
    pt_remaining_to_classify = max(0, pt_total - counts.pt_classified)

    # 5. deterministic classification — once any PT product is
    #    classified we consider this step "started". It's complete when
    #    every PT product has SOME classification (even if some end up
    #    UNKNOWN — those go to manual review next).
    if pt_total == 0:
        det_status: StepStatus = "locked"
    elif counts.pt_classified == pt_total:
        det_status = "complete"
    else:
        det_status = "needs_action"
    steps.append(
        WorkflowStep(
            key="deterministic_classification",
            label="Classification déterministe",
            status=det_status,
            progress_pct=(
                int(100 * counts.pt_classified / pt_total) if pt_total else 0
            ),
            counts={
                "classified": counts.pt_classified,
                "remaining": pt_remaining_to_classify,
                "in_review": counts.pt_needs_review,
            },
            accessible=det_status != "locked",
            editable=det_status not in ("locked",),
            summary=(
                f"{counts.pt_classified}/{pt_total} produit(s) classifiés"
                if pt_total > 0
                else None
            ),
        )
    )

    # 6. AI classification — exposed as "available" only when there are
    #    still unclassified PT products (or unknown ones).
    if pt_remaining_to_classify > 0 or counts.pt_unknown > 0:
        ai_status: StepStatus = "available"
    elif pt_total == 0:
        ai_status = "locked"
    else:
        ai_status = "not_needed"
    steps.append(
        WorkflowStep(
            key="ai_classification",
            label="Classification IA",
            status=ai_status,
            progress_pct=100 if ai_status in ("not_needed", "complete") else 0,
            counts={
                "remaining": pt_remaining_to_classify,
                "unknown": counts.pt_unknown,
            },
            accessible=ai_status != "locked",
            editable=ai_status not in ("locked", "not_needed"),
            summary=(
                "Aucune classification IA nécessaire"
                if ai_status == "not_needed"
                else None
            ),
        )
    )

    # 7. manual classification review
    if counts.pt_needs_review > 0:
        review_status: StepStatus = "needs_action"
    elif pt_total == 0:
        review_status = "locked"
    else:
        review_status = "not_needed"
    steps.append(
        WorkflowStep(
            key="manual_classification_review",
            label="Validation manuelle des catégories",
            status=review_status,
            progress_pct=100 if review_status in ("not_needed", "complete") else 0,
            counts={"pending": counts.pt_needs_review},
            accessible=review_status != "locked",
            editable=review_status not in ("locked",),
            summary=(
                "Aucun produit à valider"
                if review_status == "not_needed"
                else f"{counts.pt_needs_review} produit(s) en attente"
                if counts.pt_needs_review > 0
                else None
            ),
        )
    )

    # 8. NEVO enrichment
    pt_needs_nutrition = counts.pt_missing_nutrition
    if pt_total == 0:
        nevo_status: StepStatus = "locked"
    elif counts.pt_nevo_records > 0 and pt_needs_nutrition == 0:
        nevo_status = "complete"
    elif pt_needs_nutrition > 0:
        nevo_status = "available"
    else:
        nevo_status = "not_needed"
    steps.append(
        WorkflowStep(
            key="nutrition_enrichment_nevo",
            label="Enrichissement NEVO",
            status=nevo_status,
            progress_pct=(
                int(
                    100
                    * (counts.pt_protein_retailer + counts.pt_protein_enriched)
                    / pt_total
                )
                if pt_total else 0
            ),
            counts={
                "matched": counts.pt_nevo_records,
                "with_split": counts.pt_nevo_with_split,
                "no_match": pt_needs_nutrition,
            },
            accessible=nevo_status != "locked",
            editable=nevo_status not in ("locked",),
            summary=(
                f"{counts.pt_nevo_records} correspondance(s) NEVO"
                if counts.pt_nevo_records > 0
                else "Non requis"
                if nevo_status == "not_needed"
                else None
            ),
        )
    )

    # 9. CIQUAL fallback — only "available" once NEVO has been tried
    #    and there are still products without nutrition.
    nevo_attempted = counts.pt_nevo_records > 0
    if pt_total == 0:
        ciqual_status: StepStatus = "locked"
    elif pt_needs_nutrition == 0:
        ciqual_status = "not_needed"
    elif nevo_attempted and pt_needs_nutrition > 0:
        ciqual_status = "available"
    elif counts.pt_ciqual_records > 0:
        ciqual_status = "complete"
    else:
        ciqual_status = "locked"   # try NEVO first
    steps.append(
        WorkflowStep(
            key="nutrition_enrichment_ciqual",
            label="Fallback CIQUAL",
            status=ciqual_status,
            counts={
                "matched_total_only": counts.pt_ciqual_records,
                "remaining": pt_needs_nutrition,
            },
            accessible=ciqual_status != "locked",
            editable=ciqual_status not in ("locked",),
            summary=(
                f"{counts.pt_ciqual_records} correspondance(s) CIQUAL"
                if counts.pt_ciqual_records > 0
                else "Non requis"
                if ciqual_status == "not_needed"
                else None
            ),
        )
    )

    # 10. manual nutrition review (deferred; placeholder).
    mnr_status: StepStatus = (
        "needs_action" if pt_needs_nutrition > 0 else
        "not_needed" if pt_total else
        "locked"
    )
    steps.append(
        WorkflowStep(
            key="manual_nutrition_review",
            label="Validation manuelle nutrition",
            status=mnr_status,
            counts={"pending": pt_needs_nutrition},
            accessible=mnr_status != "locked",
            editable=mnr_status not in ("locked",),
            summary=(
                f"{pt_needs_nutrition} produit(s) sans nutrition"
                if pt_needs_nutrition > 0
                else "Aucun produit à valider"
                if mnr_status == "not_needed"
                else None
            ),
        )
    )

    # 11. calculation — blocked unless every PT product is classified,
    #     no review is open, and at least one row is eligible.
    blocking: list[BlockingReason] = []
    if pt_total == 0:
        blocking.append(
            BlockingReason(
                code="no_eligible_products",
                label="Aucun produit Protein Tracker importé",
                count=0,
                next_action="upload",
            )
        )
    if pt_remaining_to_classify > 0:
        blocking.append(
            BlockingReason(
                code="classification_required",
                label="Produits non classifiés",
                count=pt_remaining_to_classify,
                next_action="classify",
            )
        )
    if counts.pt_needs_review > 0:
        blocking.append(
            BlockingReason(
                code="review_pending",
                label="Produits en attente de validation manuelle",
                count=counts.pt_needs_review,
                next_action="open_review_queue",
            )
        )
    if pt_total > 0 and pt_needs_nutrition > 0:
        blocking.append(
            BlockingReason(
                code="nutrition_required",
                label="Produits sans donnée nutritionnelle exploitable",
                count=pt_needs_nutrition,
                next_action="apply_nevo",
            )
        )
    if pt_total > 0 and counts.pt_eligible == 0 and not blocking:
        # All filtered out (e.g. all OUT_OF_SCOPE) — surface that.
        blocking.append(
            BlockingReason(
                code="no_eligible_products",
                label="Aucun produit éligible au calcul",
                count=0,
                next_action="open_review_queue",
            )
        )

    if blocking:
        calc_status: StepStatus = "blocked"
    elif pt_total > 0 and counts.pt_eligible > 0:
        calc_status = "ready"
    else:
        calc_status = "locked"
    steps.append(
        WorkflowStep(
            key="calculation",
            label="Calcul du ratio",
            status=calc_status,
            counts={"eligible_rows": counts.pt_eligible},
            blocking_reasons=blocking,
            accessible=calc_status not in ("locked",),
            editable=False,
            summary=(
                f"{counts.pt_eligible} ligne(s) éligible(s)"
                if calc_status == "ready"
                else None
            ),
        )
    )

    # 12. report — locked until a run exists.
    runs_list = store.list_runs_for_project(project.id)
    run_count = len(runs_list)
    report_status: StepStatus = "complete" if has_runs else "locked"
    steps.append(
        WorkflowStep(
            key="report",
            label="Rapport",
            status=report_status,
            counts={"runs": run_count},
            accessible=has_runs,
            editable=False,
            summary=(
                f"{run_count} calcul(s) effectué(s)"
                if has_runs
                else None
            ),
        )
    )

    # Pick the "current" step: the first step whose status is not in
    # {complete, not_needed, locked}.
    current = next(
        (
            s.key
            for s in steps
            if s.status not in ("complete", "not_needed", "locked")
        ),
        steps[-1].key,
    )
    # Overall progress — average completion across the 12 steps; locked
    # / not_needed steps count as 100% since they don't apply.
    progress_total = sum(
        100
        if s.status in ("complete", "not_needed", "locked")
        else s.progress_pct
        for s in steps
    )
    overall_pct = int(progress_total / len(steps)) if steps else 0

    next_action: NextAction | None = None
    next_action_map: dict[str, NextAction] = {
        "upload": NextAction(label="Importer un fichier", action="upload", href="upload"),
        "methodology": NextAction(
            label="Confirmer la méthodologie",
            action="methodology",
            href=None,
        ),
        "mapping": NextAction(
            label="Vérifier le mapping", action="mapping", href="upload"
        ),
        "ingestion": NextAction(
            label="Lancer l’ingestion", action="ingest", href="upload"
        ),
        "deterministic_classification": NextAction(
            label="Classifier les produits",
            action="classify",
            href="upload",
        ),
        "ai_classification": NextAction(
            label="Lancer la classification IA",
            action="classify_ai",
            href="upload",
        ),
        "manual_classification_review": NextAction(
            label="Ouvrir la validation manuelle",
            action="open_review_queue",
            href="review",
        ),
        "nutrition_enrichment_nevo": NextAction(
            label="Enrichir avec NEVO",
            action="apply_nevo",
            href=None,
        ),
        "nutrition_enrichment_ciqual": NextAction(
            label="Essayer CIQUAL pour les protéines restantes",
            action="apply_ciqual",
            href=None,
        ),
        "manual_nutrition_review": NextAction(
            label="Ouvrir la validation nutrition",
            action="open_nutrition_review",
            href="review",
        ),
        "calculation": NextAction(
            label="Lancer le calcul", action="run_calculation", href="runs"
        ),
        "report": NextAction(label="Voir le rapport", action="open_report", href="runs"),
    }
    next_action = next_action_map.get(current)

    return WorkflowStatus(
        project_id=str(project.id),
        methodologies_enabled=methodologies,
        overall_progress_pct=overall_pct,
        current_step=current,
        active_step=current,
        next_action=next_action,
        steps=steps,
    )
