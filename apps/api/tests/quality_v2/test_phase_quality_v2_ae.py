"""Phase Quality-V2-AE — high-risk semantics + safety-downgrade diagnostics.

"high_risk" must mean a genuinely dangerous auto-apply (a would-enrich on a
non-food), NOT a nutrition-safety downgrade (skip_state_mismatch /
skip_proxy_too_broad / route_to_review). The CSV and project batch share the
semantics; the project batch adds an existing-V2 diff diagnostic. No DB writes;
V1 default; embeddings off; routes clean.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import altera_api.api  # noqa: F401  isort:skip
from altera_api.classification_v2 import nevo_v2_batch_dry_run as ac
from altera_api.classification_v2 import nevo_v2_project_batch_dry_run as proj
from altera_api.domain.enrichment import (
    NutritionEnrichmentSource,
    NutritionEnrichmentStatus,
)


def _row(outcome, action, policy="food"):
    return {"v2_outcome": outcome, "safety_action": action, "policy": policy}


# ---------------------------------------------------------------------------
# Part A — bucket semantics.
# ---------------------------------------------------------------------------
class TestBucketSemantics:
    def test_safety_downgrades_are_not_high_risk(self) -> None:
        assert ac.batch_bucket(
            _row("auto_accept", "skip_state_mismatch")) == "safety_downgrade"
        assert ac.batch_bucket(
            _row("auto_accept", "skip_proxy_too_broad")) == "safety_downgrade"
        assert ac.batch_bucket(
            _row("review_required", "route_to_review")) == "needs_review"
        assert ac.batch_bucket(
            _row("auto_accept", "skip_no_nutrition_value")) == "safety_downgrade"

    def test_would_enrich_food_is_auto_ready(self) -> None:
        assert ac.batch_bucket(
            _row("auto_accept", "would_enrich")) == "auto_ready"

    def test_would_enrich_pet_is_auto_ready(self) -> None:
        # pet food is food → not high-risk.
        assert ac.batch_bucket(
            _row("auto_accept", "would_enrich", "pet")) == "auto_ready"

    def test_would_enrich_nonfood_is_true_high_risk(self) -> None:
        assert ac.batch_bucket(
            _row("auto_accept", "would_enrich", "non_food")) == "true_high_risk"

    def test_no_match_and_policy_excluded(self) -> None:
        assert ac.batch_bucket(_row("no_match", "skip_no_match")) == "no_match"
        assert ac.batch_bucket(
            _row("policy_excluded", "skip_no_match", "non_food")) == "no_match"


class TestDiffBucket:
    def test_route_to_review_is_safety_downgraded(self) -> None:
        assert ac.diff_bucket(
            {"safety_action": "route_to_review", "confidence": 0.9}
        ) == "safety_downgraded_current_batch"

    def test_skip_state_mismatch_is_safety_downgraded(self) -> None:
        assert ac.diff_bucket(
            {"safety_action": "skip_state_mismatch", "confidence": 0.9}
        ) == "safety_downgraded_current_batch"

    def test_clean_high_conf_is_current_batch_better(self) -> None:
        assert ac.diff_bucket(
            {"safety_action": "would_enrich", "confidence": 0.99}
        ) == "current_batch_better"

    def test_clean_mid_conf_is_needs_manual_review(self) -> None:
        assert ac.diff_bucket(
            {"safety_action": "would_enrich", "confidence": 0.9}
        ) == "needs_manual_review"


# ---------------------------------------------------------------------------
# Part C — recommendation from a controlled CSV batch run.
# ---------------------------------------------------------------------------
def _stub_summary(results):
    return ac.build_summary(
        run_id="R", input_path="x", raw_count=len(results),
        groups=[{"raw_row_indices": [i]} for i in range(len(results))],
        results=results, sensitive=[], provider="fake", model="m", top_k=5,
        generated_at=None)


def _result(outcome, action, policy="food", dup=1):
    return {**_row(outcome, action, policy), "duplicate_count": dup}


class TestRecommendation:
    def test_ready_for_human_review_when_only_downgrades(self) -> None:
        results = [_result("auto_accept", "would_enrich"),
                   _result("auto_accept", "skip_state_mismatch"),
                   _result("review_required", "route_to_review"),
                   _result("no_match", "skip_no_match")]
        s = _stub_summary(results)
        assert s["true_high_risk_count"] == 0
        assert s["safety_downgrade_count"] == 1
        assert s["needs_review_count"] == 1
        assert s["recommendation"] == "ready_for_human_review"

    def test_investigate_only_with_true_high_risk(self) -> None:
        results = [_result("auto_accept", "would_enrich", "non_food"),
                   _result("auto_accept", "skip_proxy_too_broad")]
        s = _stub_summary(results)
        assert s["true_high_risk_count"] == 1
        assert s["recommendation"] == "investigate_high_risk"


# ---------------------------------------------------------------------------
# Part B/D — project batch: shared semantics + existing-V2 diff CSV.
# ---------------------------------------------------------------------------
def _product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, brand="b", retailer_category="cat",
        ingredients_text="x", labels=())


def _existing_total(pid, code, name):
    return SimpleNamespace(
        product_id=pid, nutrient="protein_pct", unit="g_per_100g",
        source=NutritionEnrichmentSource.NEVO, match_method="ai_assisted",
        source_version="v2_embeddings", enriched_value=Decimal("7"),
        status=NutritionEnrichmentStatus.ENRICHED,
        source_metadata={"nevo_code": code, "nevo_food_name": name})


def _cand(name, code, sim):
    return SimpleNamespace(candidate_name=name, nevo_code=code, similarity=sim,
                           rank=1, rejection_reason="")


def _decision(*, matched, code, name, conf=0.97, review=False):
    return SimpleNamespace(
        matched=matched, nevo_code=code, food_name_en=name, confidence=conf,
        match_type="concept", review_required=review,
        top_candidates=[_cand(name or "x", code, conf)])


class _FakeMatcher:
    def __init__(self, by_name):
        self._by_name = by_name

    def decide(self, query, top_k):  # noqa: ARG002
        return self._by_name[query["product_name"]]


class _Store:
    def __init__(self, products, records):
        self._products = products
        self._records = records
        self.reads: list[str] = []

    def get_project(self, pid):
        self.reads.append("get_project")
        return object()

    def list_products_for_project(self, pid):
        self.reads.append("list_products")
        return self._products

    def list_enrichment_records_for_project(self, pid):
        self.reads.append("list_records")
        return self._records

    def __getattr__(self, name):
        if name.split("_")[0] in ("add", "update", "delete", "upsert"):
            raise AssertionError(f"write: {name}")
        raise AttributeError(name)


class TestProjectBatch:
    def test_oil_like_diff_is_not_high_risk(self, tmp_path) -> None:
        # Huile de Colza: existing N-5041, batch N-606 (blend) → route_to_review.
        oil = _product("Huile de Colza Bio 1L")
        records = [_existing_total(oil.id, "5041", "Oil vegetable av")]
        matcher = _FakeMatcher({
            "Huile de Colza Bio 1L": _decision(
                matched=True, code="606", name="Oil Becel Blend Classic",
                review=True),
        })
        groups = proj.dedupe_products([oil], enabled=True)
        existing = proj._existing_v2_index(records)
        results = proj.build_results(groups, matcher=matcher, top_k=5,
                                     existing=existing)
        row = results[0]
        # route_to_review → needs_review bucket, NOT true_high_risk.
        assert row["_bucket"] == "needs_review"
        assert row["batch_matches_existing_v2"] == "false"
        diffs = proj._diff_rows(results)
        assert len(diffs) == 1
        assert diffs[0]["diff_bucket"] == "safety_downgraded_current_batch"

    def test_diff_csv_written_and_read_only(self, tmp_path) -> None:
        a = _product("Lentilles Vertes Cuites")
        records = [_existing_total(a.id, "N-LENT", "Lentils cooked")]
        store = _Store([a], records)
        project_id = uuid4()

        # Patch the matcher build to return a scripted matcher (avoid embeddings).
        import altera_api.classification_v2.nevo_v2_batch_dry_run as acmod
        original = acmod._build_matcher
        acmod._build_matcher = lambda args: (
            _FakeMatcher({"Lentilles Vertes Cuites": _decision(
                matched=True, code="N-DRIED", name="Lentils dried")}),
            "fake", "m")
        try:
            rc = proj.main(
                ["--project-id", str(project_id), "--output-dir", str(tmp_path),
                 "--evaluator-fake", "--run-id", "RX"], store=store)
        finally:
            acmod._build_matcher = original
        assert rc == 0
        # read-only.
        assert set(store.reads) <= {"get_project", "list_products",
                                    "list_records"}
        suffix = f"{project_id}_RX"
        diff_path = (tmp_path
                     / f"nevo_v2_project_batch_existing_v2_diffs_{suffix}.csv")
        assert diff_path.exists()
        s = json.loads((tmp_path
                        / f"nevo_v2_project_batch_summary_{suffix}.json"
                        ).read_text())
        assert "safety_downgrade_count" in s
        assert "true_high_risk_count" in s
        assert "existing_v2_diff_count" in s
        # the safety-downgrade and true_high_risk files exist.
        for name in ("safety_downgrade", "true_high_risk", "high_risk"):
            assert (tmp_path
                    / f"nevo_v2_project_batch_{name}_{suffix}.csv").exists()


# ---------------------------------------------------------------------------
# Safety.
# ---------------------------------------------------------------------------
class TestSafety:
    def test_v1_default_and_embeddings_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from altera_api.classification_v2.nevo_matcher import (
            resolve_nevo_matcher_version,
        )
        from altera_api.quality_config import embeddings_enabled

        monkeypatch.delenv("ALTERA_NEVO_MATCHER_VERSION", raising=False)
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        assert str(resolve_nevo_matcher_version()) == "v1"
        assert embeddings_enabled() is False

    def test_routes_clean(self) -> None:
        api_dir = Path(proj.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
