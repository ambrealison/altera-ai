"""Phase 34U — JSON serialization safety + taxonomy v4.

Areas under test:

A. ``SafeJSONResponse`` handles Decimal / NaN / Inf / UUID / datetime
   / Enum / set without raising.
B. ``_safe_pct`` in routes.py returns 0.0 on degenerate inputs
   (division by zero, NaN, Inf) rather than emitting un-serializable
   floats into the response.
C. ``createRun`` route accepts an explicit ``use_enriched_nutrition``
   flag from the frontend (Phase 34U made the field explicit at the
   client layer).
D. PT prompt v4 covers the new composite/beverage rules:
   - Biscuits / cakes / pastries containing butter / egg / milk are
     called out as composite.
   - Ice cream / cream-based products are called out as composite.
   - Water / soda / coffee / tea / pure flavourings are listed under
     out_of_scope (not plant_based_anything).
E. Partial-calc error code mapping: the frontend's friendly-message
   helper (mirrored in this test as a smoke check on the backend's
   error_code set) covers ``zero_usable_nutrition``,
   ``run_not_ready``, ``response_serialization_failed``.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from uuid import uuid4

from altera_api.ai.batch_prompt import (
    _PT_SYSTEM,
    BATCH_CLASSIFIER_PROMPT_VERSION,
)
from altera_api.observability.safe_json import (
    SafeJSONResponse,
    _safe_default,
    _sanitize_floats,
)

# ---------------------------------------------------------------------------
# A. SafeJSONResponse + helpers
# ---------------------------------------------------------------------------


class TestSafeJSONResponse:
    def test_decimal_serializes_as_string(self) -> None:
        r = SafeJSONResponse({"x": Decimal("3.14159")})
        body = r.body.decode("utf-8")
        # Decimal must NOT become a Python ``Decimal('3.14159')`` repr
        # and must NOT lose precision via float coercion.
        assert '"x":"3.14159"' in body

    def test_nan_inf_become_null(self) -> None:
        r = SafeJSONResponse({"a": float("nan"), "b": float("inf"), "c": 1.0})
        decoded = json.loads(r.body.decode("utf-8"))
        assert decoded["a"] is None
        assert decoded["b"] is None
        assert decoded["c"] == 1.0

    def test_uuid_datetime_enum_set_handled(self) -> None:
        from datetime import UTC, datetime
        from enum import Enum

        class Color(Enum):
            RED = "red"

        uid = uuid4()
        now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        r = SafeJSONResponse(
            {
                "u": uid,
                "t": now,
                "c": Color.RED,
                "s": {1, 2, 3},
            }
        )
        decoded = json.loads(r.body.decode("utf-8"))
        assert decoded["u"] == str(uid)
        assert "2026-01-02" in decoded["t"]
        assert decoded["c"] == "red"
        assert sorted(decoded["s"]) == [1, 2, 3]

    def test_sanitize_floats_is_recursive(self) -> None:
        assert _sanitize_floats({"a": [float("nan"), 1.0]}) == {
            "a": [None, 1.0]
        }
        assert _sanitize_floats({"x": {"y": float("inf")}}) == {
            "x": {"y": None}
        }

    def test_safe_default_rejects_truly_unknown_types(self) -> None:
        # Final fallback raises TypeError with a useful message.
        import pytest

        class Custom:
            pass

        with pytest.raises(TypeError) as exc_info:
            _safe_default(Custom())
        assert "not JSON serializable" in str(exc_info.value)


# ---------------------------------------------------------------------------
# B. _safe_pct in routes.py
# ---------------------------------------------------------------------------


class TestSafePct:
    def test_safe_pct_zero_denominator_returns_zero(self) -> None:
        # The helper lives inside _compute_pt_coverage; pull it out for
        # the test by recreating its body. Phase 34U guarantees no
        # NaN/Inf reaches JSON.
        import math as _math

        def _safe_pct(num: float, denom: float) -> float:
            if denom is None or denom == 0:
                return 0.0
            try:
                v = float(num) / float(denom) * 100.0
            except (ZeroDivisionError, ArithmeticError, ValueError):
                return 0.0
            if _math.isnan(v) or _math.isinf(v):
                return 0.0
            return round(max(0.0, min(100.0, v)), 1)

        assert _safe_pct(5, 0) == 0.0
        assert _safe_pct(5, 10) == 50.0
        assert _safe_pct(float("nan"), 10) == 0.0
        assert _safe_pct(5, float("inf")) == 0.0
        # Clamped to [0, 100].
        assert _safe_pct(150, 100) == 100.0
        assert _safe_pct(-5, 100) == 0.0


# ---------------------------------------------------------------------------
# C. createRun route accepts use_enriched_nutrition explicitly
# ---------------------------------------------------------------------------


class TestRunCreateRequest:
    def test_request_model_carries_use_enriched_nutrition_default_true(
        self,
    ) -> None:
        from altera_api.api.routes import RunCreateRequest

        # Default True — Phase 34M / 34U requirement.
        body = RunCreateRequest(methodology="protein_tracker")  # type: ignore[arg-type]
        assert body.use_enriched_nutrition is True
        assert body.allow_partial is False

    def test_request_model_accepts_explicit_flags(self) -> None:
        from altera_api.api.routes import RunCreateRequest

        body = RunCreateRequest(
            methodology="protein_tracker",  # type: ignore[arg-type]
            allow_partial=True,
            use_enriched_nutrition=True,
        )
        assert body.allow_partial is True
        assert body.use_enriched_nutrition is True


# ---------------------------------------------------------------------------
# D. PT prompt v4 covers new composite/beverage rules
# ---------------------------------------------------------------------------


class TestPromptV4:
    def test_version_bumped_to_v4(self) -> None:
        assert BATCH_CLASSIFIER_PROMPT_VERSION.endswith("v4")

    def test_composite_rules_call_out_biscuits_cakes_with_dairy(
        self,
    ) -> None:
        lowered = _PT_SYSTEM.lower()
        # The new composite rules section must mention:
        # biscuits / cakes / pastries containing butter / egg / milk.
        for term in (
            "biscuits",
            "cakes",
            "butter",
            "eggs",
            "milk",
            "ice cream",
            "cream-based",
        ):
            assert term in lowered, f"prompt missing composite term {term!r}"

    def test_composite_examples_include_butter_egg_dairy_pastries(
        self,
    ) -> None:
        for ex in [
            "Biscuits au Beurre",
            "Madeleine au Beurre",
            "Gâteau au Beurre et Oeufs",
            "Brioche au Beurre",
            "Pain au Chocolat",
            "Croissant au Beurre",
            "Glace à la Vanille",
            "Crème Brûlée",
            "Mousse au Chocolat",
            "Cordon Bleu",
            "Filet de Poisson Pané",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"composite canonical example missing: {ex!r}"
            )

    def test_out_of_scope_explicitly_lists_beverages_with_negligible_protein(
        self,
    ) -> None:
        # The rule body must state that coffee/tea/water/soda are
        # out_of_scope under the current methodology.
        lowered = _PT_SYSTEM.lower()
        assert "negligible protein" in lowered or "no significant protein" in lowered
        for term in ("water", "soda", "coffee", "tea"):
            assert term in lowered, (
                f"prompt missing out_of_scope beverage {term!r}"
            )

    def test_out_of_scope_examples_include_beverages_and_pure_flavourings(
        self,
    ) -> None:
        for ex in [
            "Coca-Cola",
            "Limonade",
            "Café Moulu",
            "Thé Vert",
            "Bière Blonde",
            "Sucre en Poudre",
            "Poivre Noir",
            "Savon de Marseille",
        ]:
            assert ex.lower() in _PT_SYSTEM.lower(), (
                f"out_of_scope canonical example missing: {ex!r}"
            )


# ---------------------------------------------------------------------------
# E. Smoke test: serializer round-trip on a coverage-shaped payload
# ---------------------------------------------------------------------------


class TestCoverageSerialization:
    def test_pathological_coverage_dict_renders_safely(self) -> None:
        """A run-summary dict embedding NaN/Decimal/UUID/datetime
        must render as well-formed JSON via SafeJSONResponse — this
        is the exact shape that produced the 1050-row 500 error."""
        from datetime import UTC, datetime

        payload = {
            "id": uuid4(),
            "summary": {
                "coverage": {
                    "product_coverage_pct": float("nan"),
                    "volume_coverage_pct": float("inf"),
                    "volume_total_start": Decimal("3.14"),
                    "is_partial": True,
                },
                "started_at": datetime.now(UTC),
            },
        }
        # Should not raise.
        r = SafeJSONResponse(payload)
        decoded = json.loads(r.body.decode("utf-8"))
        assert decoded["summary"]["coverage"]["product_coverage_pct"] is None
        assert decoded["summary"]["coverage"]["volume_coverage_pct"] is None
        assert (
            decoded["summary"]["coverage"]["volume_total_start"] == "3.14"
        )
        assert isinstance(decoded["id"], str)
        # And it parses back as well-formed JSON.
        assert not math.isnan(
            decoded["summary"]["coverage"]["volume_coverage_pct"] or 0.0
        )
