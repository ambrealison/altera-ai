"""Phase Quality-V2-A — ordered, deterministic rule engine.

A rule is any callable ``(ProductInput) -> RuleResult``. The engine
runs them in priority order, records a trace, and returns the first
match. Rules are pure + deterministic so the evaluator is reproducible
and individual rules are unit-testable in isolation.

This engine is invoked only by the evaluator + tests in this phase; no
production route calls it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from altera_api.classification_v2.models import RuleResult
from altera_api.classification_v2.rule_trace import RuleTrace


@dataclass(frozen=True)
class ProductInput:
    """Non-commercial product descriptors a rule may read. Mirrors the
    AI input policy: NO volume / sales / weight / price / margin."""

    product_name: str
    retailer_category: str | None = None
    ingredients_text: str | None = None
    labels: str | None = None


Rule = Callable[[ProductInput], RuleResult]


@dataclass
class EngineOutcome:
    result: RuleResult
    trace: RuleTrace


class RuleEngine:
    """Runs an ordered list of rules; first match wins."""

    def __init__(self, rules: list[Rule], *, name: str = "rule_engine") -> None:
        self._rules = rules
        self.name = name

    def evaluate(self, product: ProductInput) -> EngineOutcome:
        trace = RuleTrace(product_name=product.product_name)
        for rule in self._rules:
            result = rule(product)
            trace.record(result)
            if result.matched:
                return EngineOutcome(result=result, trace=trace)
        # No rule fired — abstain (defer to V1 fallback / AI / review).
        abstain = RuleResult(
            matched=False,
            rule_id="abstain",
            confidence=0.0,
            review_required=True,
            rationale="No V2 rule matched; abstaining.",
        )
        trace.record(abstain)
        return EngineOutcome(result=abstain, trace=trace)
