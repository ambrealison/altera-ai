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
    ManualReviewPriority,
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
    # Phase 19A
    upload_id: UUID | None = None
    confidence: Decimal | None = None
    # Phase 19B — safe classification metadata (no commercial fields)
    source: ClassificationSource | None = None
    rule_id: str | None = None
    ai_model: str | None = None
    ai_prompt_version: str | None = None
    rationale_notes: tuple[str, ...] = ()
    # Phase 19D — lock and assignment fields
    locked_by_user_id: UUID | None = None
    locked_by_email: str | None = None
    locked_at: datetime | None = None
    lock_expires_at: datetime | None = None
    lock_status: str = "unlocked"  # unlocked | locked_by_me | locked_by_other | expired
    assigned_to_user_id: UUID | None = None
    assigned_to_email: str | None = None
    # Phase 19E — priority
    priority_level: str = "low"  # low | medium | high | critical
    priority_reasons: tuple[str, ...] = ()


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
    duplicate_of = existing.id if existing is not None and existing.id != the_upload_id else None

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
        UploadStatus.VALIDATION_FAILED if is_invalid else UploadStatus.READY_FOR_CLASSIFICATION
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

    store.set_upload_validation_report(the_upload_id, result.report, duplicate_of=duplicate_of)

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
        raise ValueError(f"methodology {methodology.value} is not enabled on project {project.id}")
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
                _queue_unknown_pt(
                    store,
                    product,
                    ManualReviewQueueReason.RULE_COLLISION,
                    now,
                    notes=verdict.conflicting_rule_ids,
                )
                collision += 1
                queued += 1
            elif isinstance(verdict, PTContradiction):
                _queue_unknown_pt(
                    store,
                    product,
                    ManualReviewQueueReason.CONTRADICTION_DETECTED,
                    now,
                    notes=verdict.contradiction_notes,
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
                            store,
                            product.id,
                            methodology,
                            ManualReviewQueueReason.LOW_CONFIDENCE,
                            now,
                        )
                        ai_review += 1
                        queued += 1
                    elif isinstance(ai_v, AINeedsReviewParseFailed):
                        _queue_unknown_pt(
                            store, product, ManualReviewQueueReason.AI_PARSE_FAILED, now
                        )
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                    elif isinstance(ai_v, AIProviderError):
                        _queue_unknown_pt(
                            store, product, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
                        )
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
                _queue_unknown_wwf(
                    store,
                    product,
                    ManualReviewQueueReason.RULE_COLLISION,
                    now,
                    notes=verdict_w.conflicting_rule_ids,
                )
                collision += 1
                queued += 1
            elif isinstance(verdict_w, WWFContradiction):
                _queue_unknown_wwf(
                    store,
                    product,
                    ManualReviewQueueReason.CONTRADICTION_DETECTED,
                    now,
                    notes=verdict_w.contradiction_notes,
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
                            store,
                            product.id,
                            methodology,
                            ManualReviewQueueReason.LOW_CONFIDENCE,
                            now,
                        )
                        ai_review += 1
                        queued += 1
                    elif isinstance(ai_v_w, AINeedsReviewParseFailed):
                        _queue_unknown_wwf(
                            store, product, ManualReviewQueueReason.AI_PARSE_FAILED, now
                        )
                        ai_review += 1
                        ai_failed += 1
                        queued += 1
                    elif isinstance(ai_v_w, AIProviderError):
                        _queue_unknown_wwf(
                            store, product, ManualReviewQueueReason.AI_PROVIDER_ERROR, now
                        )
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
    notes: tuple[str, ...] = (),
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
            rationale_notes=notes,
        )
    )


