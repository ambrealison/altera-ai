"""Phase 33I-AI — AI-assisted NEVO/CIQUAL reference matching.

The matcher's contract:

  * AI helps choose the reference. AI does **not** create the nutrition
    number. The protein values applied to a product always come from
    the matched reference row, never from the LLM.
  * The LLM must pick one of the candidates we shortlisted. Any code
    that was not in the shortlist is rejected (treated as no_match).
  * The product card sent to the LLM is run through
    ``assert_payload_allowed`` — commercial / quantity / pricing fields
    can never reach the model.
  * Output is strict JSON with a known schema. Parse failures fall
    through to a deterministic-no-match result; the calculation
    pipeline never blocks on a bad LLM response.

Thresholds:

  * confidence >= ``THRESHOLD_AUTO_APPLY`` (0.85) → "match" (auto-apply).
  * ``THRESHOLD_REVIEW`` (0.60) <= confidence < auto → "needs_review".
  * confidence < review → "no_match".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from altera_api.ai.nutrition_candidates import NutritionCandidate
from altera_api.ai.policy import assert_payload_allowed
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderError
from altera_api.domain.common import Methodology

#: Stamp on the rationale of every AI-assisted record so audits can
#: replay which prompt template produced a given match decision.
NUTRITION_PROMPT_VERSION = "nutrition_matcher_v1"

#: confidence >= → safe to apply automatically (still validated against
#: the candidate shortlist).
THRESHOLD_AUTO_APPLY: float = 0.85

#: confidence >= → create a NEEDS_MANUAL_REVIEW record so an analyst can
#: confirm or reject the proposed match.
THRESHOLD_REVIEW: float = 0.60


_SYSTEM_INSTRUCTIONS = """\
You are an assistant that helps a sustainability analyst select the
correct reference food row (from NEVO or CIQUAL) for a retailer product
whose nutrition label was not provided.

You DO NOT supply nutrition numbers — they will be looked up from the
chosen reference row by the calling system. Your job is only to pick
the most appropriate candidate, or to abstain.

Strict rules:
  - You MUST pick a reference_code from the candidates list. Inventing
    codes, names, or sources is forbidden.
  - You MUST output valid minified JSON only, no markdown, no prose,
    matching exactly this schema:
    {
      "decision": "match" | "needs_review" | "no_match",
      "target": "product",
      "source": "nevo" | "ciqual" | null,
      "reference_code": "<one of the candidates' reference_code>" | null,
      "reference_name": "<one of the candidates' name>" | null,
      "confidence": <number between 0.0 and 1.0>,
      "reason": "<one-sentence reason, <= 200 chars>",
      "normalised_query": "<the product name you matched on>"
    }
  - confidence MUST reflect how sure you are this is the same food.
    Use 0.85+ only when you are confident name and category align.
    Use 0.60–0.85 when likely but ambiguous; the analyst will review.
    Use <0.60 (or decision="no_match") when uncertain. Never fabricate.
