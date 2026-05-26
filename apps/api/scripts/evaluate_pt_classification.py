#!/usr/bin/env python
"""Phase 36J — Protein Tracker classification audit script.

Runs each entry in a PT audit fixture through the Phase 36I guards
and reports accuracy, the predicted-category distribution, the
review rate, the unknown rate, and the per-rule guard breakdown.

The fixture format is the JSON shape produced by
``altera_api/data/audit/pt_batch_150_fixture.json``:

    {
      "name": "...",
      "cases": [
        {
          "product_name": "Coulis Mangue",
          "expected_pt_group": "plant_based_non_core",
          "expected_review_required": true,
          "expected_guard_rule": "plant_core_demoted_preparation_or_simple_veg",
          "model_pt_group": "plant_based_core"
        },
        ...
      ]
    }

``model_pt_group`` is what the AI would have returned *before* the
Phase 36I guards. The script feeds that into ``apply_pt_guards`` to
get the corrected verdict and compares against
``expected_pt_group``.

Usage:

    .venv/bin/python scripts/evaluate_pt_classification.py
        # default: bundled fixture, markdown report on stdout

    .venv/bin/python scripts/evaluate_pt_classification.py \\
        --fixture path/to/custom_audit.json --json
        # JSON report for piping into observability tooling

Exit code is non-zero when accuracy drops below ``--target`` (default
0.90) so the script can gate CI runs against precision regressions.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from altera_api.ai.pt_guards import apply_pt_guards  # noqa: E402
from altera_api.domain.common import ClassificationSource  # noqa: E402
from altera_api.domain.protein_tracker import (  # noqa: E402
    ProteinTrackerGroup,
    ProteinTrackerProductClassification,
)

_DEFAULT_FIXTURE = (
    _REPO_ROOT
    / "altera_api"
    / "data"
    / "audit"
    / "pt_batch_150_fixture.json"
)


@dataclass(frozen=True)
class CaseResult:
    """Per-case audit outcome."""

    product_name: str
    expected_pt_group: str
    model_pt_group: str
    final_pt_group: str
    expected_guard_rule: str | None
    actual_guard_rule: str | None
    expected_review_required: bool
    actual_review_required: bool
    category_match: bool
    guard_rule_match: bool


def _build_classification(
    pt_group: ProteinTrackerGroup,
) -> ProteinTrackerProductClassification:
    """Synthetic classification object at confidence 0.9 — high enough
    that the guard's 0.69 clamp is observable as a review-required
    state without the model's pre-guard confidence being a confounder.
    """
    return ProteinTrackerProductClassification(
        product_id=uuid4(),
        pt_group=pt_group,
        source=ClassificationSource.AI,
        confidence=Decimal("0.9"),
        ai_prompt_version="phase36j-eval",
        ai_model="phase36j-eval-fake",
        updated_at=datetime.now(UTC),
    )


def evaluate_case(case: dict[str, Any]) -> CaseResult:
    """Run one fixture entry through ``apply_pt_guards``."""
    product_name = case["product_name"]
    model_group_str = case["model_pt_group"]
    model_group = ProteinTrackerGroup(model_group_str)
    cls = _build_classification(model_group)
    override = apply_pt_guards(product_name, cls)
    if override is None:
        final_group = model_group
        actual_rule: str | None = None
        actual_review_required = False
    else:
        final_group = override.new_classification.pt_group
        actual_rule = override.rule
        # Phase 36I clamps confidence to ≤ 0.69 which is below the
        # 0.70 auto-accept threshold, so the row IS routed to review.
        actual_review_required = (
            override.new_classification.confidence
            < Decimal("0.7")
        )

    expected_group_str = case["expected_pt_group"]
    expected_rule = case.get("expected_guard_rule")
    expected_review_required = bool(
        case.get("expected_review_required", False)
    )
    return CaseResult(
        product_name=product_name,
        expected_pt_group=expected_group_str,
        model_pt_group=model_group_str,
        final_pt_group=final_group.value,
        expected_guard_rule=expected_rule,
        actual_guard_rule=actual_rule,
        expected_review_required=expected_review_required,
        actual_review_required=actual_review_required,
        category_match=(final_group.value == expected_group_str),
        guard_rule_match=(actual_rule == expected_rule),
    )


@dataclass(frozen=True)
class AuditReport:
    fixture_name: str
    total: int
    correct: int
    incorrect: int
    accuracy: float
    review_rate: float
    unknown_rate: float
    predicted_distribution: dict[str, int]
    expected_distribution: dict[str, int]
    guard_overrides_by_rule: dict[str, int]
    mismatches: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_name": self.fixture_name,
            "total": self.total,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "accuracy": round(self.accuracy, 4),
            "review_rate": round(self.review_rate, 4),
            "unknown_rate": round(self.unknown_rate, 4),
            "predicted_distribution": self.predicted_distribution,
            "expected_distribution": self.expected_distribution,
            "guard_overrides_by_rule": self.guard_overrides_by_rule,
            "mismatches": [
                {
                    "product_name": m.product_name,
                    "expected": m.expected_pt_group,
                    "got": m.final_pt_group,
                    "model_said": m.model_pt_group,
                    "expected_rule": m.expected_guard_rule,
                    "actual_rule": m.actual_guard_rule,
                }
                for m in self.mismatches
            ],
        }


def run_audit(fixture: dict[str, Any]) -> AuditReport:
    cases = fixture["cases"]
    results = [evaluate_case(c) for c in cases]
    total = len(results)
    correct = sum(1 for r in results if r.category_match)
    incorrect = total - correct
    accuracy = correct / total if total else 0.0
    review_rate = (
        sum(1 for r in results if r.actual_review_required) / total
        if total
        else 0.0
    )
    unknown_rate = (
        sum(1 for r in results if r.final_pt_group == "unknown") / total
        if total
        else 0.0
    )
    predicted_dist = Counter(r.final_pt_group for r in results)
    expected_dist = Counter(r.expected_pt_group for r in results)
    rule_counter: Counter[str] = Counter()
    for r in results:
        if r.actual_guard_rule is not None:
            rule_counter[r.actual_guard_rule] += 1
    mismatches = [r for r in results if not r.category_match]
    return AuditReport(
        fixture_name=fixture.get("name", "<unnamed>"),
        total=total,
        correct=correct,
        incorrect=incorrect,
        accuracy=accuracy,
        review_rate=review_rate,
        unknown_rate=unknown_rate,
        predicted_distribution=dict(predicted_dist),
        expected_distribution=dict(expected_dist),
        guard_overrides_by_rule=dict(rule_counter),
        mismatches=mismatches,
    )


def format_markdown(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append(f"# PT classification audit — {report.fixture_name}")
    lines.append("")
    lines.append(f"- Total cases: **{report.total}**")
    lines.append(
        f"- Accuracy: **{report.accuracy:.1%}** "
        f"({report.correct} / {report.total})"
    )
    lines.append(f"- Review rate: **{report.review_rate:.1%}**")
    lines.append(f"- Unknown rate: **{report.unknown_rate:.1%}**")
    lines.append("")
    lines.append("## Predicted category distribution")
    lines.append("")
    for cat, count in sorted(report.predicted_distribution.items()):
        pct = count / report.total if report.total else 0.0
        lines.append(f"- `{cat}`: {count} ({pct:.1%})")
    lines.append("")
    lines.append("## Guard overrides by rule")
    lines.append("")
    if not report.guard_overrides_by_rule:
        lines.append("- (none fired)")
    else:
        for rule, count in sorted(report.guard_overrides_by_rule.items()):
            lines.append(f"- `{rule}`: {count}")
    lines.append("")
    if report.mismatches:
        lines.append(f"## Mismatches ({len(report.mismatches)})")
        lines.append("")
        for m in report.mismatches:
            lines.append(
                f"- **{m.product_name}** — expected `{m.expected_pt_group}`, "
                f"got `{m.final_pt_group}` (model said "
                f"`{m.model_pt_group}`, expected_rule="
                f"`{m.expected_guard_rule}`, actual_rule="
                f"`{m.actual_guard_rule}`)"
            )
    else:
        lines.append("## Mismatches")
        lines.append("")
        lines.append("- (none — fixture clean)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FIXTURE,
        help="Path to a PT audit fixture JSON file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON report instead of the markdown summary.",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=0.90,
        help=(
            "Minimum accuracy target (0–1). The script exits non-zero "
            "when accuracy drops below this value."
        ),
    )
    args = parser.parse_args()
    with args.fixture.open(encoding="utf-8") as f:
        fixture = json.load(f)
    report = run_audit(fixture)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(format_markdown(report))
    return 0 if report.accuracy >= args.target else 1


if __name__ == "__main__":
    sys.exit(main())
