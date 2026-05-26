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
from uuid import UUID

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
class MethodologyClassificationCounts:
    """Phase WWF-H — per-methodology classification status.

    Lets the PT+WWF wizard render two separate classification cards
    with accurate per-methodology progress, instead of inferring
    everything from the PT-shaped ``ai_classification`` step.

    All fields are populated; absence of data is encoded as 0.
    """

    methodology: str       # "protein_tracker" | "wwf"
    total: int             # products eligible for this methodology
    classified: int        # products with a classification row
    pending: int           # total - classified
    needs_review: int      # in IN_QUEUE or REVIEWING for this methodology
    unknown: int           # classified rows with food_group=unknown
    failed: int = 0        # reserved for future "classification_failed" state
    status: StepStatus = "locked"


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
    # Phase WWF-H — backward-compatible per-methodology counts.
    # Empty dict when neither methodology is enabled (defensive).
    classification_by_methodology: dict[
        str, MethodologyClassificationCounts
    ] = field(default_factory=dict)


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
    # Phase WWF-H — WWF parallel counts.
    wwf_products: int = 0
    wwf_classified: int = 0
    wwf_unknown: int = 0           # classified but food_group = UNKNOWN
    wwf_needs_review: int = 0      # in IN_QUEUE or REVIEWING for WWF