"""


@dataclass(frozen=True)
class NutritionMatchProposal:
    """Result of one AI-matching attempt.

    ``decision`` is one of:
      * ``"match"``         — confidence >= AUTO_APPLY, source + code valid.
                              Caller should apply the reference's values.
      * ``"needs_review"``  — REVIEW <= confidence < AUTO_APPLY, valid code.
                              Caller should store NEEDS_MANUAL_REVIEW.
      * ``"no_match"``      — confidence < REVIEW, parse failed, or AI
                              tried to return a code not in the shortlist.
    """

    decision: str
    source: str | None
    reference_code: str | None
    reference_name: str | None
    confidence: float
    reason: str
    ai_model: str
    prompt_version: str = NUTRITION_PROMPT_VERSION


def build_product_card(
    *,
    product_name: str,
    brand: str | None,
    retailer_category: str | None,
    retailer_subcategory: str | None,
    ingredients_text: str | None,
    labels: tuple[str, ...] = (),
    language: str | None = None,
    country: str | None = None,
) -> dict[str, Any]:
    """Build the non-commercial product payload sent to the LLM.

    Only fields explicitly allow-listed in ``altera_api.ai.policy`` are
    emitted, so ``assert_payload_allowed`` cannot block this. Empty
    values are dropped to keep the prompt compact.
    """
    card: dict[str, Any] = {"product_name": product_name}
    if brand:
        card["brand"] = brand
    if retailer_category:
        card["retailer_category"] = retailer_category
    if retailer_subcategory:
        card["retailer_subcategory"] = retailer_subcategory
    if ingredients_text:
        card["ingredients_text"] = ingredients_text
    if labels:
        card["labels"] = list(labels)
    if language:
        card["language"] = language
    if country:
        card["country"] = country
    # Layered enforcement — the test suite asserts this fires on any
    # forbidden field, but we re-run here to defend against bugs in
    # future call-sites that build the dict differently.
    assert_payload_allowed(card)
    return card


def _build_prompt(
    product_card: dict[str, Any],
    candidates: list[NutritionCandidate],
) -> ClassifierPrompt:
    candidates_block = "\n".join(
        json.dumps(
            {
                "source": c.source,
                "reference_code": c.reference_code,
                "name": c.name,
                "food_group": c.food_group,
            },
            ensure_ascii=False,
        )
        for c in candidates
    )
    methodology_card = (
        "Candidates to choose from (one per line, JSON). The "
        "reference_code you return MUST appear in this list exactly:\n"
        f"{candidates_block}"
    )
    return ClassifierPrompt(
        methodology=Methodology.PROTEIN_TRACKER,  # carrier only — matcher is methodology-agnostic
        prompt_version=NUTRITION_PROMPT_VERSION,
        system_instructions=_SYSTEM_INSTRUCTIONS,
        methodology_card=methodology_card,
        product_card=product_card,
    )


def _no_match(reason: str, ai_model: str) -> NutritionMatchProposal:
    return NutritionMatchProposal(
        decision="no_match",
        source=None,
        reference_code=None,
        reference_name=None,
        confidence=0.0,
        reason=reason,
        ai_model=ai_model,
    )


def _trim_to_outer_braces(text: str) -> str | None:
    open_idx = text.find("{")
    close_idx = text.rfind("}")
    if open_idx == -1 or close_idx == -1 or close_idx < open_idx:
        return None
    return text[open_idx : close_idx + 1]


def propose_match(
    *,
    product_card: dict[str, Any],
    candidates: list[NutritionCandidate],
    provider: ClassifierProvider,
) -> NutritionMatchProposal:
    """Ask the LLM to pick one of ``candidates`` for ``product_card``.

    Returns a ``NutritionMatchProposal`` whose ``decision`` is always
    one of "match" | "needs_review" | "no_match". The caller looks the
    reference_code up in NEVO/CIQUAL to obtain the nutrition values.

    Hard guarantees:
      * No bytes leave this process before ``assert_payload_allowed``
        has run on ``product_card``.
      * If the AI returns a reference_code that is not in
        ``candidates``, the proposal is downgraded to ``no_match``.
      * AI never returns nutrition values — they live on the reference
        rows and are looked up by the caller via ``reference_code``.
    """
    if not candidates:
        return _no_match("no candidates available", provider.model)

    prompt = _build_prompt(product_card, candidates)
    try:
        response = provider.classify(prompt)
    except ProviderError as exc:
        return _no_match(f"provider error: {exc}", provider.model)

    raw = (response.raw_text or "").strip()
    trimmed = _trim_to_outer_braces(raw)
    if trimmed is None:
        return _no_match("ai returned non-JSON output", response.model)
    try:
        data = json.loads(trimmed)
    except json.JSONDecodeError:
        return _no_match("ai returned malformed JSON", response.model)
    if not isinstance(data, dict):
        return _no_match("ai output is not a JSON object", response.model)

    decision = str(data.get("decision", "")).lower()
    source = data.get("source")
    code = data.get("reference_code")
    name = data.get("reference_name")
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(data.get("reason", ""))[:240]

    # Decisive: if the AI says "no_match" we trust it.
    if decision == "no_match" or source is None or code is None:
        return _no_match(reason or "ai abstained", response.model)

    # Validate the proposed code is one of the candidates we showed it.
    allowed = {(c.source, c.reference_code) for c in candidates}
    if (str(source).lower(), str(code)) not in allowed:
        return _no_match(
            f"ai proposed reference_code={code!r} outside candidate list",
            response.model,
        )

    # Confidence routing.
    if confidence >= THRESHOLD_AUTO_APPLY:
        final_decision = "match"
    elif confidence >= THRESHOLD_REVIEW:
        final_decision = "needs_review"
    else:
        return _no_match(
            reason or "confidence below review threshold",
            response.model,
        )

    return NutritionMatchProposal(
        decision=final_decision,
        source=str(source).lower(),
        reference_code=str(code),
        reference_name=str(name) if name else None,
        confidence=confidence,
        reason=reason,
        ai_model=response.model,
    )