def _queue_unknown_wwf(
    store: StoreProtocol,
    product: NormalizedProduct,
    reason: ManualReviewQueueReason,
    now: datetime,
    notes: tuple[str, ...] = (),
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
            rationale_notes=notes,
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
    priority_level: ManualReviewPriority | None = None,
    upload_id: UUID | None = None,
    product_search: str | None = None,
    sort: str = "oldest",  # oldest | newest | priority
    viewer_user_id: UUID | None = None,
) -> list[ReviewItemView]:
    """Return review items for a project with optional filtering and sorting.

    Filters applied in order:
    1. methodology / status / reason / priority_level — pure, no DB join
    2. upload_id — requires the product record
    3. product_search — case-insensitive substring on name or external ID

    Sort options: oldest (default) | newest | priority (critical first).
    """
    from altera_api.review.priority import assign_priority
    from altera_api.review.queue import (
        filter_by_priority,
        filter_queue,
        sort_by_priority,
        sort_queue_by_age,
    )

    now = datetime.now(UTC)
    raw_items = store.list_review_items_for_project(project.id, methodology=methodology)
    filtered = filter_queue(raw_items, status=status, reason=reason)
    if priority_level is not None:
        filtered = filter_by_priority(filtered, priority=priority_level)

    if sort == "newest":
        sorted_items = sort_queue_by_age(filtered, oldest_first=False)
    elif sort == "priority":
        sorted_items = sort_by_priority(filtered, highest_first=True)
    else:
        sorted_items = sort_queue_by_age(filtered, oldest_first=True)

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
        source: ClassificationSource | None = None
        rule_id: str | None = None
        ai_model: str | None = None
        ai_prompt_version: str | None = None
        if item.methodology is Methodology.PROTEIN_TRACKER:
            c = store.get_pt_classification(item.product_id)
            if c:
                current_category = c.pt_group.value
                confidence = c.confidence
                source = c.source
                rule_id = c.rule_id
                ai_model = c.ai_model
                ai_prompt_version = c.ai_prompt_version
        else:
            c = store.get_wwf_classification(item.product_id)
            if c:
                current_category = c.wwf_food_group.value
                confidence = c.confidence
                source = c.source
                rule_id = c.rule_id
                ai_model = c.ai_model
                ai_prompt_version = c.ai_prompt_version

        prio, prio_reasons = assign_priority(item.reason)
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
                source=source,
                rule_id=rule_id,
                ai_model=ai_model,
                ai_prompt_version=ai_prompt_version,
                rationale_notes=item.rationale_notes,
                priority_level=prio.value,
                priority_reasons=prio_reasons,
                **_lock_fields(item, store, viewer_user_id, now),
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
            outcome = defer_item(claimed, reviewer_user_id=reviewer_user_id, reason=reason, now=now)
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
            outcome = defer_item(claimed, reviewer_user_id=reviewer_user_id, reason=reason, now=now)

    if outcome.item.status is ManualReviewStatus.DEFERRED:
        store.upsert_review_item(outcome.item)
    else:
        store.remove_review_item(product_id, methodology)

    return _review_view_for(
        store,
        product_id=product_id,
        methodology=methodology,
        viewer_user_id=reviewer_user_id,
    )


def _review_view_for(
    store: StoreProtocol,
    *,
    product_id: UUID,
    methodology: Methodology,
    viewer_user_id: UUID | None = None,
) -> ReviewItemView:
    product = store.get_product(product_id)
    assert product is not None
    item = store.get_review_item(product_id, methodology)
    confidence: Decimal | None = None
    source: ClassificationSource | None = None
    rule_id: str | None = None
    ai_model: str | None = None
    ai_prompt_version: str | None = None
    if methodology is Methodology.PROTEIN_TRACKER:
        c = store.get_pt_classification(product_id)
        if c:
            category: str | None = c.pt_group.value
            confidence = c.confidence
            source = c.source
            rule_id = c.rule_id
            ai_model = c.ai_model
            ai_prompt_version = c.ai_prompt_version
        else:
            category = None
    else:
        c = store.get_wwf_classification(product_id)
        if c:
            category = c.wwf_food_group.value
            confidence = c.confidence
            source = c.source
            rule_id = c.rule_id
            ai_model = c.ai_model
            ai_prompt_version = c.ai_prompt_version
        else:
            category = None
    _now = datetime.now(UTC)
    lock_kw = (
        _lock_fields(item, store, viewer_user_id, _now)
        if item
        else {
            "locked_by_user_id": None,
            "locked_by_email": None,
            "locked_at": None,
            "lock_expires_at": None,
            "lock_status": "unlocked",
            "assigned_to_user_id": None,
            "assigned_to_email": None,
        }
    )
    return ReviewItemView(
        product_id=product.id,
        external_product_id=product.external_product_id,
        product_name=product.product_name,
        brand=product.brand,
        methodology=methodology,
        status=item.status if item else ManualReviewStatus.ACCEPTED,
        reason=item.reason if item else ManualReviewQueueReason.REQUESTED,
        queued_at=item.queued_at if item else _now,
        current_category=category,
        upload_id=product.upload_id,
        confidence=confidence,
        source=source,
        rule_id=rule_id,
        ai_model=ai_model,
        ai_prompt_version=ai_prompt_version,
        rationale_notes=item.rationale_notes if item else (),
        **_priority_fields(item),
        **lock_kw,
    )


