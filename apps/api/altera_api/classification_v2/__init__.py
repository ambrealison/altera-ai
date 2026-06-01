"""Phase Quality-V2-A — V2 classification rule engine (skeleton).

Opt-in, evaluator-only for now. NOT wired into any production route.
The V1 guards in ``altera_api/ai/pt_guards.py`` and
``altera_api/ai/wwf_guards.py`` remain the production path and are
untouched by this package.
"""

from altera_api.classification_v2.models import (
    RuleConfidence,
    RuleDecision,
    RuleResult,
)
from altera_api.classification_v2.rule_engine import RuleEngine
from altera_api.classification_v2.rule_trace import RuleTrace, RuleTraceEntry

__all__ = [
    "RuleConfidence",
    "RuleDecision",
    "RuleResult",
    "RuleEngine",
    "RuleTrace",
    "RuleTraceEntry",
]
