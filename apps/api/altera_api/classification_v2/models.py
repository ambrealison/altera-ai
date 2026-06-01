"""Phase Quality-V2-A — V2 rule-engine core models.

These are plain, immutable dataclasses (no Pydantic dependency on the
hot path) describing a single rule's decision plus the metadata the
evaluator + future review UI need: confidence, whether human review is
required, the rule id, and a human-readable rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# A rule id is a stable, versioned string, e.g.
# ``pt_plant_core_legume_dish_v1``. Keeping it a bare ``str`` keeps the
# engine cheap; the ``_v1`` suffix lets us evolve a rule without
# breaking fixtures that pin the old behaviour.
RuleId = str


class RuleConfidence(StrEnum):
    """Coarse confidence band. The numeric ``confidence`` on
    :class:`RuleResult` is the precise value; this band makes
    thresholding + reporting readable."""

    HIGH = "high"      # >= auto-accept threshold
    MEDIUM = "medium"  # plausible, route to review
    LOW = "low"        # weak signal, route to review
    NONE = "none"      # no decision


class RuleDecision(StrEnum):
    """What the engine concluded for a product."""

    CLASSIFIED = "classified"   # a rule produced a category
    ABSTAIN = "abstain"         # no rule matched — defer to fallback/AI
    REVIEW = "review"           # classified but flagged for human check


@dataclass(frozen=True)
class RuleResult:
    """The outcome of evaluating one rule against one product.

    ``classification`` is an open dict so PT and WWF can carry their
    own shapes (pt_group / wwf_food_group + subgroup + bucket …)
    without coupling this core to either domain.
    """

    matched: bool
    rule_id: RuleId
    confidence: float = 0.0
    review_required: bool = False
    classification: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    @property
    def confidence_band(self) -> RuleConfidence:
        if not self.matched:
            return RuleConfidence.NONE
        if self.confidence >= 0.90:
            return RuleConfidence.HIGH
        if self.confidence >= 0.60:
            return RuleConfidence.MEDIUM
        return RuleConfidence.LOW

    @classmethod
    def no_match(cls, rule_id: RuleId) -> RuleResult:
        return cls(matched=False, rule_id=rule_id)