# ---------------------------------------------------------------------------
# Lock management and assignment
# ---------------------------------------------------------------------------
def claim_review_item(
    store: StoreProtocol,
    *,
    project: Project,
    product_id: UUID,
    methodology: Methodology,
    reviewer_user_id: UUID,
) -> ReviewItemView:
    from altera_api.review.errors import SoftLockHeldError
    from altera_api.review.workflow import claim_item

    item = store.get_review_item(product_id, methodology)
    if item is None:
        raise LookupError("review item not found")
    now = datetime.now(UTC)
    try:
        updated = claim_item(item, reviewer_user_id=reviewer_user_id, now=now)
    except SoftLockHeldError as exc:
        raise ValueError(str(exc)) from exc
    store.upsert_review_item(updated)
    return _review_view_for(
        store,
        product_id=product_id,
        methodology=methodology,
        viewer_user_id=reviewer_user_id,
    )


def release_review_item(
    store: StoreProtocol,
    *,
    project: Project,
    product_id: UUID,
    methodology: Methodology,
    reviewer_user_id: UUID,
) -> ReviewItemView:
    from altera_api.review.errors import SoftLockHeldError
    from altera_api.review.workflow import release_item

    item = store.get_review_item(product_id, methodology)
    if item is None:
        raise LookupError("review item not found")
    now = datetime.now(UTC)
    try:
        updated = release_item(item, reviewer_user_id=reviewer_user_id, now=now)
    except SoftLockHeldError as exc:
        raise ValueError(str(exc)) from exc
    store.upsert_review_item(updated)
    return _review_view_for(
        store,
        product_id=product_id,
        methodology=methodology,
        viewer_user_id=reviewer_user_id,
    )


def refresh_review_lock(
    store: StoreProtocol,
    *,
    project: Project,
    product_id: UUID,
    methodology: Methodology,
    reviewer_user_id: UUID,
) -> ReviewItemView:
    from altera_api.review.errors import SoftLockHeldError
    from altera_api.review.workflow import refresh_lock

    item = store.get_review_item(product_id, methodology)
    if item is None:
        raise LookupError("review item not found")
    now = datetime.now(UTC)
    try:
        updated = refresh_lock(item, reviewer_user_id=reviewer_user_id, now=now)
    except SoftLockHeldError as exc:
        raise ValueError(str(exc)) from exc
    store.upsert_review_item(updated)
    return _review_view_for(
        store,
        product_id=product_id,
        methodology=methodology,
        viewer_user_id=reviewer_user_id,
    )


def assign_review_item(
    store: StoreProtocol,
    *,
    project: Project,
    product_id: UUID,
    methodology: Methodology,
    assigner_user_id: UUID,
    assign_to_user_id: UUID,
    auth_can_assign_others: bool,
) -> ReviewItemView:
    """Assign a review item to a reviewer.

    Altera admins / methodology leads may assign to any user.
    Regular Altera reviewers may only assign to themselves.
    """
    if not auth_can_assign_others and assign_to_user_id != assigner_user_id:
        raise ValueError(
            "only Altera admins and methodology leads can assign items to "
            "other reviewers; use your own user_id to assign to yourself."
        )
    item = store.get_review_item(product_id, methodology)
    if item is None:
        raise LookupError("review item not found")
    if item.status.is_terminal:
        raise ValueError("cannot assign a terminal review item.")
    updated = item.model_copy(update={"assigned_to_user_id": assign_to_user_id})
    store.upsert_review_item(updated)
    return _review_view_for(
        store,
        product_id=product_id,
        methodology=methodology,
        viewer_user_id=assigner_user_id,
    )


def _priority_fields(item: ManualReviewItem | None) -> dict:
    from altera_api.review.priority import assign_priority

    if item is None:
        return {"priority_level": "low", "priority_reasons": ()}
    prio, reasons = assign_priority(item.reason)
    return {"priority_level": prio.value, "priority_reasons": reasons}


def _lock_status(
    item: ManualReviewItem,
    viewer_user_id: UUID | None,
    now: datetime,
) -> str:
    from altera_api.review.locks import is_lock_expired

    if item.soft_lock_user_id is None:
        return "unlocked"
    if is_lock_expired(item, now=now):
        return "expired"
    if viewer_user_id is not None and item.soft_lock_user_id == viewer_user_id:
        return "locked_by_me"
    return "locked_by_other"


