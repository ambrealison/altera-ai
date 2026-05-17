"""Deterministic rules engine.

A versioned, reproducible classifier that runs *before* the AI
classifier. The engine is allowed to say "I don't know" (pass-through),
"more than one rule disagrees" (rule collision), or "this product has
contradicting signals" (contradiction). It never produces a low-confidence
verdict.

See docs/classification/deterministic-rules.md for the canonical spec.
"""
from __future__ import annotations

from altera_api.rules.conditions import ConditionContext, match_condition_node
from altera_api.rules.engine import (
    PTContradiction,
    PTMatched,
    PTPassThrough,
    PTRuleCollision,
    PTVerdict,
    Verdict,
    WWFContradiction,
    WWFMatched,
    WWFPassThrough,
    WWFRuleCollision,
    WWFVerdict,
    classify_protein_tracker,
    classify_wwf,
)
from altera_api.rules.loader import (
    RuleSet,
    load_rules_from_dir,
    load_rules_from_file,
    load_rules_from_yaml,
)
from altera_api.rules.schema import (
    ConditionNode,
    PTRule,
    Rule,
    WWFRule,
    WWFRuleCategory,
)

#: Rules-engine version. Increments per docs/methodologies/versioning.md.
#: 0.2.0 — Phase 18: contradiction detection, expanded coverage for 30+
#: product categories, French-language keywords, FG6 starchy veg, mycoprotein.
VERSION: str = "0.2.0"

__all__ = [
    "VERSION",
    "ConditionContext",
    "ConditionNode",
    "PTContradiction",
    "PTMatched",
    "PTPassThrough",
    "PTRule",
    "PTRuleCollision",
    "PTVerdict",
    "Rule",
    "RuleSet",
    "Verdict",
    "WWFContradiction",
    "WWFMatched",
    "WWFPassThrough",
    "WWFRule",
    "WWFRuleCategory",
    "WWFRuleCollision",
    "WWFVerdict",
    "classify_protein_tracker",
    "classify_wwf",
    "load_rules_from_dir",
    "load_rules_from_file",
    "load_rules_from_yaml",
    "match_condition_node",
]
