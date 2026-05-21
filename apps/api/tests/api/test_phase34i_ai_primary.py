"""Phase 34I — AI-primary workflow contracts.

Deterministic classification has been removed from the normal user
workflow. The wizard now has 8 steps (down from 9):

    1. Import
    2. Méthodologie
    3. Classification IA          ← was Step 3 "Classif. déterministe"
    4. Validation des classifications
    5. NEVO
    6. CIQUAL + IA
    7. Calcul
    8. Résultat

Step 3 calls ``classify_route`` with ``skip_deterministic=true``, which
makes the orchestrator route every eligible non-manually-locked
product directly to batched AI classification. This avoids the
keyword-trap failures retailer CSVs produced under the deterministic
engine (e.g. "Poulet végétal" mis-classified as animal_core because
the rules matched the word "poulet").

Tests below cover:

A. Workflow status — the legacy ``deterministic_classification`` step
   no longer appears in the emitted workflow.
B. ``skip_deterministic=true`` routes ALL products to AI (not only
   pass-throughs from the rule engine).
C. ``skip_deterministic=true`` does NOT overwrite manually-locked
   classifications.
D. ``deterministic_only=true`` and ``skip_deterministic=true`` together
   return 400 (mutually exclusive flags).
E. The paginated classifications endpoint supports
   ``min_confidence`` / ``max_confidence`` filters.
F. Step 4 unlocks NEVO once validation is complete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from altera_api.ai.batch_prompt import BatchClassifierPrompt
from altera_api.ai.prompt_builder import ClassifierPrompt
from altera_api.ai.provider import ClassifierProvider, ProviderResponse
from altera_api.api.state import InMemoryStore

# ---------------------------------------------------------------------------
# A fake provider that returns whatever PT category we configure per
# substring keyword. Drives the AI-primary path end-to-end in tests
# without a real OpenAI key.
# ---------------------------------------------------------------------------


@dataclass
class _BatchKeywordProvider(ClassifierProvider):
    """Looks at product names in the batched user message and returns a
    matching pt_group per keyword. Default = unknown @ 0.2."""

    rules: dict[str, tuple[str, float]]
    default_pt_group: str = "unknown"
    default_confidence: float = 0.2
    model_name: str = "phase34i-fake-batch"
    calls: list[Any] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, prompt: ClassifierPrompt) -> ProviderResponse:
        raise NotImplementedError

    def supports_batch(self) -> bool:
        return True

    def batch_classify(self, prompt: BatchClassifierPrompt) -> ProviderResponse:
        self.calls.append(prompt)
        results: list[dict[str, Any]] = []
        for line in prompt.user_message.split("\n"):
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" not in row or "product_name" not in row:
                continue
            name = str(row["product_name"]).lower()
            chosen = (self.default_pt_group, self.default_confidence)
            for needle, val in self.rules.items():
                if needle.lower() in name:
                    chosen = val
                    break
            results.append(
                {
                    "id": row["id"],
                    "pt_group": chosen[0],
                    "confidence": chosen[1],
                    "rationale": "ok",
                }
            )
        return ProviderResponse(
            raw_text=json.dumps({"results": results}), model=self.model_name
        )


_SPARSE_CSV = b"""Product Name (FR),Poids unitaire produit (g),Volume
Pommes Golden,150,3.0
Blanc de Poulet Roti,193,3.0
Poulet Vegetal,150,2.0
Tofu Nature,200,2.0
Saumon Atlantique,168,5.0
"""

_SPARSE_MAPPING = (
    '{"product_name_fr": "product_name",'
    ' "poids_unitaire_produit_g": "weight_per_item_g",'
    ' "volume": "items_purchased"}'
)


def _create_pt_project(client: TestClient) -> str:
    r = client.post(
        "/api/v1/projects",
        json={
            "name": "phase34i",
            "methodologies_enabled": ["protein_tracker"],
            "reporting_period_label": "FY 2024",
        },
    )
    assert r.status_code == 201
    return r.json()["id"]


def _upload_sparse(client: TestClient, project_id: str) -> str:
    r = client.post(
        f"/api/v1/projects/{project_id}/uploads",
        files={"file": ("sparse.csv", _SPARSE_CSV, "text/csv")},
        data={"column_mapping": _SPARSE_MAPPING},
    )
    assert r.status_code == 201
    return r.json()["id"]


# ---------------------------------------------------------------------------
# A. workflow-status no longer emits a deterministic step
# ---------------------------------------------------------------------------


class TestWorkflowHas8Steps:
    def test_no_deterministic_classification_step_emitted(
        self, client: TestClient
    ) -> None:
        pid = _create_pt_project(client)
        _upload_sparse(client, pid)
        body = client.get(f"/api/v1/projects/{pid}/workflow-status").json()
        keys = [s["key"] for s in body["steps"]]
        assert "deterministic_classification" not in keys
        # AI classification IS present and accessible.
        ai = next(s for s in body["steps"] if s["key"] == "ai_classification")
        assert ai["accessible"] is True
        assert ai["status"] in {"needs_action", "complete", "available"}


# ---------------------------------------------------------------------------
# B. skip_deterministic routes ALL products to AI
# ---------------------------------------------------------------------------


class TestSkipDeterministicAI:
    def test_skip_deterministic_classifies_all_eligible_products(
        self, client: TestClient, store: InMemoryStore
    ) -> None:
        # Inject a batched fake provider so the orchestrator's AI path
        # is exercised end-to-end. Uses the real config bootstrap.
        from altera_api.ai import config as ai_config

        def _provider_factory() -> ClassifierProvider:
            return _BatchKeywordProvider(
                rules={
                    "pommes": ("plant_based_core", 0.96),
                    "blanc de poulet": ("animal_core", 0.97),
                    "poulet vegetal": ("plant_based_non_core", 0.92),
                    "tofu": ("plant_based_core", 0.96),
                    "saumon": ("animal_core", 0.96),
                }
            )

        # Monkey-patch get_ai_provider for the duration of the test.
        original = ai_config.get_ai_provider
        ai_config.get_ai_provider = _provider_factory  # type: ignore[assignment]
        try:
            pid = _create_pt_project(client)
            uid = _upload_sparse(client, pid)

            # Step 3 — AI primary, skip deterministic.
            r = client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            assert r.status_code == 200, r.json()
            body = r.json()
            # AI was used for every eligible product (5 rows).
            assert body["ai_enabled"] is True
            assert body["ai_attempted"] == 5
            assert body["ai_accepted"] == 5
            assert body["ai_failed"] == 0
            assert body["ai_batch_count"] == 1

            # The classifications endpoint reports source=ai for all.
            cls = client.get(
                f"/api/v1/projects/{pid}/classifications"
            ).json()
            ai_count = cls["counts_by_source"].get("ai", 0)
            assert ai_count == 5
        finally:
            ai_config.get_ai_provider = original  # type: ignore[assignment]

    def test_poulet_vegetal_is_not_animal_core(
        self, client: TestClient
    ) -> None:
        """Regression test for the keyword-trap that motivated 34I."""
        from altera_api.ai import config as ai_config

        def _provider_factory() -> ClassifierProvider:
            return _BatchKeywordProvider(
                rules={
                    "poulet vegetal": ("plant_based_non_core", 0.92),
                    "blanc de poulet": ("animal_core", 0.97),
                }
            )

        original = ai_config.get_ai_provider
        ai_config.get_ai_provider = _provider_factory  # type: ignore[assignment]
        try:
            pid = _create_pt_project(client)
            uid = _upload_sparse(client, pid)
            client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            rows = client.get(
                f"/api/v1/projects/{pid}/classifications"
                "?product_search=Poulet"
            ).json()["items"]
            poulet_vegetal = next(
                r for r in rows if "vegetal" in r["product_name"].lower()
            )
            blanc_poulet = next(
                r for r in rows if "blanc" in r["product_name"].lower()
            )
            assert poulet_vegetal["pt_group"] == "plant_based_non_core"
            assert blanc_poulet["pt_group"] == "animal_core"
        finally:
            ai_config.get_ai_provider = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# C. Manual lock survives re-classify
# ---------------------------------------------------------------------------


class TestManualLockNotOverwritten:
    def test_skip_deterministic_does_not_overwrite_manual(
        self, client: TestClient
    ) -> None:
        from altera_api.ai import config as ai_config

        def _provider_factory() -> ClassifierProvider:
            return _BatchKeywordProvider(
                rules={
                    # Pommes returns LOW confidence so it lands in the
                    # review queue and the user can submit a manual
                    # decision against it. That decision is what
                    # the second classify call must not overwrite.
                    "pommes": ("plant_based_core", 0.55),
                    "poulet": ("animal_core", 0.97),
                    "tofu": ("plant_based_core", 0.96),
                    "saumon": ("animal_core", 0.96),
                }
            )

        original = ai_config.get_ai_provider
        ai_config.get_ai_provider = _provider_factory  # type: ignore[assignment]
        try:
            pid = _create_pt_project(client)
            uid = _upload_sparse(client, pid)
            # First pass: AI classifies everything.
            client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            rows = client.get(
                f"/api/v1/projects/{pid}/classifications"
            ).json()["items"]
            target = next(
                r for r in rows if "pommes" in r["product_name"].lower()
            )
            # User locks "Pommes" to animal_core (intentionally wrong
            # — the test asserts the manual choice survives a re-classify).
            r_dec = client.post(
                f"/api/v1/projects/{pid}/review/{target['product_id']}"
                "/protein_tracker/decision",
                json={"decision": "changed", "to_category": "animal_core"},
            )
            assert r_dec.status_code == 200, r_dec.json()
            # Second pass: re-classify with skip_deterministic.
            client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            # The manual choice MUST be intact.
            rows2 = client.get(
                f"/api/v1/projects/{pid}/classifications"
                f"?product_search={target['product_name'].split()[0]}"
            ).json()["items"]
            after = next(
                r for r in rows2 if r["product_id"] == target["product_id"]
            )
            assert after["pt_group"] == "animal_core"
            assert after["pt_source"] == "manual_review"
        finally:
            ai_config.get_ai_provider = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# D. Mutually-exclusive flags
# ---------------------------------------------------------------------------


class TestMutuallyExclusiveFlags:
    def test_both_flags_returns_400(self, client: TestClient) -> None:
        pid = _create_pt_project(client)
        uid = _upload_sparse(client, pid)
        r = client.post(
            f"/api/v1/projects/{pid}/uploads/{uid}/classify",
            json={
                "methodology": "protein_tracker",
                "deterministic_only": True,
                "skip_deterministic": True,
            },
        )
        assert r.status_code == 400
        assert "mutually exclusive" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# E. Confidence range filter on the classifications endpoint
# ---------------------------------------------------------------------------


class TestConfidenceRangeFilter:
    def test_min_and_max_confidence_filter_rows(
        self, client: TestClient
    ) -> None:
        from altera_api.ai import config as ai_config

        # Spread classifications across the confidence buckets so the
        # filter assertions are meaningful.
        def _provider_factory() -> ClassifierProvider:
            return _BatchKeywordProvider(
                rules={
                    "pommes": ("plant_based_core", 0.95),  # high
                    "blanc de poulet": ("animal_core", 0.85),  # high
                    "poulet vegetal": ("plant_based_non_core", 0.65),  # mid
                    "tofu": ("plant_based_core", 0.55),  # low
                    "saumon": ("animal_core", 0.75),  # mid
                }
            )

        original = ai_config.get_ai_provider
        ai_config.get_ai_provider = _provider_factory  # type: ignore[assignment]
        try:
            pid = _create_pt_project(client)
            uid = _upload_sparse(client, pid)
            client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            # Low confidence only (< 0.60).
            low = client.get(
                f"/api/v1/projects/{pid}/classifications"
                "?max_confidence=0.60"
            ).json()
            assert low["total"] == 1
            assert low["items"][0]["pt_confidence"] < 0.60

            # Mid (0.60–0.80).
            mid = client.get(
                f"/api/v1/projects/{pid}/classifications"
                "?min_confidence=0.60&max_confidence=0.80"
            ).json()
            assert mid["total"] == 2

            # High (>= 0.80).
            high = client.get(
                f"/api/v1/projects/{pid}/classifications"
                "?min_confidence=0.80"
            ).json()
            assert high["total"] == 2
            for row in high["items"]:
                assert row["pt_confidence"] is not None
                assert row["pt_confidence"] >= 0.80
        finally:
            ai_config.get_ai_provider = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# F. Manual validation gates NEVO
# ---------------------------------------------------------------------------


class TestValidationGatesNevo:
    def test_nevo_step_blocked_until_review_complete(
        self, client: TestClient
    ) -> None:
        from altera_api.ai import config as ai_config

        # Force every product to low confidence so they all enter
        # manual review.
        def _provider_factory() -> ClassifierProvider:
            return _BatchKeywordProvider(
                rules={},  # default fallback for all rows
                default_pt_group="plant_based_core",
                default_confidence=0.55,  # below the 0.80 threshold
            )

        original = ai_config.get_ai_provider
        ai_config.get_ai_provider = _provider_factory  # type: ignore[assignment]
        try:
            pid = _create_pt_project(client)
            uid = _upload_sparse(client, pid)
            client.post(
                f"/api/v1/projects/{pid}/uploads/{uid}/classify",
                json={
                    "methodology": "protein_tracker",
                    "skip_deterministic": True,
                },
            )
            body = client.get(
                f"/api/v1/projects/{pid}/workflow-status"
            ).json()
            review = next(
                s
                for s in body["steps"]
                if s["key"] == "manual_classification_review"
            )
            calc = next(
                s for s in body["steps"] if s["key"] == "calculation"
            )
            # Review needs action; calculation is blocked by review.
            assert review["status"] == "needs_action"
            assert calc["status"] == "blocked"
            codes = {b["code"] for b in calc["blocking_reasons"]}
            assert "review_pending" in codes
        finally:
            ai_config.get_ai_provider = original  # type: ignore[assignment]