def _lock_fields(
    item: ManualReviewItem,
    store: StoreProtocol,
    viewer_user_id: UUID | None,
    now: datetime,
) -> dict:
    from altera_api.review.locks import SOFT_LOCK_DURATION

    locked_by_email: str | None = None
    locked_at: datetime | None = None
    if item.soft_lock_user_id is not None:
        profile = store.get_user(item.soft_lock_user_id)
        locked_by_email = profile.email if profile else None
        if item.soft_lock_expires_at is not None:
            locked_at = item.soft_lock_expires_at - SOFT_LOCK_DURATION

    assigned_email: str | None = None
    if item.assigned_to_user_id is not None:
        profile = store.get_user(item.assigned_to_user_id)
        assigned_email = profile.email if profile else None

    return {
        "locked_by_user_id": item.soft_lock_user_id,
        "locked_by_email": locked_by_email,
        "locked_at": locked_at,
        "lock_expires_at": item.soft_lock_expires_at,
        "lock_status": _lock_status(item, viewer_user_id, now),
        "assigned_to_user_id": item.assigned_to_user_id,
        "assigned_to_email": assigned_email,
    }


BULK_ACTION_MAX_ITEMS = 100


@dataclass(frozen=True)
class BulkActionResult:
    action: str
    requested_count: int
    updated_count: int
    decision_ids: tuple[UUID, ...]


