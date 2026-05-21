"""Phase 34F — Batched AI classification orchestrator.

Replaces the per-product OpenAI call with a single batched call per N
products (default 50). Returns one verdict per input product in the
same order so the upstream orchestrator can apply them by id.

Verdict types and the threshold semantics are intentionally identical
to ``classifier.py`` so existing storage and review-queue logic do not
need to change.

Parse-failure policy: a single bad JSON response from the batched
provider call no longer dooms every product in the batch. We:

1. Try to parse the response as ``{"results": [...]}``.
2. For each input id, look up its matching result by id.
3. If a result is missing or malformed for one product, that single
   product gets :class:`AINeedsReviewParseFailed`; the rest are still
   classified normally.

This is the behaviour the wizard needs to never report "0 classified /
N failed" again on ordinary retailer CSVs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pydantic import ValidationError as _PydanticValidationError

from altera_api.ai.batch_prompt import (
    BATCH_CLASSIFIER_PROMPT_VERSION,
    DEFAULT_BATCH_SIZE,
    build_batch_classifier_prompt,
)
from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
    AIVerdict,
)
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.provider import ClassifierProvider, ProviderError
from altera_api.ai.result_schema import (
    PTClassifierResult,
    WWFClassifierResult,
)
from altera_api.domain.common import Methodology
from altera_api.domain.product import NormalizedProduct


@dataclass(frozen=True)
class BatchVerdictBundle:
    """Per-batch aggregate diagnostics surfaced to the orchestrator.

    The route layer flattens these into the ClassifyResponse so the
    wizard's Step 4 can show full diagnostic counts and never look
    silent.
    """

    verdicts: list[AIVerdict]
    batch_count: int
    parse_failures: int
    provider_errors: int
    unsupported_category_failures: int
    sample_errors: list[str]


def _chunked(
    seq: list[NormalizedProduct], n: int
) -> Iterable[list[NormalizedProduct]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _parse_results_envelope(raw_text: str) -> list[dict[str, object]]:
    """Extract the ``results`` array from the model's batched response.

    Tolerant of the model wrapping its answer in surrounding prose by
    searching for the outermost ``{...}``. The strict per-row validation
    happens after the envelope is found.
    """
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in batched response")
    try:
        envelope = json.loads(raw_text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON decode failed: {exc.msg}") from exc
    if not isinstance(envelope, dict):
        raise ValueError("batched response top-level must be an object")
    results = envelope.get("results")
    if not isinstance(results, list):
        raise ValueError("batched response missing `results` array")
    return [r for r in results if isinstance(r, dict)]


def _result_for_id(
    results: list[dict[str, object]], item_id: str
) -> dict[str, object] | None:
    for r in results:
        if r.get("id") == item_id:
            return r
    return None


def _coerce_pt_result(
    raw_row: dict[str, object], methodology_value: str
) -> PTClassifierResult:
    """Build a strict PTClassifierResult from a per-row batch dict.

    The batched response uses a flat per-row shape (id, pt_group,
    confidence, rationale). We materialise that into the existing
    :class:`PTClassifierResult` so downstream storage uses the same
    type as the single-product path.
    """
    pt_group = raw_row.get("pt_group")
    confidence = raw_row.get("confidence")
    rationale = raw_row.get("rationale") or ""
    if not isinstance(pt_group, str):
        raise ValueError("pt_group missing or not a string")
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence missing or not numeric")
    return PTClassifierResult.model_validate(
        {
            "methodology": methodology_value,
            "pt_group": pt_group,
            "confidence": float(confidence),
            "rationale": str(rationale)[:240],
        }
    )


def _coerce_wwf_result(
    raw_row: dict[str, object], methodology_value: str
) -> WWFClassifierResult:
    """Same idea for WWF — minimal coercion, then strict validation.

    The batched WWF prompt only asks for the top-level food group and
    composite flag; subgroup detail is left for the deterministic /
    manual / nutrition phases to populate. We satisfy
    :class:`WWFClassifierResult`'s subgroup-required validator by
    relying on its existing behaviour for system states (FG-out_of_scope
    and unknown require NO subgroup fields).
    """
    wwf_food_group = raw_row.get("wwf_food_group")
    confidence = raw_row.get("confidence")
    rationale = raw_row.get("rationale") or ""
    is_composite = bool(raw_row.get("wwf_is_composite", False))
    if not isinstance(wwf_food_group, str):
        raise ValueError("wwf_food_group missing or not a string")
    if not isinstance(confidence, (int, float)):
        raise ValueError("confidence missing or not numeric")
    return WWFClassifierResult.model_validate(
        {
            "methodology": methodology_value,
            "wwf_food_group": wwf_food_group,
            "wwf_is_composite": is_composite,
            "confidence": float(confidence),
            "rationale": str(rationale)[:240],
        }
    )


def batch_classify(
    products: list[NormalizedProduct],
    provider: ClassifierProvider,
    methodology: Methodology,
    *,
    now: datetime,
    threshold: Decimal = Decimal("0.8"),
    batch_size: int = DEFAULT_BATCH_SIZE,
    prompt_version: str = BATCH_CLASSIFIER_PROMPT_VERSION,
) -> BatchVerdictBundle:
    """Classify ``products`` in batches and return ordered verdicts.

    Output verdicts are in the same order as ``products``. One
    AIProviderError or batched-response parse failure does NOT poison
    the whole input; only the affected batch (or per-product result
    within a batch) is marked failed.
    """
    out: list[AIVerdict] = []
    batch_count = 0
    parse_failures = 0
    provider_errors = 0
    unsupported_category_failures = 0
    sample_errors: list[str] = []

    def _maybe_sample(msg: str) -> None:
        # Keep at most the first 10 distinct sample errors so the
        # diagnostic counter does not unbounded-grow on a bad day.
        if len(sample_errors) < 10:
            sample_errors.append(msg)

    for chunk in _chunked(products, batch_size):
        batch_count += 1
        items = [
            (str(p.id), ClassifierPromptInput.from_product(p)) for p in chunk
        ]
        prompt = build_batch_classifier_prompt(
            items, methodology, prompt_version=prompt_version
        )

        try:
            response = provider.batch_classify(prompt)
        except ProviderError as exc:
            # Whole batch failed at provider level; mark each product
            # with a ProviderError verdict so the orchestrator routes
            # them to manual review with reason ``ai_provider_error``.
            provider_errors += len(chunk)
            _maybe_sample(f"provider_error: {exc}")
            for p in chunk:
                out.append(
                    AIProviderError(
                        product_id=p.id,
                        methodology=methodology,
                        message=str(exc),
                    )
                )
            continue
        except NotImplementedError:
            # Provider does not support batch — caller should have
            # checked supports_batch(); fall through with a uniform
            # provider-error verdict so the wizard surfaces the cause.
            provider_errors += len(chunk)
            _maybe_sample("provider does not support batch classification")
            for p in chunk:
                out.append(
                    AIProviderError(
                        product_id=p.id,
                        methodology=methodology,
                        message="batch unsupported",
                    )
                )
            continue

        try:
            results = _parse_results_envelope(response.raw_text)
        except ValueError as exc:
            # The batched response itself is unparseable — every product
            # in the chunk gets a parse_failed verdict.
            parse_failures += len(chunk)
            _maybe_sample(f"parse_failed: {exc}")
            for p in chunk:
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=str(exc),
                        second_error=str(exc),
                    )
                )
            continue

        for p in chunk:
            row = _result_for_id(results, str(p.id))
            if row is None:
                parse_failures += 1
                _maybe_sample("parse_failed: id missing from batched response")
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error="id missing from batched response",
                        second_error="id missing from batched response",
                    )
                )
                continue
            try:
                if methodology is Methodology.PROTEIN_TRACKER:
                    result = _coerce_pt_result(row, methodology.value)
                else:
                    result = _coerce_wwf_result(row, methodology.value)
            except (ValueError, _PydanticValidationError) as exc:
                # Treat "unsupported category" (an enum the model
                # invented) and "schema failure" as the same kind of
                # parse failure from the route's point of view, but
                # break out the unsupported_category subcounter when
                # we can detect it. The error_count() / message path is
                # different between pydantic versions; conservative
                # heuristic: any time the row has a string pt_group/
                # wwf_food_group but coercion failed, count it as
                # unsupported category.
                raw_cat = row.get("pt_group") or row.get("wwf_food_group")
                if isinstance(raw_cat, str):
                    unsupported_category_failures += 1
                else:
                    parse_failures += 1
                _maybe_sample(f"parse_failed: {exc}")
                out.append(
                    AINeedsReviewParseFailed(
                        product_id=p.id,
                        methodology=methodology,
                        first_error=str(exc),
                        second_error=str(exc),
                    )
                )
                continue

            classification = result.to_classification(
                product_id=p.id,
                ai_prompt_version=prompt_version,
                ai_model=response.model,
                now=now,
            )
            if classification.confidence < threshold:
                out.append(
                    AINeedsReviewLowConfidence(
                        classification=classification,
                        raw_text=response.raw_text,
                        threshold=threshold,
                    )
                )
            else:
                out.append(
                    AIAccepted(
                        classification=classification,
                        raw_text=response.raw_text,
                        parse_failures=0,
                    )
                )

    return BatchVerdictBundle(
        verdicts=out,
        batch_count=batch_count,
        parse_failures=parse_failures,
        provider_errors=provider_errors,
        unsupported_category_failures=unsupported_category_failures,
        sample_errors=sample_errors,
    )