def _gather_counts(store: StoreProtocol, project: Project) -> _Counts:
    """Phase 34Z — single-pass aggregator with bulk lookups.

    The previous implementation made up to 4 × N + 3 Supabase HTTP
    round-trips on a Postgres-backed store: 1 products fetch, 1
    uploads, 1 reviews, then ``get_pt_classification`` ×N (×2 loops),
    plus ``get_enrichment_records_for_product`` ×N (×2 loops). On a
    1050-product project that's >4200 round-trips, far beyond
    Render's request timeout, and the underlying PostgREST query
    eventually fails with ``JSON could not be generated``.

    This rewrite does **5** total HTTP round-trips regardless of N:

      1. ``list_uploads_for_project``       (1 call)
      2. ``list_products_for_project``      (1 call, paginated)
      3. ``list_review_items_for_project``  (1 call)
      4. ``get_pt_classifications_bulk``    (1 call, IN(…))
      5. ``get_enrichment_records_bulk``    (1 call, IN(…))

    Then the per-product checks are pure Python set/dict membership.
    """
    counts = _Counts()
    counts.total_uploads = len(store.list_uploads_for_project(project.id))
    products = store.list_products_for_project(project.id)
    counts.total_products = len(products)

    pt_enabled_for_project = (
        Methodology.PROTEIN_TRACKER in project.methodologies_enabled
    )
    wwf_enabled_for_project = (
        Methodology.WWF in project.methodologies_enabled
    )

    review_items = {
        item.product_id: item
        for item in store.list_review_items_for_project(
            project.id,
            methodology=(
                Methodology.PROTEIN_TRACKER if pt_enabled_for_project else None
            ),
        )
    }

    # Phase WWF-H — separate review queue lookup for WWF when enabled.
    # PT and WWF review queues are distinct (review items carry a
    # methodology). We keep them in independent dicts so the per-
    # methodology counters can attribute "in_review" correctly.
    wwf_review_items: dict[UUID, object] = {}
    if wwf_enabled_for_project:
        wwf_review_items = {
            item.product_id: item
            for item in store.list_review_items_for_project(
                project.id,
                methodology=Methodology.WWF,
            )
        }

    # Phase 34Z — pre-fetch classifications + enrichment records for
    # every PT-eligible product in this project in two HTTP calls.
    pt_product_ids: list[UUID] = []
    if pt_enabled_for_project:
        pt_product_ids = [
            p.id
            for p in products
            if p.pt_fields is not None
            and Methodology.PROTEIN_TRACKER in p.methodologies_enabled
        ]
    classifications = (
        store.get_pt_classifications_bulk(pt_product_ids)
        if pt_product_ids
        else {}
    )
    enrichment_by_product = (
        store.get_enrichment_records_bulk(pt_product_ids)
        if pt_product_ids
        else {}
    )

    # Phase WWF-H — parallel WWF classification bulk fetch.
    wwf_product_ids: list[UUID] = []
    if wwf_enabled_for_project:
        wwf_product_ids = [
            p.id
            for p in products
            if Methodology.WWF in p.methodologies_enabled
        ]
    wwf_classifications = (
        store.get_wwf_classifications_bulk(wwf_product_ids)
        if wwf_product_ids
        else {}
    )

    for product in products:
        if (
            not pt_enabled_for_project
            or product.pt_fields is None
            or Methodology.PROTEIN_TRACKER not in product.methodologies_enabled
        ):
            continue
        counts.pt_products += 1

        classification = classifications.get(product.id)
        has_unknown = (
            classification is not None
            and classification.pt_group.value == "unknown"
        )
        review = review_items.get(product.id)
        review_is_open = (
            review is not None
            and review.status
            in (ManualReviewStatus.IN_QUEUE, ManualReviewStatus.REVIEWING)
        )

        if classification is not None:
            counts.pt_classified += 1
            if has_unknown:
                counts.pt_unknown += 1
            if review_is_open:
                counts.pt_needs_review += 1

        # Nutrition resolution: retailer first, then any ENRICHED record.
        if product.pt_fields.protein_pct is not None:
            counts.pt_protein_retailer += 1
            if (
                classification is not None
                and not review_is_open
            ):
                counts.pt_eligible += 1
        else:
            records = enrichment_by_product.get(product.id, [])
            protein_records = [
                r
                for r in records
                if r.nutrient == "protein_pct"
                and r.status is NutritionEnrichmentStatus.ENRICHED
                and r.enriched_value is not None
            ]
            if protein_records:
                counts.pt_protein_enriched += 1
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
                if classification is not None and not review_is_open:
                    counts.pt_eligible += 1
            else:
                counts.pt_missing_nutrition += 1

    # Phase WWF-H — second pass for WWF counts. We walk the same
    # products list but only inspect WWF-eligible rows. Adds no new
    # round-trips (the bulk classifications + review list were
    # fetched above).
    if wwf_enabled_for_project:
        for product in products:
            if Methodology.WWF not in product.methodologies_enabled:
                continue
            counts.wwf_products += 1
            wwf_cls = wwf_classifications.get(product.id)
            wwf_review = wwf_review_items.get(product.id)
            wwf_review_open = (
                wwf_review is not None
                and getattr(wwf_review, "status", None)
                in (ManualReviewStatus.IN_QUEUE, ManualReviewStatus.REVIEWING)
            )
            if wwf_cls is not None:
                counts.wwf_classified += 1
                if wwf_cls.wwf_food_group.value == "unknown":
                    counts.wwf_unknown += 1
                if wwf_review_open:
                    counts.wwf_needs_review += 1
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

    # 2. methodology — selected at project creation. Phase 34K — only
    # counts as "complete" once the user has actually started using
    # the project (an upload exists). Without this gate a brand-new
    # project would show 12.5% on the progress bar before the user
    # has done anything.
    if methodologies and counts.total_uploads > 0:
        methodology_status: StepStatus = "complete"
    elif methodologies:
        methodology_status = "needs_action"
    else:
        methodology_status = "needs_action"
    steps.append(
        WorkflowStep(
            key="methodology",
            label="Méthodologie",
            status=methodology_status,
            progress_pct=100 if methodology_status == "complete" else 0,
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

    # 5. AI classification — Phase 34I makes this the primary classifier
    #    (the legacy deterministic-only step has been removed from the
    #    user-facing workflow). Available whenever there are PT products
    #    in scope; complete once every product has a classification.
    if pt_total == 0:
        ai_status: StepStatus = "locked"
    elif counts.pt_classified == pt_total:
        ai_status = "complete"
    else:
        ai_status = "needs_action"
    steps.append(
        WorkflowStep(
            key="ai_classification",
            label="Classification IA",
            status=ai_status,
            progress_pct=(
                int(100 * counts.pt_classified / pt_total) if pt_total else 0
            ),
            counts={
                "classified": counts.pt_classified,
                "remaining": pt_remaining_to_classify,
                "in_review": counts.pt_needs_review,
            },
            accessible=ai_status != "locked",
            editable=ai_status not in ("locked",),
            summary=(
                f"{counts.pt_classified}/{pt_total} produit(s) classifiés"
                if pt_total > 0
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

    # 8. NEVO enrichment.
    # Phase 34M — NEVO step becomes "complete" once apply-references
    # has been called for the project (regardless of how many products
    # actually matched). Previously the step only completed when
    # pt_needs_nutrition reached 0, which was unreachable on real
    # retailer CSVs where 25+ of 33 products couldn't find a NEVO
    # entry. We detect attempts by looking for ANY enrichment record
    # on a project's products — Phase 34M now writes a FAILED record
    # even for no-match products so this signal is reliable across
    # both the in-memory and Postgres stores.
    # Phase 34Z — boolean probe instead of materialising every
    # enrichment record. The 34Z initial pass switched to
    # ``list_enrichment_records_for_project`` but that helper still
    # builds a 1050-id ``.in_(...)`` URL on Postgres, which fails
    # with PostgREST 400 "JSON could not be generated" on large
    # projects. ``project_has_any_enrichment`` uses a head-only
    # ``count="exact"`` probe that returns no rows — bounded HTTP
    # regardless of project size.
    try:
        nevo_attempted = store.project_has_any_enrichment(project.id)
    except Exception:
        nevo_attempted = False
    pt_needs_nutrition = counts.pt_missing_nutrition
    if pt_total == 0:
        nevo_status: StepStatus = "locked"
    elif nevo_attempted:
        nevo_status = "complete"
    elif pt_needs_nutrition == 0:
        # Every product already has retailer-provided nutrition; NEVO
        # is not required to attempt.
        nevo_status = "not_needed"
    else:
        nevo_status = "available"
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
    # Phase 34L — CIQUAL is no longer part of the normal user flow.
    # CIQUAL provides total protein only (no plant/animal split) which
    # is fundamentally insufficient for Protein Tracker. The CIQUAL
    # endpoint stays in the codebase for admin/debug (Altera-only). We
    # keep the legacy key emitted but always mark it ``not_needed`` so
    # the frontend can filter it out without breaking persisted
    # references in client tooling / docs.
    steps.append(
        WorkflowStep(
            key="nutrition_enrichment_ciqual",
            label="Fallback CIQUAL (admin/debug)",
            status="not_needed",
            counts={
                "matched_total_only": counts.pt_ciqual_records,
                "remaining": pt_needs_nutrition,
            },
            accessible=False,
            editable=False,
            summary="Étape admin/debug — retirée du parcours utilisateur",
        )
    )

    # Phase 34L — new Validation nutritionnelle step. Sits between
    # NEVO and Calculation. Status reflects whether NEVO leaves any
    # products without usable nutrition (`needs_action`), everything
    # is ready (`complete`), or nothing applies yet (`locked`).
    if pt_total == 0:
        nv_status: StepStatus = "locked"
    elif pt_needs_nutrition == 0:
        nv_status = "complete"
    else:
        nv_status = "needs_action"
    steps.append(
        WorkflowStep(
            key="nutrition_validation",
            label="Validation nutritionnelle",
            status=nv_status,
            counts={
                "ready": pt_total - pt_needs_nutrition,
                "missing": pt_needs_nutrition,
            },
            accessible=nv_status != "locked",
            editable=nv_status not in ("locked",),
            summary=(
                f"{pt_total - pt_needs_nutrition}/{pt_total} produit(s) prêt(s)"
                if pt_total > 0
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
    # Phase 34K — progress is the count of *user-visible* wizard steps
    # that are done, divided by the wizard's step count (8). Earlier
    # code averaged ``progress_pct`` over every emitted backend step
    # and counted ``locked`` as 100%, which made a brand-new project
    # display ~65% because the locked downstream steps inflated the
    # ratio. The new rule: only ``complete`` and ``not_needed`` count
    # as done; ``locked`` / ``needs_action`` / ``available`` /
    # ``blocked`` count as not done.
    _WIZARD_STEP_KEYS = (
        "upload",
        "methodology",
        "ai_classification",
        "manual_classification_review",
        "nutrition_enrichment_nevo",
        # Phase 34L — CIQUAL replaced by Validation nutritionnelle.
        "nutrition_validation",
        "calculation",
        "report",
    )
    wizard_step_map = {s.key: s for s in steps}
    done_count = sum(
        1
        for k in _WIZARD_STEP_KEYS
        if k in wizard_step_map
        and wizard_step_map[k].status in ("complete", "not_needed")
    )
    overall_pct = int(100 * done_count / len(_WIZARD_STEP_KEYS))

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

    # Phase WWF-H — per-methodology classification counts so the
    # PT+WWF wizard can render two cards with accurate progress.
    classification_by_methodology: dict[
        str, MethodologyClassificationCounts
    ] = {}
    if Methodology.PROTEIN_TRACKER in project.methodologies_enabled:
        pt_pending = max(0, counts.pt_products - counts.pt_classified)
        if counts.pt_products == 0:
            pt_status: StepStatus = "locked"
        elif counts.pt_classified == counts.pt_products:
            pt_status = "complete"
        else:
            pt_status = "needs_action"
        classification_by_methodology["protein_tracker"] = (
            MethodologyClassificationCounts(
                methodology="protein_tracker",
                total=counts.pt_products,
                classified=counts.pt_classified,
                pending=pt_pending,
                needs_review=counts.pt_needs_review,
                unknown=counts.pt_unknown,
                status=pt_status,
            )
        )
    if Methodology.WWF in project.methodologies_enabled:
        wwf_pending = max(0, counts.wwf_products - counts.wwf_classified)
        if counts.wwf_products == 0:
            wwf_status: StepStatus = "locked"
        elif counts.wwf_classified == counts.wwf_products:
            wwf_status = "complete"
        else:
            wwf_status = "needs_action"
        classification_by_methodology["wwf"] = (
            MethodologyClassificationCounts(
                methodology="wwf",
                total=counts.wwf_products,
                classified=counts.wwf_classified,
                pending=wwf_pending,
                needs_review=counts.wwf_needs_review,
                unknown=counts.wwf_unknown,
                status=wwf_status,
            )
        )

    return WorkflowStatus(
        project_id=str(project.id),
        methodologies_enabled=methodologies,
        overall_progress_pct=overall_pct,
        current_step=current,
        active_step=current,
        next_action=next_action,
        steps=steps,
        classification_by_methodology=classification_by_methodology,
    )