def bulk_submit_decision(
    store: StoreProtocol,
    *,
    project: Project,
    product_ids: list[UUID],
    methodology: Methodology,
    action: Literal["bulk_accept", "bulk_defer", "bulk_change_pt_group"],
    reviewer_user_id: UUID,
    to_pt_group: str | None = None,
    reason: str | None = None,
) -> BulkActionResult:
    """Apply one action to multiple review items atomically.

    Validates the entire batch before touching any state. Raises
    ``ValueError`` for any validation failure so the caller returns 400
    without partial updates.

    Supported actions
    -----------------
    bulk_accept          Both PT and WWF — accepts the current classification.
    bulk_defer           Both PT and WWF — defers without changing classification.
    bulk_change_pt_group PT only — reassigns all items to ``to_pt_group``.
                         WWF bulk change is not supported (requires a fully
                         validated classification object per item); use single-
                         item ``submit_decision`` instead.
    """
    from altera_api.domain.audit import AuditEvent, AuditEventType
    from altera_api.review.workflow import (
        accept_pt_item,
        accept_wwf_item,
        change_pt_item,
        claim_item,
        defer_item,
    )

    # --- batch-size guard ---
    if len(product_ids) == 0:
        raise ValueError("product_ids must not be empty.")
    if len(product_ids) > BULK_ACTION_MAX_ITEMS:
        raise ValueError(
            f"batch size {len(product_ids)} exceeds the maximum of {BULK_ACTION_MAX_ITEMS}."
        )

    # --- methodology-action compatibility ---
    if action == "bulk_change_pt_group":
        if methodology is not Methodology.PROTEIN_TRACKER:
            raise ValueError(
                "bulk_change_pt_group is only supported for protein_tracker methodology."
            )
        if to_pt_group is None:
            raise ValueError("to_pt_group is required for bulk_change_pt_group.")
        try:
            target_pt_group = ProteinTrackerGroup(to_pt_group)
        except ValueError:
            raise ValueError(f"invalid PT group: {to_pt_group!r}.") from None
        if not target_pt_group.is_methodology_group:
            raise ValueError(
                "bulk_change_pt_group cannot set system states (out_of_scope/unknown)."
            )

    # --- validate all items exist and are submittable ---
    now = datetime.now(UTC)
    items: list[ManualReviewItem] = []
    missing: list[UUID] = []
    wrong_methodology: list[UUID] = []
    terminal_ids: list[UUID] = []

    for pid in product_ids:
        product = store.get_product(pid)
        if product is None or product.project_id != project.id:
            missing.append(pid)
            continue
        item = store.get_review_item(pid, methodology)
        if item is None:
            missing.append(pid)
            continue
        if item.methodology is not methodology:
            wrong_methodology.append(pid)
            continue
        if item.status.is_terminal:
            terminal_ids.append(pid)
            continue
        items.append(item)

    # Check for items locked by another active reviewer
    from altera_api.review.locks import is_lock_held_by_other

    locked_by_other_ids: list[UUID] = [
        item.product_id
        for item in items
        if is_lock_held_by_other(item, reviewer_user_id=reviewer_user_id, now=now)
    ]

    errors: list[str] = []
    if missing:
        errors.append(
            f"{len(missing)} product(s) not found in this project's review queue "
            f"for methodology {methodology.value}: "
            + ", ".join(str(p) for p in missing[:5])
            + ("…" if len(missing) > 5 else "")
        )
    if wrong_methodology:
        errors.append(f"{len(wrong_methodology)} item(s) have wrong methodology.")
    if terminal_ids:
        errors.append(
            f"{len(terminal_ids)} item(s) are already in a terminal state "
            f"(accepted/changed/deferred) and cannot be actioned again."
        )
    if locked_by_other_ids:
        errors.append(
            f"{len(locked_by_other_ids)} item(s) are actively locked by another "
            "reviewer and cannot be bulk-actioned."
        )
    if errors:
        raise ValueError("; ".join(errors))

    # --- apply all ---
    decision_ids: list[UUID] = []
    for item in items:
        claimed = claim_item(item, reviewer_user_id=reviewer_user_id, now=now)

        if action == "bulk_accept":
            if methodology is Methodology.PROTEIN_TRACKER:
                current_pt = store.get_pt_classification(item.product_id)
                assert current_pt is not None
                outcome = accept_pt_item(
                    claimed,
                    current=current_pt,
                    reviewer_user_id=reviewer_user_id,
                    reason=reason,
                    now=now,
                )
                store.upsert_pt_classification(outcome.pt_classification)  # type: ignore[arg-type]
            else:
                current_wwf = store.get_wwf_classification(item.product_id)
                assert current_wwf is not None
                outcome = accept_wwf_item(
                    claimed,
                    current=current_wwf,
                    reviewer_user_id=reviewer_user_id,
                    reason=reason,
                    now=now,
                )
                store.upsert_wwf_classification(outcome.wwf_classification)  # type: ignore[arg-type]

        elif action == "bulk_defer":
            outcome = defer_item(claimed, reviewer_user_id=reviewer_user_id, reason=reason, now=now)

        else:  # bulk_change_pt_group
            current_pt = store.get_pt_classification(item.product_id)
            outcome = change_pt_item(
                claimed,
                current=current_pt,
                to_group=target_pt_group,  # type: ignore[possibly-undefined]
                reviewer_user_id=reviewer_user_id,
                reason=reason,
                now=now,
            )
            store.upsert_pt_classification(outcome.pt_classification)  # type: ignore[arg-type]

        # Persist item state
        if outcome.item.status is ManualReviewStatus.DEFERRED:
            store.upsert_review_item(outcome.item)
        else:
            store.remove_review_item(item.product_id, methodology)

        # Persist the individual decision
        store.add_review_decision(outcome.decision)
        decision_ids.append(outcome.decision.id)

        # Per-item audit event
        store.append_audit(
            AuditEvent(
                id=uuid4(),
                organisation_id=project.organisation_id,
                actor_user_id=reviewer_user_id,
                action=AuditEventType.REVIEW_DECISION_MADE,
                target_table="review_items",
                target_id=item.product_id,
                metadata={
                    "bulk": True,
                    "decision": outcome.decision.decision.value,
                    "methodology": methodology.value,
                    "decision_id": str(outcome.decision.id),
                },
                created_at=now,
            )
        )

    # Bulk-level audit event
    store.append_audit(
        AuditEvent(
            id=uuid4(),
            organisation_id=project.organisation_id,
            actor_user_id=reviewer_user_id,
            action=AuditEventType.REVIEW_BULK_ACTION,
            target_table="review_items",
            metadata={
                "action": action,
                "methodology": methodology.value,
                "count": len(items),
                "decision_ids": [str(d) for d in decision_ids],
            },
            created_at=now,
        )
    )

    return BulkActionResult(
        action=action,
        requested_count=len(product_ids),
        updated_count=len(items),
        decision_ids=tuple(decision_ids),
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
        raise ValueError(f"methodology {methodology.value} is not enabled on project {project.id}")
    products = store.list_products_for_project(project.id)
    started_at = datetime.now(UTC)

    if methodology is Methodology.PROTEIN_TRACKER:
        classifications = {
            p.id: c for p in products if (c := store.get_pt_classification(p.id)) is not None
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
            p.id: c for p in products if (c := store.get_wwf_classification(p.id)) is not None
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
        rows = tuple(ProteinTrackerCalculationRow.model_validate(r) for r in record.rows_payload)
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
            protein_sources={p.id: p.pt_fields.protein_source for p in products if p.pt_fields},
            items_purchased={p.id: p.pt_fields.items_purchased for p in products if p.pt_fields},
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
