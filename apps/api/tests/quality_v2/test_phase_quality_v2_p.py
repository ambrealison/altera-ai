"""Phase Quality-V2-P — nutrition-enrichment safety layer for NEVO V2 dry-run.

A concept-correct matcher match can still be a NUTRITION-wrong source (dry vs
cooked, instant/powder vs brewed, sweetened vs plain, syrup/concentrate vs
whole food). This phase adds a SECOND-STAGE nutrition-safety policy that runs
ONLY in the ``nevo_v2_enrich`` dry-run proposals — it never changes the matcher
gates and never writes anything.

All offline; V1 stays default; embeddings off by default; no route imports
V2/embeddings; no DB writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from altera_api.classification_v2 import nevo_v2_enrich as cli
from altera_api.classification_v2.nevo_matcher import NevoMatcherVersion
from altera_api.classification_v2.nevo_nutrition_safety import (
    NUTRITION_SAFETY_ACTIONS,
    base_safety_action,
    nutrition_safety_action,
)


def _action(product: str, ref: str, **kw) -> str:
    kw.setdefault("matched", True)
    kw.setdefault("review_required", False)
    kw.setdefault("confidence", 0.96)
    kw.setdefault("protein", 10.0)
    return nutrition_safety_action(product_name=product, ref_name=ref, **kw)[0]


# ---------------------------------------------------------------------------
# Part A — stage-1 (matcher + value) gate is independent of physical state.
# ---------------------------------------------------------------------------
class TestBaseSafetyAction:
    def test_no_match(self) -> None:
        assert base_safety_action(
            matched=False, review_required=True, protein=None, confidence=0.0
        ) == "skip_no_match"

    def test_review_or_low_confidence(self) -> None:
        assert base_safety_action(
            matched=True, review_required=True, protein=10.0, confidence=0.99
        ) == "route_to_review"
        assert base_safety_action(
            matched=True, review_required=False, protein=10.0, confidence=0.5
        ) == "route_to_review"

    def test_no_nutrition_value(self) -> None:
        assert base_safety_action(
            matched=True, review_required=False, protein=None, confidence=0.96
        ) == "skip_no_nutrition_value"

    def test_high_conf_with_value(self) -> None:
        assert base_safety_action(
            matched=True, review_required=False, protein=10.0, confidence=0.96
        ) == "would_enrich"

    def test_base_gates_dominate_state_policy(self) -> None:
        # Even a state-aligned positive routes to review when confidence is low.
        assert _action(
            "Chocolat Noir", "Chocolate dark", confidence=0.5
        ) == "route_to_review"
        assert _action("Pois Chiches", "Chickpeas canned", matched=False) == (
            "skip_no_match"
        )
        assert _action(
            "Pois Chiches", "Chickpeas canned", protein=None
        ) == "skip_no_nutrition_value"


# ---------------------------------------------------------------------------
# Part B — state-mismatch rules downgrade concept-correct matches.
# ---------------------------------------------------------------------------
class TestStateMismatch:
    @pytest.mark.parametrize(
        "product,ref",
        [
            # cooked product must not enrich from dried/raw reference.
            ("Lentilles Vertes Cuites", "Lentils dried"),
            ("Lentilles Corail Cuites", "Lentils red raw"),
            # dry/packaged staple must not enrich from a cooked reference.
            ("Pâtes Fusilli Blé Complet", "Pasta wholemeal boiled"),
            ("Riz Basmati", "Rice white cooked"),
            ("Couscous Moyen", "Couscous prepared"),
            ("Quinoa Blanc", "Quinoa cooked"),
        ],
    )
    def test_staple_state_mismatch_skips(self, product, ref) -> None:
        assert _action(product, ref) == "skip_state_mismatch"

    @pytest.mark.parametrize(
        "product,ref",
        [
            ("Café Capsules Intense", "Coffee instant powder"),
            ("Café Grains Arabica", "Cappuccino instant"),
            ("Café Moulu Pur Arabica", "Coffee soluble powder"),
            ("Thé Noir Earl Grey", "Tea herbal sweetened instant"),
            ("Thé Vert Sencha", "Tea green sweetened mix"),
        ],
    )
    def test_beverage_processing_mismatch_skips(self, product, ref) -> None:
        assert _action(product, ref) == "skip_state_mismatch"


# ---------------------------------------------------------------------------
# Part B — proxy-too-broad (processing proxy, not a whole-food source).
# ---------------------------------------------------------------------------
class TestProxyTooBroad:
    @pytest.mark.parametrize(
        "product,ref",
        [
            ("Compote Pomme Nature", "Apple syrup"),
            ("Compote Pomme Nature", "Apple rinse"),
            ("Compote Poire", "Pear concentrate"),
            ("Confiture Fraise", "Strawberry essence"),
            ("Jus Multifruits", "Fruit aroma concentrate"),
        ],
    )
    def test_proxy_skips(self, product, ref) -> None:
        assert _action(product, ref) == "skip_proxy_too_broad"


# ---------------------------------------------------------------------------
# Part C — aligned positives stay would_enrich.
# ---------------------------------------------------------------------------
class TestAlignedPositives:
    @pytest.mark.parametrize(
        "product,ref",
        [
            ("Chocolat Noir 70%", "Chocolate dark"),
            ("Yaourt Grec Nature", "Yoghurt Greek full fat"),
            # canned legumes are NOT a cooked/dried conflict → safe.
            ("Pois Chiches", "Chickpeas canned"),
            ("Haricots Rouges", "Beans red canned"),
            ("Thon au Naturel", "Tuna in water tinned"),
            ("Maïs Doux", "Sweetcorn tinned"),
            ("Jus Orange avec Pulpe", "Orange juice with pulp"),
            ("Muesli Croustillant", "Muesli"),
            ("Corn Flakes Nature", "Cornflakes"),
        ],
    )
    def test_aligned_would_enrich(self, product, ref) -> None:
        assert _action(product, ref) == "would_enrich"

    def test_dry_staple_to_dry_reference_ok(self) -> None:
        # A dry product matched to a dry/unspecified reference is aligned.
        assert _action("Pâtes Fusilli", "Pasta wholemeal dry") == "would_enrich"
        assert _action("Lentilles Vertes", "Lentils dried") == "would_enrich"


# ---------------------------------------------------------------------------
# Part E — end-to-end dry-run via build_proposals + summary (deterministic).
# ---------------------------------------------------------------------------
def _candidate(name, reason=""):
    return SimpleNamespace(candidate_name=name, rejection_reason=reason)


def _decision(*, matched, code, food, conf, mt, review, top):
    return SimpleNamespace(
        matched=matched, nevo_code=code, food_name_en=food, confidence=conf,
        match_type=mt, review_required=review, top_candidates=top,
    )


class _FakeMatcher:
    """Returns a scripted decision keyed by product name (no embeddings)."""

    def __init__(self, by_name):
        self._by_name = by_name

    def decide(self, query, top_k):  # noqa: ARG002
        name = query.get("product_name") if isinstance(query, dict) else None
        return self._by_name[name]


def _entry(code, protein, name):
    return SimpleNamespace(
        nevo_code=code, protein_g_per_100g=protein, food_name_en=name,
    )


def test_build_proposals_layers_nutrition_safety() -> None:
    # Three concept-correct matcher matches; only one is nutrition-safe.
    def _p(name):
        return SimpleNamespace(
            id=uuid4(), product_name=name, retailer_category=None,
            ingredients_text=None, labels=(),
        )

    products = [
        _p("Chocolat Noir 70%"),
        _p("Pâtes Fusilli Blé Complet"),
        _p("Compote Pomme Nature"),
    ]
    decisions = {
        "Chocolat Noir 70%": _decision(
            matched=True, code="N-CHOC", food="Chocolate dark", conf=0.97,
            mt="concept", review=False, top=[_candidate("Chocolate dark")],
        ),
        "Pâtes Fusilli Blé Complet": _decision(
            matched=True, code="N-PASTA", food="Pasta wholemeal boiled",
            conf=0.95, mt="concept", review=False,
            top=[_candidate("Pasta wholemeal boiled")],
        ),
        "Compote Pomme Nature": _decision(
            matched=True, code="N-SYRUP", food="Apple syrup", conf=0.96,
            mt="concept", review=False, top=[_candidate("Apple syrup")],
        ),
    }
    nevo_by_code = {
        "N-CHOC": _entry("N-CHOC", 7.0, "Chocolate dark"),
        "N-PASTA": _entry("N-PASTA", 5.0, "Pasta wholemeal boiled"),
        "N-SYRUP": _entry("N-SYRUP", 0.1, "Apple syrup"),
    }
    rows = cli.build_proposals(
        products, version=NevoMatcherVersion.V2_EMBEDDINGS,
        matcher=_FakeMatcher(decisions), nevo=None, nevo_by_code=nevo_by_code,
        provider_name="fake", model="fake-model", top_k=5,
    )

    by_name = {r["product_name"]: r for r in rows}
    # All three matched at the matcher stage…
    assert all(r["matcher_outcome"] == "match" for r in rows)
    # …but only the aligned one is nutrition-safe.
    assert by_name["Chocolat Noir 70%"]["nutrition_safety_action"] == "would_enrich"
    assert by_name["Chocolat Noir 70%"]["would_persist"] is True
    pasta = by_name["Pâtes Fusilli Blé Complet"]
    assert pasta["nutrition_safety_action"] == "skip_state_mismatch"
    assert pasta["would_persist"] is False
    assert "water" in pasta["nutrition_safety_reason"]
    compote = by_name["Compote Pomme Nature"]
    assert compote["nutrition_safety_action"] == "skip_proxy_too_broad"
    assert compote["would_persist"] is False

    summary = cli.build_dry_run_summary(
        rows, project_id="p", version="v2-embeddings", provider="fake",
        model="fake-model", top_k=5, generated_at=None,
    )
    assert summary["matcher_match_count"] == 3
    assert summary["nutrition_would_enrich"] == 1
    counts = summary["nutrition_safety_counts"]
    assert set(counts) == set(NUTRITION_SAFETY_ACTIONS)
    assert counts["would_enrich"] == 1
    assert counts["skip_state_mismatch"] == 1
    assert counts["skip_proxy_too_broad"] == 1
    # Examples of downgraded rows are surfaced for observability.
    actions = {e["nutrition_safety_action"] for e in summary["skipped_examples"]}
    assert actions == {"skip_state_mismatch", "skip_proxy_too_broad"}


# ---------------------------------------------------------------------------
# Read-only store spy — end-to-end dry-run makes no DB writes.
# ---------------------------------------------------------------------------
class _ReadOnlyStore:
    _WRITES = frozenset({"add_enrichment_record", "upsert_pt_classification",
                         "add_product", "add_run", "add_job"})

    def __init__(self, products, nevo_entries=()):
        self._products = products
        self._nevo = list(nevo_entries)
        self.reads: list[str] = []

    def get_project(self, project_id):
        self.reads.append("get_project")
        return object()

    def list_products_for_project(self, project_id):
        self.reads.append("list_products_for_project")
        return self._products

    def list_nevo_entries(self):
        self.reads.append("list_nevo_entries")
        return self._nevo

    def __getattr__(self, name):
        if name in self._WRITES:
            raise AssertionError(f"read-only violation: {name}")
        raise AttributeError(name)


def _product(name):
    return SimpleNamespace(
        id=uuid4(), product_name=name, retailer_category=None,
        retailer_subcategory=None, ingredients_text=None, labels=(),
        pt_fields=object(),
    )


def _nevo_entry(code, protein, name):
    return SimpleNamespace(
        nevo_code=code, protein_g_per_100g=protein, food_name_en=name,
        food_name_nl="", food_group="", plant_protein_g_per_100g=None,
        animal_protein_g_per_100g=None,
    )


class TestDryRunNoWrites:
    def test_dry_run_emits_new_summary_and_no_writes(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        monkeypatch.delenv("ALTERA_ENABLE_EMBEDDINGS", raising=False)
        store = _ReadOnlyStore(
            [_product("Tofu nature"), _product("Lentilles Vertes Cuites")],
            nevo_entries=[_nevo_entry("NEVO-TOFU", 8.0, "Tofu")],
        )
        pid = str(uuid4())
        rc = cli.main(
            ["--project-id", pid, "--matcher-version", "v2-embeddings",
             "--evaluator-fake", "--reference-source", "fixture",
             "--cache-dir", "", "--output-dir", str(tmp_path)],
            store=store,
        )
        assert rc == 0
        summary = json.loads(
            (tmp_path / f"nevo_v2_enrich_proposals_{pid}.json").read_text()
        )
        # New two-stage shape is present; old key is gone.
        assert "safety_action_counts" not in summary
        assert set(summary["nutrition_safety_counts"]) == set(
            NUTRITION_SAFETY_ACTIONS
        )
        assert "matcher_outcome_counts" in summary
        assert "skipped_examples" in summary
        assert summary["persisted_writes"] == 0
        out = capsys.readouterr().out
        assert "STAGE 1" in out and "STAGE 2" in out
        # Only read methods were called.
        assert set(store.reads) <= {
            "get_project", "list_products_for_project", "list_nevo_entries"
        }


# ---------------------------------------------------------------------------
# Safety — defaults unchanged; routes clean (no V2/embeddings import).
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

    def test_routes_do_not_import_v2(self) -> None:
        api_dir = Path(cli.__file__).resolve().parents[1] / "api"
        offenders = [
            p.name for p in api_dir.rglob("*.py")
            if "classification_v2" in p.read_text(encoding="utf-8")
            or "altera_api.embeddings" in p.read_text(encoding="utf-8")
            or "nevo_nutrition_safety" in p.read_text(encoding="utf-8")
        ]
        assert not offenders
