"""Orchestration of the existing pure-logic modules.

The orchestrator is the only place where ingestion, the rules engine,
manual review, calculation, and exports are stitched together. The
HTTP routes are deliberately thin — they parse requests, call into the
orchestrator, and serialise responses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
)
from altera_api.ai.classifier import (
    classify_pt as ai_classify_pt,
)
from altera_api.ai.classifier import (
    classify_wwf as ai_classify_wwf,
)
from altera_api.ai.provider import ClassifierProvider
from altera_api.api.state import RunRecord
from altera_api.calculation import (
    PTRunVersions,
    WWFRunVersions,
    calculate_pt_run,
    calculate_wwf_run,
)
from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.product import NormalizedProduct
from altera_api.domain.project import Project
from altera_api.domain.protein_tracker import (
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.domain.upload import Upload, UploadStatus
from altera_api.domain.validation import ValidationReport
from altera_api.domain.wwf import (
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)
from altera_api.exports import (
    ExportClassificationMeta,
    ExportProductMaster,
    PTExportContext,
    RunMetadata,
    WWFExportContext,
    render_pt_csv,
    render_pt_json,
    render_pt_markdown,
    render_wwf_csv,
    render_wwf_json,
    render_wwf_markdown,
)
from altera_api.ingestion import ingest_csv_bytes
from altera_api.ingestion.validators import compute_sha256
from altera_api.persistence.protocol import StoreProtocol
from altera_api.review.workflow import (
    accept_pt_item,
    accept_wwf_item,
    change_pt_item,
    change_wwf_item,
    claim_item,
    defer_item,
)
from altera_api.rules import (
    PTContradiction,
    PTMatched,
    PTPassThrough,
    PTRuleCollision,
    WWFContradiction,
    WWFMatched,
    WWFPassThrough,
    WWFRuleCollision,
    classify_protein_tracker,
    classify_wwf,
    load_rules_from_dir,
)

#: Module-level singleton — loading rules from disk is expensive enough
#: that we don't want to do it per request.
_RULE_SET = load_rules_from_dir()

PT_VERSIONS = PTRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="GPA & ProVeg Foodservice 2024-08",
    taxonomy_version="1.0.0",
    rules_version="0.1.0",
)
WWF_VERSIONS = WWFRunVersions(
    methodology_version="1.0.0",
    methodology_source_edition="WWF Food Practice 2024",
    taxonomy_version="1.0.0",
    rules_version="0.1.0",
)


@dataclass(frozen=True)
class IngestSummary:
    upload: Upload
    report: ValidationReport
    products_count: int
    dropped_columns: tuple[str, ...]
    duplicate_of: UUID | None = None


@dataclass(frozen=True)
class ClassifySummary:
    methodology: Methodology
    matched: int
    pass_through: int
    rule_collision: int
    queued_for_review: int
    # Contradiction detection (Phase 18) — products with contradicting
    # label/ingredient/category signals; bypass the AI classifier entirely.
    contradictions: int = 0
    # AI pipeline counts (all zero when AI is disabled)
    ai_attempted: int = 0
    ai_accepted: int = 0
    ai_review: int = 0
    ai_failed: int = 0


@dataclass(frozen=True)
class ReviewItemView:
    product_id: UUID
    external_product_id: str
    product_name: str
    brand: str | None
    methodology: Methodology
    status: ManualReviewStatus
    reason: ManualReviewQueueReason
    queued_at: datetime
    current_category: str | None
    # Enriched fields added in Phase 19A
    upload_id: UUID | None = None
    confidence: Decimal | None = None
    # TODO(phase-19b): ai_rationale — not stored on ManualReviewItem yet;
    # requires persisting the AI response alongside the review item.


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
def create_upload_stub(
    store: StoreProtocol,
    *,
    project: Project,
    upload_id: UUID,
    original_filename: str,
    storage_path: str,
    uploaded_by: UUID,
    content_type: str | None = None,
    file_size_bytes: int | None = None,
) -> Upload:
    """Create a placeholder Upload record for the storage-backed two-step flow.

    Called by ``prepare_upload_route`` after issuing a signed URL.
    The record is updated to the final status by ``ingest_upload`` when
    the client later calls the ingest endpoint.
    """
    now = datetime.now(UTC)
    upload = Upload(
        id=upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=storage_path,
        original_filename=original_filename,
        status=UploadStatus.UPLOAD_URL_CREATED,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        uploaded_by=uploaded_by,
        created_at=now,
    )
    store.add_upload(upload, product_ids=[])
    return upload


def ingest_upload(
    store: StoreProtocol,
    *,
    project: Project,
    file_bytes: bytes,
    original_filename: str,
    uploaded_by: UUID,
    upload_id: UUID | None = None,
    storage_path: str | None = None,
    content_type: str | None = None,
) -> IngestSummary:
    """Run the full ingestion pipeline on raw file bytes.

    Parameters
    ----------
    upload_id:
        If the caller pre-allocated an ID (storage-backed flow), pass it here
        so the existing stub record is updated. Otherwise a new UUID is minted.
    storage_path:
        The path already written to Supabase Storage, or ``None`` for the
        direct-upload (dev/multipart) flow where we use an in-memory sentinel.
    content_type:
        MIME type of the uploaded file, forwarded from the HTTP request.
    """
    now = datetime.now(UTC)
    the_upload_id = upload_id or uuid4()
    actual_path = storage_path or f"in_memory/{the_upload_id}"
    file_size_bytes = len(file_bytes)
    checksum = compute_sha256(file_bytes)

    # Duplicate detection — same checksum in the same project (warn, don't block)
    existing = store.find_upload_by_checksum(project.id, checksum)
    duplicate_of = (
        existing.id
        if existing is not None and existing.id != the_upload_id
        else None
    )

    # Run the parsing + validation + normalisation pipeline
    validation_start = datetime.now(UTC)
    result = ingest_csv_bytes(
        file_bytes,
        upload_id=the_upload_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        methodologies_enabled=project.methodologies_enabled,
        now=now,
    )
    validation_end = datetime.now(UTC)

    is_invalid = result.read_error is not None or result.report.is_blocking
    terminal_status = (
        UploadStatus.VALIDATION_FAILED
        if is_invalid
        else UploadStatus.READY_FOR_CLASSIFICATION
    )
    ingestion_start = None if is_invalid else validation_end
    ingestion_end = None if is_invalid else datetime.now(UTC)

    upload = Upload(
        id=the_upload_id,
        organisation_id=project.organisation_id,
        project_id=project.id,
        storage_path=actual_path,
        original_filename=original_filename,
        status=terminal_status,
        row_count=result.report.total_rows,
        dropped_columns=result.dropped_columns,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        checksum_sha256=checksum,
        uploaded_by=uploaded_by,
        created_at=now,
        validation_started_at=validation_start,
        validation_completed_at=validation_end,
        ingestion_started_at=ingestion_start,
        ingestion_completed_at=ingestion_end,
    )

    product_ids = [p.id for p in result.products]
    existing_rec = store.get_upload(the_upload_id)
    if existing_rec is not None:
        store.update_upload(upload, product_ids=product_ids)
    else:
        store.add_upload(upload, product_ids=product_ids)

    for product in result.products:
        store.add_product(product)

    store.set_upload_validation_report(
        the_upload_id, result.report, duplicate_of=duplicate_of
    )

    return IngestSummary(
        upload=upload,
        report=result.report,
        products_count=len(result.products),
        dropped_columns=result.dropped_columns,
        duplicate_of=duplicate_of,
    )


# ---------------------------------------------------------------------------
# Classification (rules engine only at this phase — AI orchestration
# is Phase 7 logic that needs a real provider configured)
# ---------------------------------------------------------------------------
def classify_upload(
    store: StoreProtocol,
    *,
    project: Project,
    upload_id: UUID,
    methodology: Methodology,
    ai_provider: ClassifierProvider | None = None,
) -> ClassifySummary:
    if methodology not in project.methodologies_enabled:
        raise ValueError(
            f"methodology {methodology.value} is not enabled on project {project.id}"
        )
    upload_record = store.get_upload(upload_id)
    if upload_record is None:
        raise LookupError("upload not found")

    now = datetime.now(UTC)
    matched = pass_through = collision = contradiction = queued = 0
    ai_attempted = ai_accepted = ai_review = ai_failed = 0

    for product_id in upload_record.product_ids:
        product = store.get_product(product_id)
        assert product is not None, f"product {product_id} missing"

        if methodology is Methodology.PROTEIN_TRACKER:
            verdict = classify_protein_tracker(product, _RULE_SET.pt, now=now)
            if isinstance(verdict, PTMatched):
                store.upsert_pt_classification(verdict.classification)
                store.remove_review_item(product.id, methodology)
                matched += 1
            elif isinstance(verdict, PTRuleCollision):
                _queue_unknown_pt(store, product, ManualReviewQueueReason.RULE_COLLISION, now)
                collision += 1
                queued += 1
            elif isinstance(verdict, PTContradiction):
                _queue_unknown_pt(
                    store, product, ManualReviewQueueReason.CONTRADICTION_DETECTED, now
                )
                contradiction += 1
                queued += 1
            elif isinstance(verdict, PTPassThrough):
                pass_through += 1
                if ai_provider is not None:
                    ai_attempted += 1
                    ai_v = ai_classify_pt(product, ai_provider, now=now)
                    if isinstance(ai_v, AIAccepted):
                        store.upsert_pt_classification(ai_v.classification)
                        store.remove_review_item(product.id, methodology)
                        ai_accepted += 1
                    elif isinstance(ai_v, AINeedsReviewLowConfidence):
                        store.upsert_pt_classification(ai_v.classification)
                        _enqueue_review_item(
                            store, product.id, methodology,
                            ManualReviewQueueReason.LOW_CONFIDENCE, now,
                        )
                        ai_review += 1
                        queued += 1
                    elif isinstance(ai_v, AINeedsReviewParseFailed):
                        _queue_unknown_pt(store, product, ManualReviewQueueReason.AI_PARSE_FAILED, now)
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                    elif isinstance(ai_v, AIProviderError):
                        _queue_unknown_pt(store, product, ManualReviewQueueReason.AI_PROVIDER_ERROR, now)
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                else:
                    _queue_unknown_pt(store, product, ManualReviewQueueReason.REQUESTED, now)
                    queued += 1
        else:
            verdict_w = classify_wwf(product, _RULE_SET.wwf, now=now)
            if isinstance(verdict_w, WWFMatched):
                store.upsert_wwf_classification(verdict_w.classification)
                store.remove_review_item(product.id, methodology)
                matched += 1
            elif isinstance(verdict_w, WWFRuleCollision):
                _queue_unknown_wwf(store, product, ManualReviewQueueReason.RULE_COLLISION, now)
                collision += 1
                queued += 1
            elif isinstance(verdict_w, WWFContradiction):
                _queue_unknown_wwf(
                    store, product, ManualReviewQueueReason.CONTRADICTION_DETECTED, now
                )
                contradiction += 1
                queued += 1
            elif isinstance(verdict_w, WWFPassThrough):
                pass_through += 1
                if ai_provider is not None:
                    ai_attempted += 1
                    ai_v_w = ai_classify_wwf(product, ai_provider, now=now)
                    if isinstance(ai_v_w, AIAccepted):
                        store.upsert_wwf_classification(ai_v_w.classification)
                        store.remove_review_item(product.id, methodology)
                        ai_accepted += 1
                    elif isinstance(ai_v_w, AINeedsReviewLowConfidence):
                        store.upsert_wwf_classification(ai_v_w.classification)
                        _enqueue_review_item(
                            store, product.id, methodology,
                            ManualReviewQueueReason.LOW_CONFIDENCE, now,
                        )
                        ai_review += 1
                        queued += 1
                    elif isinstance(ai_v_w, AINeedsReviewParseFailed):
                        _queue_unknown_wwf(store, product, ManualReviewQueueReason.AI_PARSE_FAILED, now)
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                    elif isinstance(ai_v_w, AIProviderError):
                        _queue_unknown_wwf(store, product, ManualReviewQueueReason.AI_PROVIDER_ERROR, now)
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                else:
                    _queue_unknown_wwf(store, product, ManualReviewQueueReason.REQUESTED, now)
                    queued += 1

    return ClassifySummary(
        methodology=methodology,
        matched=matched,
        pass_through=pass_through,
        rule_collision=collision,
        contradictions=contradiction,
        queued_for_review=queued,
        ai_attempted=ai_attempted,
        ai_accepted=ai_accepted,
        ai_review=ai_review,
        ai_failed=ai_failed,
    )


def _queue_unknown_pt(
    store: StoreProtocol,
    product: NormalizedProduct,
    reason: ManualReviewQueueReason,
    now: datetime,
) -> None:
    # Place a provisional `unknown` classification so the reviewer has
    # something to start from. The reviewer can change it.
    if Methodology.PROTEIN_TRACKER not in product.methodologies_enabled:
        return
    store.upsert_pt_classification(
        ProteinTrackerProductClassification(
            product_id=product.id,
            pt_group=ProteinTrackerGroup.UNKNOWN,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="pt.system.unknown",
            updated_at=now,
        )
    )
    store.upsert_review_item(
        ManualReviewItem(
            product_id=product.id,
            methodology=Methodology.PROTEIN_TRACKER,
            status=ManualReviewStatus.IN_QUEUE,
            reason=reason,
            queued_at=now,
        )
    )


def _queue_unknown_wwf(
    store: StoreProtocol,
    product: NormalizedProduct,
    reason: ManualReviewQueueReason,
    now: datetime,
) -> None:
    if Methodology.WWF not in product.methodologies_enabled:
        return
    store.upsert_wwf_classification(
        WWFProductClassification(
            product_id=product.id,
            wwf_food_group=WWFFoodGroup.UNKNOWN,
            wwf_is_composite=False,
            source=ClassificationSource.DETERMINISTIC,
            confidence=Decimal("1"),
            rule_id="wwf.system.unknown",
            updated_at=now,
        )
    )
    store.upsert_review_item(
        ManualReviewItem(
            product_id=product.id,
            methodology=Methodology.WWF,
            status=ManualReviewStatus.IN_QUEUE,
            reason=reason,
            queued_at=now,
        )
    )


def _enqueue_review_item(
    store: StoreProtocol,
    product_id: UUID,
    methodology: Methodology,
    reason: ManualReviewQueueReason,
    now: datetime,
) -> None:
    """Queue a review item without overwriting the existing classification.

    Used when the AI returned a valid-but-low-confidence verdict: the
    AI classification is already stored and the reviewer should see it,
    not a reset-to-unknown placeholder.
    """
    store.upsert_review_item(
        ManualReviewItem(
            product_id=product_id,
            methodology=methodology,
            status=ManualReviewStatus.IN_QUEUE,
            reason=reason,
            queued_at=now,
        )
    )


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
def list_review(
    store: StoreProtocol,
    *,
    project: Project,
    methodology: Methodology | None = None,
    status: ManualReviewStatus | None = None,
    reason: ManualReviewQueueReason | None = None,
    upload_id: UUID | None = None,
    product_search: str | None = None,
    oldest_first: bool = True,
) -> list[ReviewItemView]:
    """Return review items for a project with optional filtering and sorting.

    Filters applied in order:
    1. methodology / status / reason — via filter_queue (pure, no DB join needed)
    2. upload_id — requires the product record
    3. product_search — case-insensitive substring match on product_name and
       external_product_id; requires the product record

    Sorting: oldest_first=True (default) sorts by queued_at ascending.
    confidence-based sorting is a TODO — confidence lives on the
    classification record, not the review item, so it requires either a
    join or a denormalised field on ManualReviewItem.
    """
    from altera_api.review.queue import filter_queue, sort_queue_by_age

    raw_items = store.list_review_items_for_project(project.id, methodology=methodology)
    filtered = filter_queue(raw_items, status=status, reason=reason)
    sorted_items = sort_queue_by_age(filtered, oldest_first=oldest_first)

    search_lower = product_search.lower().strip() if product_search else None

    out: list[ReviewItemView] = []
    for item in sorted_items:
        product = store.get_product(item.product_id)
        assert product is not None

        # upload_id filter
        if upload_id is not None and product.upload_id != upload_id:
            continue

        # product_search filter — substring match on name or external ID
        if search_lower:
            name_match = search_lower in product.product_name.lower()
            id_match = search_lower in product.external_product_id.lower()
            if not (name_match or id_match):
                continue

        current_category: str | None = None
        confidence: Decimal | None = None
        if item.methodology is Methodology.PROTEIN_TRACKER:
            c = store.get_pt_classification(item.product_id)
            current_category = c.pt_group.value if c else None
            confidence = c.confidence if c else None
        else:
            c = store.get_wwf_classification(item.product_id)
            current_category = c.wwf_food_group.value if c else None
            confidence = c.confidence if c else None

        out.append(
            ReviewItemView(
                product_id=product.id,
                external_product_id=product.external_product_id,
                product_name=product.product_name,
                brand=product.brand,
                methodology=item.methodology,
                status=item.status,
                reason=item.reason,
                queued_at=item.queued_at,
                current_category=current_category,
                upload_id=product.upload_id,
                confidence=confidence,
            )
        )
    return out


def submit_decision(
    store: StoreProtocol,
    *,
    product_id: UUID,
    methodology: Methodology,
    decision: Literal["accepted", "changed", "deferred"],
    reviewer_user_id: UUID,
    to_category: str | None = None,
    reason: str | None = None,
) -> ReviewItemView:
    item = store.get_review_item(product_id, methodology)
    if item is None:
        raise LookupError("review item not found")
    now = datetime.now(UTC)
    claimed = claim_item(item, reviewer_user_id=reviewer_user_id, now=now)

    if methodology is Methodology.PROTEIN_TRACKER:
        current = store.get_pt_classification(product_id)
        if decision == "accepted":
            assert current is not None
            outcome = accept_pt_item(
                claimed,
                current=current,
                reviewer_user_id=reviewer_user_id,
                reason=reason,
                now=now,
            )
            store.upsert_pt_classification(outcome.pt_classification)  # type: ignore[arg-type]
        elif decision == "changed":
            if to_category is None:
                raise ValueError("to_category required for decision=changed")
            outcome = change_pt_item(
                claimed,
                current=current,
                to_group=ProteinTrackerGroup(to_category),
                reviewer_user_id=reviewer_user_id,
                reason=reason,
                now=now,
            )
            store.upsert_pt_classification(outcome.pt_classification)  # type: ignore[arg-type]
        else:
            outcome = defer_item(
                claimed, reviewer_user_id=reviewer_user_id, reason=reason, now=now
            )
    else:
        current_w = store.get_wwf_classification(product_id)
        if decision == "accepted":
            assert current_w is not None
            outcome = accept_wwf_item(
                claimed,
                current=current_w,
                reviewer_user_id=reviewer_user_id,
                reason=reason,
                now=now,
            )
            store.upsert_wwf_classification(outcome.wwf_classification)  # type: ignore[arg-type]
        elif decision == "changed":
            if to_category is None:
                raise ValueError("to_category required for decision=changed")
            target = _build_wwf_target(product_id, to_category, now=now)
            outcome = change_wwf_item(
                claimed,
                current=current_w,
                target=target,
                reviewer_user_id=reviewer_user_id,
                reason=reason,
                now=now,
            )
            store.upsert_wwf_classification(outcome.wwf_classification)  # type: ignore[arg-type]
        else:
            outcome = defer_item(
                claimed, reviewer_user_id=reviewer_user_id, reason=reason, now=now
            )

    if outcome.item.status is ManualReviewStatus.DEFERRED:
        store.upsert_review_item(outcome.item)
    else:
        store.remove_review_item(product_id, methodology)

    return _review_view_for(store, product_id=product_id, methodology=methodology)


def _review_view_for(
    store: StoreProtocol,
    *,
    product_id: UUID,
    methodology: Methodology,
) -> ReviewItemView:
    product = store.get_product(product_id)
    assert product is not None
    item = store.get_review_item(product_id, methodology)
    confidence: Decimal | None = None
    if methodology is Methodology.PROTEIN_TRACKER:
        c = store.get_pt_classification(product_id)
        category = c.pt_group.value if c else None
        confidence = c.confidence if c else None
    else:
        c = store.get_wwf_classification(product_id)
        category = c.wwf_food_group.value if c else None
        confidence = c.confidence if c else None
    return ReviewItemView(
        product_id=product.id,
        external_product_id=product.external_product_id,
        product_name=product.product_name,
        brand=product.brand,
        methodology=methodology,
        status=item.status if item else ManualReviewStatus.ACCEPTED,
        reason=item.reason if item else ManualReviewQueueReason.REQUESTED,
        queued_at=item.queued_at if item else datetime.now(UTC),
        current_category=category,
        upload_id=product.upload_id,
        confidence=confidence,
    )


def _build_wwf_target(
    product_id: UUID, food_group_str: str, *, now: datetime
) -> WWFProductClassification:
    """Build a WWF classification target from a simple food-group string.

    The reviewer UI sends a food-group value; for FG1..FG7 we pick the
    most generic subgroup so the cross-field validator accepts the
    payload. The reviewer can refine the subgroup later via a richer UI.
    """
    fg = WWFFoodGroup(food_group_str)
    defaults: dict = {
        WWFFoodGroup.FG1: {"fg1_subgroup": WWFFG1Subgroup.LEGUMES},
        WWFFoodGroup.FG2: {"fg2_subgroup": WWFFG2Subgroup.OTHER_DAIRY_ANIMAL},
        WWFFoodGroup.FG3: {"fg3_subgroup": WWFFG3Subgroup.PLANT_BASED_FAT},
        WWFFoodGroup.FG4: {},
        WWFFoodGroup.FG5: {"fg5_grain_kind": WWFFG5GrainKind.WHOLE_GRAIN},
        WWFFoodGroup.FG6: {},
        WWFFoodGroup.FG7: {"fg7_snack_kind": WWFFG7SnackKind.PLANT_BASED_SNACK},
    }
    return WWFProductClassification(
        product_id=product_id,
        wwf_food_group=fg,
        wwf_is_composite=False,
        source=ClassificationSource.MANUAL_REVIEW,
        confidence=Decimal("1"),
        updated_at=now,
        **defaults[fg],
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------
def run_calculation(
    store: StoreProtocol,
    *,
    project: Project,
    methodology: Methodology,
    triggered_by: UUID,
) -> RunRecord:
    if methodology not in project.methodologies_enabled:
        raise ValueError(
            f"methodology {methodology.value} is not enabled on project {project.id}"
        )
    products = store.list_products_for_project(project.id)
    started_at = datetime.now(UTC)

    if methodology is Methodology.PROTEIN_TRACKER:
        classifications = {
            p.id: c
            for p in products
            if (c := store.get_pt_classification(p.id)) is not None
        }
        pt_result = calculate_pt_run(
            products,
            classifications,
            run_id=uuid4(),
            reporting_period_label=project.reporting_period_label,
            versions=PT_VERSIONS,
        )
        rows_payload = [r.model_dump() for r in pt_result.rows]
        summary_payload = pt_result.summary.model_dump()
        run_id = pt_result.summary.run_id
        rows_count = len(pt_result.rows)
    else:
        classifications_w = {
            p.id: c
            for p in products
            if (c := store.get_wwf_classification(p.id)) is not None
        }
        wwf_ingredients = store.get_wwf_ingredients_by_project(project.id)
        wwf_result = calculate_wwf_run(
            products,
            classifications_w,
            run_id=uuid4(),
            reporting_period_label=project.reporting_period_label,
            versions=WWF_VERSIONS,
            ingredients_by_product=wwf_ingredients or None,
        )
        rows_payload = [r.model_dump() for r in wwf_result.rows]
        summary_payload = wwf_result.summary.model_dump()
        run_id = wwf_result.summary.run_id
        rows_count = len(wwf_result.rows)

    record = RunRecord(
        id=run_id,
        project_id=project.id,
        organisation_id=project.organisation_id,
        methodology=methodology,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        triggered_by=triggered_by,
        rows_payload=rows_payload,
        summary_payload=summary_payload,
        rows_count=rows_count,
    )
    store.add_run(record)
    return record


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def render_export(
    store: StoreProtocol,
    *,
    project: Project,
    run_id: UUID,
    fmt: Literal["csv", "json", "md"],
) -> tuple[bytes, str, str]:
    """Re-hydrate the persisted run + render to ``fmt``.

    Returns ``(payload_bytes, media_type, filename)``.
    """
    record = store.get_run(run_id)
    if record is None or record.project_id != project.id:
        raise LookupError("run not found")

    from datetime import date as _date

    from altera_api.domain.protein_tracker import (
        ProteinTrackerCalculationRow,
        ProteinTrackerCalculationSummary,
    )
    from altera_api.domain.wwf import WWFCalculationRow, WWFCalculationSummary
    from altera_api.exports.common import export_filename

    products = store.list_products_for_project(project.id)
    product_master = {
        p.id: ExportProductMaster(
            product_id=p.id,
            external_product_id=p.external_product_id,
            product_name=p.product_name,
            brand=p.brand,
            is_own_brand=p.is_own_brand,
            retail_channel=p.wwf_fields.retail_channel if p.wwf_fields else None,
        )
        for p in products
    }
    run_meta = RunMetadata(
        run_id=record.id,
        project_slug=project.name.lower().replace(" ", "-"),
        started_at=record.started_at,
        finished_at=record.finished_at,
        triggered_by=record.triggered_by,
    )

    if record.methodology is Methodology.PROTEIN_TRACKER:
        summary = ProteinTrackerCalculationSummary.model_validate(record.summary_payload)
        rows = tuple(
            ProteinTrackerCalculationRow.model_validate(r) for r in record.rows_payload
        )
        classifications_meta = {
            p.id: _classification_meta_for_pt(c)
            for p in products
            if (c := store.get_pt_classification(p.id)) is not None
        }
        ctx = PTExportContext(
            run=run_meta,
            summary=summary,
            rows=rows,
            products=product_master,
            classifications=classifications_meta,
            pt_validation_status=project.pt_validation_status,
            protein_sources={
                p.id: p.pt_fields.protein_source for p in products if p.pt_fields
            },
            items_purchased={
                p.id: p.pt_fields.items_purchased for p in products if p.pt_fields
            },
            weights_per_item={p.id: p.weight_per_item_kg for p in products},
        )
        if fmt == "csv":
            return (
                render_pt_csv(ctx),
                "text/csv; charset=utf-8",
                export_filename(
                    project_slug=run_meta.project_slug,
                    methodology=record.methodology,
                    run_id=record.id,
                    fmt="csv",
                    today=_date.today(),
                ),
            )
        if fmt == "json":
            return (
                render_pt_json(ctx).encode("utf-8"),
                "application/json",
                export_filename(
                    project_slug=run_meta.project_slug,
                    methodology=record.methodology,
                    run_id=record.id,
                    fmt="json",
                    today=_date.today(),
                ),
            )
        return (
            render_pt_markdown(ctx).encode("utf-8"),
            "text/markdown",
            export_filename(
                project_slug=run_meta.project_slug,
                methodology=record.methodology,
                run_id=record.id,
                fmt="md",
                today=_date.today(),
            ),
        )

    # WWF
    summary_w = WWFCalculationSummary.model_validate(record.summary_payload)
    rows_w = tuple(WWFCalculationRow.model_validate(r) for r in record.rows_payload)
    classifications_meta_w = {
        p.id: _classification_meta_for_wwf(c)
        for p in products
        if (c := store.get_wwf_classification(p.id)) is not None
    }
    wwf_ingredients = store.get_wwf_ingredients_by_project(project.id)
    ctx_w = WWFExportContext(
        run=run_meta,
        summary=summary_w,
        rows=rows_w,
        products=product_master,
        classifications=classifications_meta_w,
        items_sold={p.id: p.wwf_fields.items_sold for p in products if p.wwf_fields},
        weights_per_item={p.id: p.weight_per_item_kg for p in products},
        ingredients_by_product=wwf_ingredients or None,
    )
    if fmt == "csv":
        return (
            render_wwf_csv(ctx_w),
            "text/csv; charset=utf-8",
            export_filename(
                project_slug=run_meta.project_slug,
                methodology=record.methodology,
                run_id=record.id,
                fmt="csv",
                today=_date.today(),
            ),
        )
    if fmt == "json":
        return (
            render_wwf_json(ctx_w).encode("utf-8"),
            "application/json",
            export_filename(
                project_slug=run_meta.project_slug,
                methodology=record.methodology,
                run_id=record.id,
                fmt="json",
                today=_date.today(),
            ),
        )
    return (
        render_wwf_markdown(ctx_w).encode("utf-8"),
        "text/markdown",
        export_filename(
            project_slug=run_meta.project_slug,
            methodology=record.methodology,
            run_id=record.id,
            fmt="md",
            today=_date.today(),
        ),
    )


def _classification_meta_for_pt(
    c: ProteinTrackerProductClassification,
) -> ExportClassificationMeta:
    return ExportClassificationMeta(
        source=c.source,
        confidence=c.confidence,
        rule_id=c.rule_id,
        ai_model=c.ai_model,
        reviewer_user_id=c.reviewer_user_id,
    )


def _classification_meta_for_wwf(
    c: WWFProductClassification,
) -> ExportClassificationMeta:
    return ExportClassificationMeta(
        source=c.source,
        confidence=c.confidence,
        rule_id=c.rule_id,
        ai_model=c.ai_model,
        reviewer_user_id=c.reviewer_user_id,
    )
