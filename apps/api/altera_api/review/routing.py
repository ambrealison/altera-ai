"""Verdict → ``ManualReviewItem`` routing.

The deterministic rules engine (Phase 6) and the AI classifier wrapper
(Phase 7) both emit *verdicts*. Some verdicts mean "the product is
classified, write it"; others mean "human eyes needed". This module
inspects a verdict and, when human eyes are needed, builds the
corresponding queue item.

The routing rules:

| Verdict source            | Verdict type              | Queue reason          |
|---------------------------|---------------------------|-----------------------|
| Rules engine              | ``PT/WWFMatched``         | — (accepted)          |
| Rules engine              | ``PT/WWFPassThrough``     | — (handed to AI)      |
| Rules engine              | ``PT/WWFRuleCollision``   | ``rule_collision``    |
| AI classifier             | ``AIAccepted``            | — (accepted)          |
| AI classifier             | ``AINeedsReviewLowConf*`` | ``low_confidence``    |
| AI classifier             | ``AINeedsReviewParseF*``  | ``ai_parse_failed``   |
| AI classifier             | ``AIProviderError``       | — (transient; retry)  |
| Analyst request           | (any)                     | ``requested``         |
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from altera_api.ai.classifier import (
    AIAccepted,
    AINeedsReviewLowConfidence,
    AINeedsReviewParseFailed,
    AIProviderError,
    AIVerdict,
)
from altera_api.domain.common import Methodology
from altera_api.domain.review import (
    ManualReviewItem,
    ManualReviewQueueReason,
    ManualReviewStatus,
)
from altera_api.rules.engine import (
    PTMatched,
    PTPassThrough,
    PTRuleCollision,
    PTVerdict,
    WWFMatched,
    WWFPassThrough,
    WWFRuleCollision,
    WWFVerdict,
)


def _enqueue(
    product_id: UUID,
    methodology: Methodology,
    reason: ManualReviewQueueReason,
    now: datetime,
) -> ManualReviewItem:
    return ManualReviewItem(
        product_id=product_id,
        methodology=methodology,
        status=ManualReviewStatus.IN_QUEUE,
        reason=reason,
        queued_at=now,
    )


def route_pt_verdict(
    rules_verdict: PTVerdict | None = None,
    ai_verdict: AIVerdict | None = None,
    *,
    now: datetime,
) -> ManualReviewItem | None:
    """Decide whether a PT verdict needs human review.

    Pass the rules-engine verdict and/or the AI verdict (typically only
    one is non-None per product per pass). Returns the queue item if
    routing applies, or ``None`` if nothing needs review.
    """
    if isinstance(rules_verdict, PTRuleCollision):
        return _enqueue(
            rules_verdict.product_id,
            Methodology.PROTEIN_TRACKER,
            ManualReviewQueueReason.RULE_COLLISION,
            now,
        )
    if isinstance(ai_verdict, AINeedsReviewLowConfidence):
        return _enqueue(
            ai_verdict.classification.product_id,
            Methodology.PROTEIN_TRACKER,
            ManualReviewQueueReason.LOW_CONFIDENCE,
            now,
        )
    if isinstance(ai_verdict, AINeedsReviewParseFailed):
        return _enqueue(
            ai_verdict.product_id,
            Methodology.PROTEIN_TRACKER,
            ManualReviewQueueReason.AI_PARSE_FAILED,
            now,
        )
    # PTMatched, PTPassThrough, AIAccepted, AIProviderError → no queue entry.
    _ = (PTMatched, PTPassThrough, AIAccepted, AIProviderError)
    return None


def route_wwf_verdict(
    rules_verdict: WWFVerdict | None = None,
    ai_verdict: AIVerdict | None = None,
    *,
    now: datetime,
) -> ManualReviewItem | None:
    """Decide whether a WWF verdict needs human review."""
    if isinstance(rules_verdict, WWFRuleCollision):
        return _enqueue(
            rules_verdict.product_id,
            Methodology.WWF,
            ManualReviewQueueReason.RULE_COLLISION,
            now,
        )
    if isinstance(ai_verdict, AINeedsReviewLowConfidence):
        return _enqueue(
            ai_verdict.classification.product_id,
            Methodology.WWF,
            ManualReviewQueueReason.LOW_CONFIDENCE,
            now,
        )
    if isinstance(ai_verdict, AINeedsReviewParseFailed):
        return _enqueue(
            ai_verdict.product_id,
            Methodology.WWF,
            ManualReviewQueueReason.AI_PARSE_FAILED,
            now,
        )
    _ = (WWFMatched, WWFPassThrough, AIAccepted, AIProviderError)
    return None
