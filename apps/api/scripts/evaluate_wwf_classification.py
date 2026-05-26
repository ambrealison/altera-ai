#!/usr/bin/env python
"""Phase WWF-D — WWF classification audit script.

Runs each entry in a WWF audit fixture through the Phase WWF-D
guards (``altera_api.ai.wwf_guards.apply_wwf_guards``) and reports
the same metrics flavour as the PT audit script:

  * top-level food-group accuracy
  * subgroup accuracy
  * composite-flag accuracy
  * composite-bucket accuracy
  * out_of_scope accuracy
  * unknown rate
  * review rate
  * guard firings per rule
  * top confusion pairs

Fixture format (see ``altera_api/data/audit/wwf_obvious_fixture.json``):

    {
      "name": "...",
      "cases": [
        {
          "product_name": "...",
          "expected_food_group": "FG1|FG2|FG3|FG4|FG5|FG6|FG7|out_of_scope",
          "expected_is_composite": false,
          "expected_fg1_subgroup": "..." | null,
          "expected_fg2_subgroup": "cheese|other_dairy_animal|dairy_alternative_plant" | null,
          "expected_fg3_subgroup": "plant_based_fat|animal_based_fat" | null,
          "expected_fg5_grain_kind": "whole_grain|refined_grain" | null,
          "expected_fg7_kind": "plant_based_snack|animal_based_snack" | null,
          "expected_composite_step1_bucket": "meat_based|seafood_based|vegetarian|vegan" | null
        }
      ]
    }

Usage:

    .venv/bin/python scripts/evaluate_wwf_classification.py
        # default fixture, markdown report

    .venv/bin/python scripts/evaluate_wwf_classification.py --json
        # JSON report for tooling

Exit code is non-zero when overall accuracy drops below ``--target``
(default 0.85) so the script can gate CI runs against regressions.
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

from altera_api.ai.wwf_guards import apply_wwf_guards  # noqa: E402
from altera_api.domain.common import ClassificationSource  # noqa: E402
from altera_api.domain.wwf import (  # noqa: E402
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
    WWFProductClassification,
)

_DEFAULT_FIXTURE = (
    _REPO_ROOT
    / "altera_api"
    / "data"
    / "audit"
    / "wwf_obvious_fixture.json"
)


@dataclass(frozen=True)
class WWFCaseResult:
    product_name: str
    expected_food_group: str
    actual_food_group: str
    expected_fg1: str | None
    actual_fg1: str | None
    expected_fg2: str | None
    actual_fg2: str | None
    expected_fg3: str | None
    actual_fg3: str | None
    expected_fg5: str | None
    actual_fg5: str | None
    expected_fg7: str | None
    actual_fg7: str | None
    expected_is_composite: bool
    actual_is_composite: bool
    expected_bucket: str | None
    actual_bucket: str | None
    actual_rule: str | None
    food_group_match: bool
    subgroup_match: bool
    composite_match: bool
    bucket_match: bool

    @property
    def fully_correct(self) -> bool:
        return (
            self.food_group_match
            and self.subgroup_match
            and self.composite_match
            and self.bucket_match
        )


def _seed_classification(
    food_group: WWFFoodGroup,
) -> WWFProductClassification:
    """Synthetic high-confidence ``unknown``-style classification.

    The guards see this baseline and rewrite it as they fire. Using
    ``unknown`` as the seed means we test the maximum reliance on
    the guards (the real production case is the model returning the
    right food group most of the time, with guards correcting the
    edge cases). To exercise that we'd need an actual model — see
    the PT audit script for the same trade-off.
    """
    return WWFProductClassification(
        product_id=uuid4(),
        wwf_food_group=food_group,
        wwf_is_composite=False,
        source=ClassificationSource.AI,
        confidence=Decimal("0.5"),
        ai_prompt_version="wwf-eval",
        ai_model="wwf-eval-fake",
        updated_at=datetime.now(UTC),
    )


def _enum_value(v: Any) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _evaluate_case(case: dict[str, Any]) -> WWFCaseResult:
    name = case["product_name"]
    expected_fg = case["expected_food_group"]
    expected_is_composite = bool(case.get("expected_is_composite", False))
    expected_fg1 = case.get("expected_fg1_subgroup")
    expected_fg2 = case.get("expected_fg2_subgroup")
    expected_fg3 = case.get("expected_fg3_subgroup")
    expected_fg5 = case.get("expected_fg5_grain_kind")
    expected_fg7 = case.get("expected_fg7_kind")
    expected_bucket = case.get("expected_composite_step1_bucket")

    # Seed at ``unknown`` so every case is forced through the
    # guards. The guards do the work; a real model would also
    # produce a baseline that's mostly correct.
    seed = _seed_classification(WWFFoodGroup.UNKNOWN)
    override = apply_wwf_guards(name, seed)
    if override is None:
        final = seed
        rule = None
    else:
        final = override.new_classification
        rule = override.rule

    actual_fg = final.wwf_food_group.value
    actual_fg1 = _enum_value(final.fg1_subgroup)
    actual_fg2 = _enum_value(final.fg2_subgroup)
    actual_fg3 = _enum_value(final.fg3_subgroup)
    actual_fg5 = _enum_value(final.fg5_grain_kind)
    actual_fg7 = _enum_value(final.fg7_snack_kind)
    actual_bucket = _enum_value(final.composite_step1_bucket)

    food_group_match = actual_fg == expected_fg
    # Subgroups: only check when the expected entry has one.
    subgroup_match = True
    if expected_fg1 is not None:
        subgroup_match = subgroup_match and actual_fg1 == expected_fg1
    if expected_fg2 is not None:
        subgroup_match = subgroup_match and actual_fg2 == expected_fg2
    if expected_fg3 is not None:
        subgroup_match = subgroup_match and actual_fg3 == expected_fg3
    if expected_fg5 is not None:
        subgroup_match = subgroup_match and actual_fg5 == expected_fg5
    if expected_fg7 is not None:
        subgroup_match = subgroup_match and actual_fg7 == expected_fg7
    composite_match = final.wwf_is_composite == expected_is_composite
    bucket_match = True
    if expected_bucket is not None:
        bucket_match = actual_bucket == expected_bucket

    return WWFCaseResult(
        product_name=name,
        expected_food_group=expected_fg,
        actual_food_group=actual_fg,
        expected_fg1=expected_fg1,
        actual_fg1=actual_fg1,
        expected_fg2=expected_fg2,
        actual_fg2=actual_fg2,
        expected_fg3=expected_fg3,
        actual_fg3=actual_fg3,
        expected_fg5=expected_fg5,
        actual_fg5=actual_fg5,
        expected_fg7=expected_fg7,
        actual_fg7=actual_fg7,
        expected_is_composite=expected_is_composite,
        actual_is_composite=final.wwf_is_composite,
        expected_bucket=expected_bucket,
        actual_bucket=actual_bucket,
        actual_rule=rule,
        food_group_match=food_group_match,
        subgroup_match=subgroup_match,
        composite_match=composite_match,
        bucket_match=bucket_match,
    )


@dataclass(frozen=True)
class WWFAuditReport:
    fixture_name: str
    total: int
    food_group_accuracy: float
    subgroup_accuracy: float
    composite_flag_accuracy: float
    composite_bucket_accuracy: float
    strict_accuracy: float
    unknown_rate: float
    review_rate: float
    guard_firings_by_rule: dict[str, int]
    top_confusions: dict[str, int]
    mismatches: list[WWFCaseResult]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_name": self.fixture_name,
            "total": self.total,
            "food_group_accuracy": round(self.food_group_accuracy, 4),
            "subgroup_accuracy": round(self.subgroup_accuracy, 4),
            "composite_flag_accuracy": round(
                self.composite_flag_accuracy, 4
            ),
            "composite_bucket_accuracy": round(
                self.composite_bucket_accuracy, 4
            ),
            "strict_accuracy": round(self.strict_accuracy, 4),
            "unknown_rate": round(self.unknown_rate, 4),
            "review_rate": round(self.review_rate, 4),
            "guard_firings_by_rule": self.guard_firings_by_rule,
            "top_confusions": self.top_confusions,
            "mismatches": [
                {
                    "product_name": m.product_name,
                    "expected_food_group": m.expected_food_group,
                    "actual_food_group": m.actual_food_group,
                    "expected_fg1": m.expected_fg1,
                    "actual_fg1": m.actual_fg1,
                    "expected_bucket": m.expected_bucket,
                    "actual_bucket": m.actual_bucket,
                    "rule": m.actual_rule,
                }
                for m in self.mismatches
            ],
        }


def run_audit(fixture: dict[str, Any]) -> WWFAuditReport:
    cases = fixture["cases"]
    results = [_evaluate_case(c) for c in cases]
    total = len(results) or 1
    fg_correct = sum(1 for r in results if r.food_group_match)
    sub_correct = sum(1 for r in results if r.subgroup_match)
    comp_correct = sum(1 for r in results if r.composite_match)
    bucket_correct = sum(1 for r in results if r.bucket_match)
    strict_correct = sum(1 for r in results if r.fully_correct)
    unknown_count = sum(
        1
        for r in results
        if r.actual_food_group == WWFFoodGroup.UNKNOWN.value
    )
    review_count = sum(
        1 for r in results if r.actual_rule is not None
    )
    rule_counter: Counter[str] = Counter()
    for r in results:
        if r.actual_rule is not None:
            rule_counter[r.actual_rule] += 1
    confusion_counter: Counter[str] = Counter()
    mismatches: list[WWFCaseResult] = []
    for r in results:
        if not r.fully_correct:
            mismatches.append(r)
            confusion_counter[
                f"{r.expected_food_group}→{r.actual_food_group}"
            ] += 1
    return WWFAuditReport(
        fixture_name=fixture.get("name", "<unnamed>"),
        total=total,
        food_group_accuracy=fg_correct / total,
        subgroup_accuracy=sub_correct / total,
        composite_flag_accuracy=comp_correct / total,
        composite_bucket_accuracy=bucket_correct / total,
        strict_accuracy=strict_correct / total,
        unknown_rate=unknown_count / total,
        review_rate=review_count / total,
        guard_firings_by_rule=dict(rule_counter),
        top_confusions=dict(confusion_counter.most_common(10)),
        mismatches=mismatches,
    )


def format_markdown(report: WWFAuditReport) -> str:
    lines: list[str] = []
    lines.append(f"# WWF classification audit — {report.fixture_name}")
    lines.append("")
    lines.append(f"- Total cases: **{report.total}**")
    lines.append(
        f"- Food-group accuracy: **{report.food_group_accuracy:.1%}**"
    )
    lines.append(
        f"- Subgroup accuracy:  **{report.subgroup_accuracy:.1%}**"
    )
    lines.append(
        f"- Composite flag accuracy: **{report.composite_flag_accuracy:.1%}**"
    )
    lines.append(
        f"- Composite bucket accuracy: **{report.composite_bucket_accuracy:.1%}**"
    )
    lines.append(
        f"- Strict (all fields) accuracy: **{report.strict_accuracy:.1%}**"
    )
    lines.append(
        f"- Unknown rate: **{report.unknown_rate:.1%}**"
    )
    lines.append(
        f"- Review rate (guard fired): **{report.review_rate:.1%}**"
    )
    lines.append("")
    lines.append("## Guard firings by rule")
    lines.append("")
    if not report.guard_firings_by_rule:
        lines.append("- (none)")
    for rule, count in sorted(
        report.guard_firings_by_rule.items(),
        key=lambda kv: (-kv[1], kv[0]),
    ):
        lines.append(f"- `{rule}`: {count}")
    lines.append("")
    lines.append("## Mismatches")
    lines.append("")
    if not report.mismatches:
        lines.append("- (none — fixture clean)")
    for m in report.mismatches:
        lines.append(
            f"- **{m.product_name}** — expected "
            f"`{m.expected_food_group}`, got `{m.actual_food_group}`"
            + (
                f"; expected_fg1=`{m.expected_fg1}`, actual_fg1=`{m.actual_fg1}`"
                if m.expected_fg1
                else ""
            )
            + (
                f"; expected_bucket=`{m.expected_bucket}`, actual_bucket=`{m.actual_bucket}`"
                if m.expected_bucket
                else ""
            )
            + (f"; rule=`{m.actual_rule}`" if m.actual_rule else "")
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=_DEFAULT_FIXTURE,
        help="Path to a WWF audit fixture JSON file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON report instead of the markdown summary.",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=0.85,
        help=(
            "Minimum strict-accuracy target (0–1). The script exits "
            "non-zero when accuracy drops below this value."
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
    return 0 if report.strict_accuracy >= args.target else 1


if __name__ == "__main__":
    # Silence the unused-import warnings (these are imported to make
    # them available to introspection / IDE jump-to-definition).
    _ = (
        WWFCompositeStep1Bucket,
        WWFFG1Subgroup,
        WWFFG2Subgroup,
        WWFFG3Subgroup,
        WWFFG5GrainKind,
        WWFFG7SnackKind,
    )
    sys.exit(main())
