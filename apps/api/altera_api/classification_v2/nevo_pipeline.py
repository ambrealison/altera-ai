"""Phase Quality-V2-C — NEVO matching pipeline: rules + embeddings.

Embeddings GENERATE candidates; the precision-first V2 rules DECIDE.
A candidate is never accepted on similarity alone, and embeddings can
never override a hard rejection — a trap reference ("Oil olive",
"Potatoes mashed with milk") is killed by the rules even when the
vector index ranks it first.

Flow (PART E):
  1. build a privacy-safe product query text;
  2. vector candidate search (top-k);
  3. gate each candidate with the V2 rules (secondary-ingredient /
     with-without / qualifier-concept / head-mismatch rejections);
  4. accept the first rule-confirmed candidate (high confidence);
  5. otherwise, a high-similarity candidate that merely *abstained*
     (no rule signal, but not rejected) may go to review;
  6. otherwise abstain / no_match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from altera_api.classification_v2.nevo_index import NevoVectorIndex, build_nevo_query_text
from altera_api.classification_v2.nevo_rules import gate_candidate

# A candidate that merely abstained (no rule signal) needs at least this
# cosine similarity to be surfaced for human review (never auto-accept).
_EMBED_REVIEW_THRESHOLD = 0.80


@dataclass(frozen=True)
class CandidateTrace:
    rank: int
    candidate_name: str
    nevo_code: str
    similarity: float
    accepted: bool
    match_type: str
    rejection_reason: str = ""
    confidence: float = 0.0  # the gate's confidence for this candidate


@dataclass(frozen=True)
class NevoDecision:
    matched: bool
    nevo_code: str | None
    food_name_en: str | None
    confidence: float
    match_type: str  # exact|alias|embedding|embedding_plus_rule|proxy_review|no_match
    review_required: bool
    rationale: str
    provider: str
    model: str
    top_candidates: list[CandidateTrace] = field(default_factory=list)
    rejected_candidates: list[CandidateTrace] = field(default_factory=list)


def decide_with_embeddings(
    product: dict,
    index: NevoVectorIndex,
    *,
    top_k: int | None = None,
    embed_review_threshold: float = _EMBED_REVIEW_THRESHOLD,
    full_trace: bool = False,
) -> NevoDecision:
    """Decide a NEVO match for one product using vector candidates +
    rules. ``product`` carries descriptor fields only (no commercial
    fields — enforced by the query-text builder).

    The decision is always first-accept (production-like short-circuit).
    With ``full_trace=True`` the loop still records EVERY candidate's gate
    result in ``top_candidates`` (it doesn't stop at the first accept),
    so the evaluator can report full top-k recall and the rank-6–20
    failure taxonomy. The returned decision is identical either way."""
    name = str(product.get("product_name", ""))
    query_text = build_nevo_query_text(product)
    scored = index.search(query_text, top_k=top_k)

    top: list[CandidateTrace] = []
    rejected: list[CandidateTrace] = []
    review_proxy: CandidateTrace | None = None
    accepted_hit: tuple[CandidateTrace, float] | None = None  # (trace, confidence)

    for sc in scored:
        gate = gate_candidate(name, sc.candidate)
        trace = CandidateTrace(
            rank=sc.rank,
            candidate_name=sc.candidate.food_name_en,
            nevo_code=sc.candidate.nevo_code,
            similarity=round(sc.similarity, 4),
            accepted=gate.accepted,
            match_type=gate.match_type,
            rejection_reason="" if gate.accepted else gate.reason,
            confidence=gate.confidence,
        )
        top.append(trace)

        if gate.accepted:
            if accepted_hit is None:
                accepted_hit = (trace, gate.confidence)
                if not full_trace:
                    break
            continue

        rejected.append(trace)
        # A 'proxy' (gate wants review — literal token present but not the
        # head) OR an 'abstain' (no rule signal, not a hard rejection) at
        # high similarity is the only embedding-only path — and only to
        # review, never auto-accept.
        if (
            accepted_hit is None
            and gate.match_type in ("abstain", "proxy")
            and sc.similarity >= embed_review_threshold
            and review_proxy is None
        ):
            review_proxy = trace

    if accepted_hit is not None:
        trace, confidence = accepted_hit
        # Rule-confirmed embedding candidate → accept.
        return NevoDecision(
            matched=True,
            nevo_code=trace.nevo_code,
            food_name_en=trace.candidate_name,
            confidence=confidence,
            match_type="embedding_plus_rule",
            review_required=False,
            rationale=(
                f"Vector candidate (rank {trace.rank}, sim {trace.similarity:.2f}) "
                f"confirmed by rule [{trace.match_type}]."
            ),
            provider=index.provider_name,
            model=index.provider.model,
            top_candidates=top,
            rejected_candidates=rejected,
        )

    if review_proxy is not None:
        return NevoDecision(
            matched=True,
            nevo_code=review_proxy.nevo_code,
            food_name_en=review_proxy.candidate_name,
            confidence=0.6,
            match_type="proxy_review",
            review_required=True,
            rationale=(
                f"Embedding-only candidate (sim {review_proxy.similarity:.2f}); no "
                "exact/alias rule match → review, not auto-accept."
            ),
            provider=index.provider_name,
            model=index.provider.model,
            top_candidates=top,
            rejected_candidates=rejected,
        )

    return NevoDecision(
        matched=False,
        nevo_code=None,
        food_name_en=None,
        confidence=0.0,
        match_type="no_match",
        review_required=True,
        rationale="No safe candidate passed the rules → abstain.",
        provider=index.provider_name,
        model=index.provider.model,
        top_candidates=top,
        rejected_candidates=rejected,
    )
