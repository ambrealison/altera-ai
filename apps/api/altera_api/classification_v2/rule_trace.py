"""Phase Quality-V2-A — rule-engine execution trace.

Every engine run records which rules were tried, in order, and what
each returned. The trace makes V2 decisions auditable in the evaluator
(and later in the review UI / logs) — the opposite of the V1 guards,
where the winning rule is known but the rules that *didn't* fire are
invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from altera_api.classification_v2.models import RuleId, RuleResult


@dataclass(frozen=True)
class RuleTraceEntry:
    rule_id: RuleId
    matched: bool
    confidence: float
    rationale: str


@dataclass
class RuleTrace:
    """Ordered log of rule evaluations for a single product."""

    product_name: str
    entries: list[RuleTraceEntry] = field(default_factory=list)
    winning_rule_id: RuleId | None = None

    def record(self, result: RuleResult) -> None:
        self.entries.append(
            RuleTraceEntry(
                rule_id=result.rule_id,
                matched=result.matched,
                confidence=result.confidence,
                rationale=result.rationale,
            )
        )
        if result.matched and self.winning_rule_id is None:
            self.winning_rule_id = result.rule_id

    def as_dict(self) -> dict:
        return {
            "product_name": self.product_name,
            "winning_rule_id": self.winning_rule_id,
            "entries": [
                {
                    "rule_id": e.rule_id,
                    "matched": e.matched,
                    "confidence": e.confidence,
                    "rationale": e.rationale,
                }
                for e in self.entries
            ],
        }
