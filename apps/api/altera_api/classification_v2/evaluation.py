"""Phase Quality-V2-A — evaluation core (importable + testable).

The CLI scripts in ``scripts/`` are thin wrappers around these
functions. Keeping the logic here means the metric computation is unit-
tested directly (no subprocess) and reusable.

Pipeline selection:
- classification ``v2`` → the V2 rule engine (this package).
- classification ``v1`` → the existing V1 guards (readable fallback),
  so the evaluator can compare V1 vs V2 on the same fixture.
- nevo ``v2`` → the V2 candidate gates (``nevo_rules``).

No embeddings or network are required for any path here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altera_api.classification_v2.nevo_rules import NevoCandidate, gate_candidate
from altera_api.classification_v2.pt_rules import PT_RULES
from altera_api.classification_v2.rule_engine import ProductInput, RuleEngine
from altera_api.classification_v2.wwf_rules import WWF_RULES
from altera_api.quality_config import MatcherVersion, PipelineVersion


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
def load_fixture(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data.get("cases", data if isinstance(data, list) else [])
    if not isinstance(cases, list):
        raise ValueError(f"fixture {path} has no 'cases' list")
    return cases


def _product_input(case: dict[str, Any]) -> ProductInput:
    return ProductInput(
        product_name=case.get("product_name", ""),
        retailer_category=case.get("retailer_category"),
        ingredients_text=case.get("ingredients_text"),
        labels=case.get("labels"),
    )


# ---------------------------------------------------------------------------
# Classification evaluation (PT + WWF)
# ---------------------------------------------------------------------------
@dataclass
class Mismatch:
    fixture_id: str
    product_name: str
    expected: str
    actual: str
    confidence: float
    source: str
    rule_id: str
    pipeline_version: str
    notes: str = ""
    top_candidates: str = ""


@dataclass
class ClassificationMetrics:
    task: str
    pipeline_version: str
    total: int = 0
    correct: int = 0
    auto_accept_total: int = 0
    auto_accept_correct: int = 0
    review_count: int = 0
    abstain_count: int = 0
    wrong_accepted: int = 0
    unknown_readable: int = 0  # readable name left with no category (abstain)
    failed: int = 0            # empty/unusable name
    mismatches: list[Mismatch] = field(default_factory=list)
    # WWF-specific
    composite_flag_correct: int = 0
    composite_bucket_total: int = 0
    composite_bucket_correct: int = 0

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def auto_accept_accuracy(self) -> float:
        return (
            self.auto_accept_correct / self.auto_accept_total
            if self.auto_accept_total
            else 0.0
        )

    @property
    def review_rate(self) -> float:
        return self.review_count / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "pipeline_version": self.pipeline_version,
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "auto_accept_total": self.auto_accept_total,
            "auto_accept_accuracy": round(self.auto_accept_accuracy, 4),
            "review_rate": round(self.review_rate, 4),
            "abstain_count": self.abstain_count,
            "wrong_accepted": self.wrong_accepted,
            "unknown_readable": self.unknown_readable,
            "failed": self.failed,
            "composite_flag_correct": self.composite_flag_correct,
            "composite_flag_accuracy": (
                round(self.composite_flag_correct / self.total, 4)
                if self.total
                else None
            ),
            "composite_bucket_accuracy": (
                round(
                    self.composite_bucket_correct / self.composite_bucket_total,
                    4,
                )
                if self.composite_bucket_total
                else None
            ),
            "mismatch_count": len(self.mismatches),
        }


def _v2_predict(task: str, product: ProductInput) -> tuple[dict[str, Any], float, bool, str]:
    """Returns (classification_dict, confidence, review_required, rule_id)."""
    engine = RuleEngine(
        PT_RULES if task == "pt" else WWF_RULES, name=f"{task}_v2"
    )
    outcome = engine.evaluate(product)
    r = outcome.result
    return r.classification, r.confidence, r.review_required, r.rule_id


def _v1_predict(task: str, product: ProductInput) -> tuple[dict[str, Any], float, bool, str]:
    """Baseline: the V1 readable fallback (deterministic, no AI)."""
    if task == "pt":
        from altera_api.ai.pt_guards import classify_readable_fallback

        fb = classify_readable_fallback(product.product_name)
        if fb is None:
            return {}, 0.0, True, "v1_no_match"
        group, rule = fb
        return {"pt_group": group.value}, 0.5, True, rule
    from altera_api.ai.wwf_guards import classify_wwf_readable_fallback

    fb = classify_wwf_readable_fallback(product.product_name)
    if fb is None:
        return {}, 0.0, True, "v1_no_match"
    fg, is_comp, fg1, fg2, fg3, fg5, fg7, bucket, rule = fb
    cls: dict[str, Any] = {
        "wwf_food_group": fg.value,
        "wwf_is_composite": is_comp,
    }
    if bucket is not None:
        cls["wwf_composite_step1_bucket"] = bucket.value
    return cls, 0.5, True, rule


def evaluate_classification(
    task: str,
    cases: list[dict[str, Any]],
    *,
    pipeline_version: PipelineVersion = PipelineVersion.V2,
    auto_accept_threshold: float = 0.90,
) -> ClassificationMetrics:
    assert task in ("pt", "wwf")
    m = ClassificationMetrics(task=task, pipeline_version=pipeline_version.value)
    expected_key = "expected_pt" if task == "pt" else "expected_wwf"
    group_key = "pt_group" if task == "pt" else "wwf_food_group"

    for case in cases:
        expected = case.get(expected_key) or {}
        exp_group = expected.get(group_key)
        if exp_group is None:
            continue
        m.total += 1
        product = _product_input(case)
        predict = _v2_predict if pipeline_version is PipelineVersion.V2 else _v1_predict
        cls, conf, review, rule_id = predict(task, product)
        act_group = cls.get(group_key)
        is_correct = act_group == exp_group

        if is_correct:
            m.correct += 1
        if not act_group:
            m.abstain_count += 1
            # A readable product name left without a category is an
            # "unknown readable"; an empty/unusable name is "failed".
            if product.product_name and product.product_name.strip():
                m.unknown_readable += 1
            else:
                m.failed += 1
        if review:
            m.review_count += 1

        auto_accepted = conf >= auto_accept_threshold and not review
        if auto_accepted:
            m.auto_accept_total += 1
            if is_correct:
                m.auto_accept_correct += 1
            elif act_group:
                m.wrong_accepted += 1

        # WWF composite checks
        if task == "wwf" and exp_group is not None:
            exp_comp = bool(expected.get("wwf_is_composite"))
            act_comp = bool(cls.get("wwf_is_composite"))
            if exp_comp == act_comp:
                m.composite_flag_correct += 1
            if exp_comp:
                m.composite_bucket_total += 1
                if (
                    cls.get("wwf_composite_step1_bucket")
                    == expected.get("wwf_composite_step1_bucket")
                ):
                    m.composite_bucket_correct += 1

        if not is_correct:
            m.mismatches.append(
                Mismatch(
                    fixture_id=str(case.get("id", "")),
                    product_name=product.product_name,
                    expected=str(exp_group),
                    actual=str(act_group or "—"),
                    confidence=conf,
                    source="rule_engine" if pipeline_version is PipelineVersion.V2 else "v1_fallback",
                    rule_id=rule_id,
                    pipeline_version=pipeline_version.value,
                    notes=str(case.get("notes", "")),
                )
            )
    return m


# ---------------------------------------------------------------------------
# NEVO evaluation
# ---------------------------------------------------------------------------
@dataclass
class NevoMetrics:
    matcher_version: str
    total: int = 0
    should_match_total: int = 0
    matched_correct: int = 0
    high_confidence_total: int = 0
    high_confidence_correct: int = 0
    abstain_count: int = 0
    false_positive_count: int = 0
    forbidden_rejected: int = 0
    forbidden_total: int = 0
    mismatches: list[Mismatch] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "matcher_version": self.matcher_version,
            "total": self.total,
            "coverage": round(self.matched_correct / self.should_match_total, 4)
            if self.should_match_total
            else None,
            "high_confidence_precision": round(
                self.high_confidence_correct / self.high_confidence_total, 4
            )
            if self.high_confidence_total
            else None,
            "abstain_count": self.abstain_count,
            "false_positive_count": self.false_positive_count,
            "forbidden_rejection_rate": round(
                self.forbidden_rejected / self.forbidden_total, 4
            )
            if self.forbidden_total
            else None,
            "mismatch_count": len(self.mismatches),
        }


def evaluate_nevo(
    cases: list[dict[str, Any]],
    *,
    matcher_version: MatcherVersion = MatcherVersion.V2,
    auto_accept_threshold: float = 0.90,
) -> NevoMetrics:
    m = NevoMetrics(matcher_version=matcher_version.value)
    for case in cases:
        m.total += 1
        name = case.get("product_name", "")
        expected = case.get("expected_match")
        should_match = bool(case.get("should_match", expected is not None))

        # Gate the expected match (if any).
        if should_match and expected:
            m.should_match_total += 1
            cand = NevoCandidate(
                nevo_code=expected.get("nevo_code", ""),
                food_name_en=expected.get("food_name_en", ""),
            )
            gate = gate_candidate(name, cand)
            if gate.accepted:
                m.matched_correct += 1
                if gate.confidence >= auto_accept_threshold:
                    m.high_confidence_total += 1
                    m.high_confidence_correct += 1
            else:
                m.abstain_count += 1
                m.mismatches.append(
                    Mismatch(
                        fixture_id=str(case.get("id", "")),
                        product_name=name,
                        expected=cand.food_name_en,
                        actual="(abstained)",
                        confidence=gate.confidence,
                        source="nevo_gate",
                        rule_id="gate_candidate",
                        pipeline_version=matcher_version.value,
                        notes=gate.reason,
                    )
                )
        elif not should_match:
            # Should abstain — there's nothing to accept, count as a
            # correct abstention (no candidate is high-confidence).
            m.abstain_count += 1

        # Forbidden matches must be rejected by the gates.
        for forbidden in case.get("forbidden_matches", []):
            m.forbidden_total += 1
            cand = NevoCandidate(nevo_code="X", food_name_en=forbidden)
            gate = gate_candidate(name, cand)
            if not gate.accepted:
                m.forbidden_rejected += 1
            else:
                # A forbidden candidate slipping through is a false
                # positive — the worst NEVO failure mode.
                m.false_positive_count += 1
                m.mismatches.append(
                    Mismatch(
                        fixture_id=str(case.get("id", "")),
                        product_name=name,
                        expected="(forbidden — should reject)",
                        actual=forbidden,
                        confidence=gate.confidence,
                        source="nevo_gate",
                        rule_id="gate_candidate",
                        pipeline_version=matcher_version.value,
                        notes="FALSE POSITIVE: forbidden candidate accepted",
                    )
                )
    return m


MISMATCH_CSV_COLUMNS = [
    "fixture_id",
    "product_name",
    "expected",
    "actual",
    "confidence",
    "source",
    "rule_id",
    "pipeline_version",
    "notes",
    "top_candidates",
]


def write_mismatches_csv(path: str | Path, mismatches: list[Mismatch]) -> None:
    import csv

    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=MISMATCH_CSV_COLUMNS)
        w.writeheader()
        for mm in mismatches:
            w.writerow(
                {
                    "fixture_id": mm.fixture_id,
                    "product_name": mm.product_name,
                    "expected": mm.expected,
                    "actual": mm.actual,
                    "confidence": mm.confidence,
                    "source": mm.source,
                    "rule_id": mm.rule_id,
                    "pipeline_version": mm.pipeline_version,
                    "notes": mm.notes,
                    "top_candidates": mm.top_candidates,
                }
            )


# ---------------------------------------------------------------------------
# V1 vs V2 comparison (Phase Quality-V2-B)
# ---------------------------------------------------------------------------
@dataclass
class CaseDelta:
    """One fixture case under both pipelines."""

    fixture_id: str
    product_name: str
    expected: str
    v1_actual: str
    v2_actual: str
    kind: str  # "improvement" | "regression" | "both_correct" | "both_wrong"


def _per_case_predictions(
    task: str, cases: list[dict[str, Any]], pipeline_version: PipelineVersion
) -> dict[str, tuple[str, str, bool]]:
    """Map fixture_id → (expected, actual, correct) for one pipeline."""
    group_key = "pt_group" if task == "pt" else "wwf_food_group"
    expected_key = "expected_pt" if task == "pt" else "expected_wwf"
    predict = _v2_predict if pipeline_version is PipelineVersion.V2 else _v1_predict
    out: dict[str, tuple[str, str, bool]] = {}
    for case in cases:
        exp = (case.get(expected_key) or {}).get(group_key)
        if exp is None:
            continue
        cls, _conf, _review, _rid = predict(task, _product_input(case))
        act = cls.get(group_key)
        out[str(case.get("id", ""))] = (str(exp), str(act or "—"), act == exp)
    return out


@dataclass
class ClassificationComparison:
    task: str
    v1: ClassificationMetrics
    v2: ClassificationMetrics
    deltas: list[CaseDelta] = field(default_factory=list)

    @property
    def improvements(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "improvement"]

    @property
    def regressions(self) -> list[CaseDelta]:
        return [d for d in self.deltas if d.kind == "regression"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "v1": self.v1.as_dict(),
            "v2": self.v2.as_dict(),
            "delta_accuracy": round(self.v2.accuracy - self.v1.accuracy, 4),
            "improvements": len(self.improvements),
            "regressions": len(self.regressions),
        }


def compare_classification(
    task: str,
    cases: list[dict[str, Any]],
    *,
    auto_accept_threshold: float = 0.90,
) -> ClassificationComparison:
    v1 = evaluate_classification(
        task, cases, pipeline_version=PipelineVersion.V1,
        auto_accept_threshold=auto_accept_threshold,
    )
    v2 = evaluate_classification(
        task, cases, pipeline_version=PipelineVersion.V2,
        auto_accept_threshold=auto_accept_threshold,
    )
    p1 = _per_case_predictions(task, cases, PipelineVersion.V1)
    p2 = _per_case_predictions(task, cases, PipelineVersion.V2)
    name_by_id = {str(c.get("id", "")): c.get("product_name", "") for c in cases}
    deltas: list[CaseDelta] = []
    for fid in p1.keys() & p2.keys():
        exp, a1, c1 = p1[fid]
        _exp2, a2, c2 = p2[fid]
        if c1 and c2:
            kind = "both_correct"
        elif not c1 and not c2:
            kind = "both_wrong"
        elif c2 and not c1:
            kind = "improvement"
        else:
            kind = "regression"
        deltas.append(CaseDelta(fid, name_by_id.get(fid, ""), exp, a1, a2, kind))
    return ClassificationComparison(task=task, v1=v1, v2=v2, deltas=deltas)


# ---------------------------------------------------------------------------
# Quality gates (Phase Quality-V2-B) — offline, non-CI-failing.
# A gate that fails means: do NOT activate V2; keep V1 default.
# ---------------------------------------------------------------------------
def pt_gates(cmp: ClassificationComparison) -> dict[str, bool]:
    v1, v2 = cmp.v1, cmp.v2
    gates = {
        "v2_accuracy_ge_v1": v2.accuracy >= v1.accuracy,
        "v2_wrong_accepted_le_v1": v2.wrong_accepted <= v1.wrong_accepted,
        "unknown_readable_zero": v2.unknown_readable == 0,
    }
    gates["passed"] = all(gates.values())
    return gates


def wwf_gates(cmp: ClassificationComparison) -> dict[str, bool]:
    v1, v2 = cmp.v1, cmp.v2
    v1_bucket = (
        v1.composite_bucket_correct / v1.composite_bucket_total
        if v1.composite_bucket_total else 0.0
    )
    v2_bucket = (
        v2.composite_bucket_correct / v2.composite_bucket_total
        if v2.composite_bucket_total else 0.0
    )
    gates = {
        "v2_food_group_accuracy_ge_v1": v2.accuracy >= v1.accuracy,
        "v2_composite_bucket_accuracy_ge_v1": v2_bucket >= v1_bucket,
        "v2_wrong_accepted_le_v1": v2.wrong_accepted <= v1.wrong_accepted,
        "unknown_readable_zero": v2.unknown_readable == 0,
    }
    gates["passed"] = all(gates.values())
    return gates


def nevo_gates(v2: NevoMetrics) -> dict[str, bool]:
    """NEVO V2 gates — V1 has no offline matcher, so these are absolute:
    zero high-confidence false positives + 100% forbidden rejection.
    Coverage may be lower than V1 (precision-first) and is informational."""
    forbidden_ok = (
        v2.forbidden_total == 0 or v2.forbidden_rejected == v2.forbidden_total
    )
    gates = {
        "high_confidence_false_positives_zero": v2.false_positive_count == 0,
        "forbidden_rejection_100": forbidden_ok,
    }
    gates["passed"] = all(gates.values())
    return gates


IMPROVEMENTS_CSV_COLUMNS = [
    "fixture_id", "product_name", "expected", "v1_actual", "v2_actual", "kind",
]


def write_improvements_csv(path: str | Path, deltas: list[CaseDelta]) -> None:
    """Cases where V1 and V2 disagree (improvements + regressions)."""
    import csv

    rows = [d for d in deltas if d.kind in ("improvement", "regression")]
    with Path(path).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=IMPROVEMENTS_CSV_COLUMNS)
        w.writeheader()
        for d in rows:
            w.writerow(
                {
                    "fixture_id": d.fixture_id,
                    "product_name": d.product_name,
                    "expected": d.expected,
                    "v1_actual": d.v1_actual,
                    "v2_actual": d.v2_actual,
                    "kind": d.kind,
                }
            )
