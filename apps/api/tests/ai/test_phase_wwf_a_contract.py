"""Phase WWF-A/B/C — WWF AI contract fix + methodology docs + prompt v2.

Diagnosis confirmed by the brief: the WWF batch prompt was asking
the model for only ``wwf_food_group`` + ``wwf_is_composite`` /
``confidence`` / ``rationale``, but the ``WWFClassifierResult``
schema requires the matching subgroup field for FG1/FG2/FG3/FG5/FG7
plus ``wwf_composite_step1_bucket`` for composites. The result:
every model response was rejected by the Pydantic validators and
WWF rows landed as parse-failed.

This module asserts the Phase WWF-A fix:

  A. The batched WWF prompt explicitly lists every required subgroup
     field plus the composite Step 1 bucket and the rules for which
     subgroups are required for which food groups.
  B. ``_coerce_wwf_result`` reads every subgroup field and normalises
     it via tolerant aliases.
  C. A model response carrying the full schema validates cleanly
     into a ``WWFClassifierResult``.
  D. Aliases (``fish`` → ``seafood``, ``beef`` → ``red_meat``,
     ``oat milk`` → ``dairy_alternative_plant``, …) all resolve.
  E. The WWF prompt version is independent from the PT version.
  F. The methodology document exists and covers the key rules.

Out of scope (deferred to Phase WWF-D+):
  * Deterministic WWF guards (``wwf_guards.py``).
  * Eval fixture builder from XLSX/CSV.
  * Required-column upload alerts.
  * WWF-only and PT+WWF workflow end-to-end tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from altera_api.ai.batch_classifier import (
    _WWF_COMPOSITE_BUCKET_ALIASES,
    _WWF_FG1_SUBGROUP_ALIASES,
    _WWF_FG2_DAIRY_CLASS_ALIASES,
    _WWF_FG2_KIND_ALIASES,
    _WWF_FG3_KIND_ALIASES,
    _WWF_FG5_GRAIN_KIND_ALIASES,
    _WWF_FG7_KIND_ALIASES,
    _coerce_wwf_result,
)
from altera_api.ai.batch_prompt import (
    _WWF_SYSTEM,
    BATCH_CLASSIFIER_PROMPT_VERSION,
    BATCH_WWF_PROMPT_VERSION,
    build_batch_classifier_prompt,
)
from altera_api.ai.prompt_input import ClassifierPromptInput
from altera_api.ai.result_schema import (
    WWFClassifierResult,
    WWFFG2DairyClass,
    WWFFG2Kind,
)
from altera_api.domain.common import Methodology
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG3Subgroup,
    WWFFG5GrainKind,
    WWFFG7SnackKind,
    WWFFoodGroup,
)

# ---------------------------------------------------------------------------
# A. WWF prompt v2 contract
# ---------------------------------------------------------------------------


class TestWWFPromptV2Contract:
    """The WWF system prompt lists every required subgroup field, the
    rules for required-when / forbidden-when, and at least one
    canonical example per food group."""

    def test_prompt_mentions_every_subgroup_field(self) -> None:
        required_fields = [
            "wwf_fg1_subgroup",
            "wwf_fg2_kind",
            "wwf_fg2_dairy_class",
            "wwf_fg3_kind",
            "wwf_fg5_grain_kind",
            "wwf_fg7_kind",
            "wwf_composite_step1_bucket",
        ]
        for field in required_fields:
            assert field in _WWF_SYSTEM, (
                f"WWF prompt is missing required field: {field!r}"
            )

    def test_prompt_lists_every_fg1_subgroup(self) -> None:
        for sub in WWFFG1Subgroup:
            assert sub.value in _WWF_SYSTEM, (
                f"WWF prompt missing FG1 subgroup: {sub.value}"
            )

    def test_prompt_lists_every_composite_bucket(self) -> None:
        for bucket in WWFCompositeStep1Bucket:
            assert bucket.value in _WWF_SYSTEM, (
                f"WWF prompt missing composite bucket: {bucket.value}"
            )

    def test_prompt_states_composite_step1_rules(self) -> None:
        # Bucket precedence MUST be stated explicitly so the model
        # follows the right ordering.
        assert "Contains ANY meat" in _WWF_SYSTEM
        assert "meat_based" in _WWF_SYSTEM
        assert "seafood_based" in _WWF_SYSTEM
        assert "vegetarian" in _WWF_SYSTEM
        assert "vegan" in _WWF_SYSTEM

    def test_prompt_states_butter_is_fg3(self) -> None:
        # Common confusion: butter is dairy → FG2. Methodology says
        # FG3 animal_based_fat. The prompt must state this rule.
        assert "BUTTER" in _WWF_SYSTEM or "butter" in _WWF_SYSTEM
        assert "FG3" in _WWF_SYSTEM

    def test_prompt_states_sweetcorn_vs_mature_corn(self) -> None:
        assert "Sweetcorn" in _WWF_SYSTEM or "sweetcorn" in _WWF_SYSTEM
        assert "Mature" in _WWF_SYSTEM or "mature" in _WWF_SYSTEM

    def test_prompt_states_coconut_milk_vs_flesh(self) -> None:
        # FG1 nuts_seeds (flesh) vs FG2 dairy_alternative_plant (milk).
        assert "Coconut" in _WWF_SYSTEM or "coconut" in _WWF_SYSTEM

    def test_prompt_is_not_a_pt_prompt(self) -> None:
        # The prompt must NOT mention PT-specific concepts like
        # "plant_based_core" or "animal_core" — that's Protein Tracker.
        assert "plant_based_core" not in _WWF_SYSTEM
        assert "animal_core" not in _WWF_SYSTEM

    def test_prompt_distinguishes_processed_from_composite(self) -> None:
        # Phase WWF-B methodology — processed bread / parmesan /
        # smoked salmon are whole products, NOT composites.
        assert (
            "Processed bread" in _WWF_SYSTEM
            or "processed bread" in _WWF_SYSTEM
            or "NOT a composite" in _WWF_SYSTEM
            or "not a composite" in _WWF_SYSTEM
            or "not\n  a composite" in _WWF_SYSTEM
        )


class TestWWFPromptVersionIndependentFromPT:
    """Phase WWF-A — the WWF prompt version is separate so bumping it
    doesn't invalidate PT calibration samples (and vice-versa)."""

    def test_wwf_and_pt_versions_differ(self) -> None:
        assert (
            BATCH_WWF_PROMPT_VERSION != BATCH_CLASSIFIER_PROMPT_VERSION
        )

    def test_build_uses_wwf_version_for_wwf_methodology(self) -> None:
        prompt_input = ClassifierPromptInput(
            product_name="Yaourt Nature 0% MG",
            brand=None,
            retailer_category=None,
            retailer_subcategory=None,
            ingredients_text=None,
            labels=(),
            language=None,
            country=None,
        )
        prompt = build_batch_classifier_prompt(
            [("p1", prompt_input)], Methodology.WWF
        )
        assert prompt.prompt_version == BATCH_WWF_PROMPT_VERSION

    def test_build_uses_pt_version_for_pt_methodology(self) -> None:
        prompt_input = ClassifierPromptInput(
            product_name="Tofu Nature Bio",
            brand=None,
            retailer_category=None,
            retailer_subcategory=None,
            ingredients_text=None,
            labels=(),
            language=None,
            country=None,
        )
        prompt = build_batch_classifier_prompt(
            [("p1", prompt_input)], Methodology.PROTEIN_TRACKER
        )
        assert prompt.prompt_version == BATCH_CLASSIFIER_PROMPT_VERSION


# ---------------------------------------------------------------------------
# B. _coerce_wwf_result handles the full schema
# ---------------------------------------------------------------------------


class TestCoerceWWFFullSchema:
    """``_coerce_wwf_result`` reads every subgroup field and passes
    a complete payload to the Pydantic validator. Pre-Phase-WWF-A it
    dropped all subgroup fields, which caused every model response
    to fail validation."""

    def test_fg1_red_meat_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p1",
                "wwf_food_group": "FG1",
                "wwf_is_composite": False,
                "wwf_fg1_subgroup": "red_meat",
                "confidence": 0.95,
                "rationale": "beef",
            },
            methodology_value="wwf",
        )
        assert isinstance(result, WWFClassifierResult)
        assert result.wwf_food_group is WWFFoodGroup.FG1
        assert result.wwf_fg1_subgroup is WWFFG1Subgroup.RED_MEAT

    def test_fg2_dairy_animal_cheese_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p2",
                "wwf_food_group": "FG2",
                "wwf_is_composite": False,
                "wwf_fg2_kind": "dairy_animal",
                "wwf_fg2_dairy_class": "cheese",
                "confidence": 0.92,
                "rationale": "parmesan",
            },
            methodology_value="wwf",
        )
        assert result.wwf_food_group is WWFFoodGroup.FG2
        assert result.wwf_fg2_kind is WWFFG2Kind.DAIRY_ANIMAL
        assert result.wwf_fg2_dairy_class is WWFFG2DairyClass.CHEESE

    def test_fg2_plant_dairy_alternative_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p3",
                "wwf_food_group": "FG2",
                "wwf_is_composite": False,
                "wwf_fg2_kind": "dairy_alternative_plant",
                "wwf_fg2_dairy_class": None,
                "confidence": 0.9,
                "rationale": "oat milk",
            },
            methodology_value="wwf",
        )
        assert result.wwf_fg2_kind is WWFFG2Kind.DAIRY_ALTERNATIVE_PLANT
        assert result.wwf_fg2_dairy_class is None

    def test_fg3_animal_fat_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p4",
                "wwf_food_group": "FG3",
                "wwf_is_composite": False,
                "wwf_fg3_kind": "animal_based_fat",
                "confidence": 0.93,
                "rationale": "butter",
            },
            methodology_value="wwf",
        )
        assert result.wwf_fg3_kind is WWFFG3Subgroup.ANIMAL_BASED_FAT

    def test_fg5_whole_grain_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p5",
                "wwf_food_group": "FG5",
                "wwf_is_composite": False,
                "wwf_fg5_grain_kind": "whole_grain",
                "confidence": 0.9,
                "rationale": "wholewheat bread",
            },
            methodology_value="wwf",
        )
        assert result.wwf_fg5_grain_kind is WWFFG5GrainKind.WHOLE_GRAIN

    def test_fg7_animal_snack_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p6",
                "wwf_food_group": "FG7",
                "wwf_is_composite": False,
                "wwf_fg7_kind": "animal_based_snack",
                "confidence": 0.85,
                "rationale": "chocolate with milk",
            },
            methodology_value="wwf",
        )
        assert result.wwf_fg7_kind is WWFFG7SnackKind.ANIMAL_BASED_SNACK

    def test_composite_meat_based_is_accepted(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p7",
                "wwf_food_group": "FG1",
                "wwf_is_composite": True,
                "wwf_fg1_subgroup": "red_meat",
                "wwf_composite_step1_bucket": "meat_based",
                "confidence": 0.88,
                "rationale": "beef lasagne",
            },
            methodology_value="wwf",
        )
        assert result.wwf_is_composite is True
        assert (
            result.wwf_composite_step1_bucket
            is WWFCompositeStep1Bucket.MEAT_BASED
        )

    def test_fg4_no_subgroup_is_accepted(self) -> None:
        # FG4 has no required subgroup; all subgroup fields stay null.
        result = _coerce_wwf_result(
            {
                "id": "p8",
                "wwf_food_group": "FG4",
                "wwf_is_composite": False,
                "confidence": 0.97,
                "rationale": "tomato",
            },
            methodology_value="wwf",
        )
        assert result.wwf_food_group is WWFFoodGroup.FG4
        assert result.wwf_fg1_subgroup is None
        assert result.wwf_fg2_kind is None

    def test_out_of_scope_has_no_subgroups(self) -> None:
        result = _coerce_wwf_result(
            {
                "id": "p9",
                "wwf_food_group": "out_of_scope",
                "wwf_is_composite": False,
                "confidence": 0.95,
                "rationale": "tea",
            },
            methodology_value="wwf",
        )
        assert result.wwf_food_group is WWFFoodGroup.OUT_OF_SCOPE

    def test_missing_required_subgroup_still_raises(self) -> None:
        # Pre-Phase-WWF-A: ALL well-classified rows landed here.
        # Post-Phase-WWF-A: only model responses that genuinely
        # omit the required subgroup land here.
        with pytest.raises(Exception):  # noqa: B017
            _coerce_wwf_result(
                {
                    "id": "p10",
                    "wwf_food_group": "FG1",
                    "wwf_is_composite": False,
                    # NO subgroup → validator rejects.
                    "confidence": 0.95,
                    "rationale": "meat",
                },
                methodology_value="wwf",
            )


# ---------------------------------------------------------------------------
# C. Aliases — tolerant normalisation
# ---------------------------------------------------------------------------


class TestWWFSubgroupAliases:
    """The prompt asks for canonical values but the model
    occasionally returns shorthand (``fish`` instead of ``seafood``,
    ``beef`` instead of ``red_meat``, ``oat milk`` instead of
    ``dairy_alternative_plant``). Tolerant aliases let the parser
    accept those without forcing a parse-failed row."""

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("beef", "red_meat"),
            ("pork", "red_meat"),
            ("chicken", "poultry"),
            ("turkey", "poultry"),
            ("ham", "processed_meats_alternatives"),
            ("salami", "processed_meats_alternatives"),
            ("sausage", "processed_meats_alternatives"),
            ("fish", "seafood"),
            ("shellfish", "seafood"),
            ("fish_shellfish", "seafood"),
            ("pulses", "legumes"),
            ("lentils", "legumes"),
            ("chickpeas", "legumes"),
            ("nuts", "nuts_seeds"),
            ("seeds", "nuts_seeds"),
            ("tofu", "alternative_protein_sources"),
            ("tempeh", "alternative_protein_sources"),
            ("seitan", "alternative_protein_sources"),
            ("plant_meat", "meat_egg_seafood_alternatives"),
            ("egg_alternative", "meat_egg_seafood_alternatives"),
        ],
    )
    def test_fg1_subgroup_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG1_SUBGROUP_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("dairy", "dairy_animal"),
            ("plant_dairy", "dairy_alternative_plant"),
            ("dairy_alt", "dairy_alternative_plant"),
            ("milk_alternative", "dairy_alternative_plant"),
        ],
    )
    def test_fg2_kind_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG2_KIND_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("milk", "other"),
            ("yoghurt", "other"),
            ("yogurt", "other"),
            ("cream", "other"),
        ],
    )
    def test_fg2_dairy_class_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG2_DAIRY_CLASS_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("plant_fat", "plant_based_fat"),
            ("vegetable_oil", "plant_based_fat"),
            ("animal_fat", "animal_based_fat"),
            ("butter", "animal_based_fat"),
            ("lard", "animal_based_fat"),
        ],
    )
    def test_fg3_kind_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG3_KIND_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("wholegrain", "whole_grain"),
            ("wholemeal", "whole_grain"),
            ("brown", "whole_grain"),
            ("white", "refined_grain"),
            ("refined", "refined_grain"),
        ],
    )
    def test_fg5_grain_kind_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG5_GRAIN_KIND_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("plant_snack", "plant_based_snack"),
            ("vegan_snack", "plant_based_snack"),
            ("animal_snack", "animal_based_snack"),
            ("dairy_snack", "animal_based_snack"),
        ],
    )
    def test_fg7_kind_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_FG7_KIND_ALIASES.get(alias) == canonical

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("meat_composite", "meat_based"),
            ("seafood_composite", "seafood_based"),
            ("fish_based", "seafood_based"),
            ("vegetarian_composite", "vegetarian"),
            ("vegan_composite", "vegan"),
            ("plant_composite", "vegan"),
        ],
    )
    def test_composite_bucket_aliases(
        self, alias: str, canonical: str
    ) -> None:
        assert _WWF_COMPOSITE_BUCKET_ALIASES.get(alias) == canonical

    def test_alias_returns_canonical_in_coerce(self) -> None:
        # End-to-end: "beef" alias survives the coercer to
        # WWFFG1Subgroup.RED_MEAT.
        result = _coerce_wwf_result(
            {
                "id": "p1",
                "wwf_food_group": "FG1",
                "wwf_is_composite": False,
                "wwf_fg1_subgroup": "beef",  # alias, not canonical
                "confidence": 0.9,
                "rationale": "beef",
            },
            methodology_value="wwf",
        )
        assert result.wwf_fg1_subgroup is WWFFG1Subgroup.RED_MEAT


# ---------------------------------------------------------------------------
# D. Methodology document
# ---------------------------------------------------------------------------


_DOC_PATH = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "methodologies"
    / "wwf-classification-rules.md"
)


class TestMethodologyDocument:
    def test_doc_file_exists(self) -> None:
        assert _DOC_PATH.exists(), (
            f"methodology doc missing at {_DOC_PATH}"
        )

    def test_doc_mentions_key_rules(self) -> None:
        content = _DOC_PATH.read_text(encoding="utf-8")
        # Key rules from XLSX Tab 7 / brief Part B.
        for needle in (
            "Composite vs whole product",
            "meat_based",
            "seafood_based",
            "vegetarian",
            "vegan",
            "out_of_scope",
            "Butter",  # FG3 animal fat, not FG2
            "Sweetcorn",  # FG4 vs mature corn FG5
            "Coconut",  # flesh FG1 vs milk FG2
            "Pet food",  # Altera implementation decision
            "Pears in rum",  # XLSX Tab 7 typo handling
            "batch_wwf_v2",  # the new prompt version
        ):
            assert needle in content, (
                f"methodology doc missing key topic: {needle!r}"
            )

    def test_doc_documents_xlsx_typo(self) -> None:
        # The XLSX Tab 7 typo "FG3 (Fruits & Vegetables)" must be
        # explicitly documented — the methodology team confirmed
        # the intent is FG4.
        content = _DOC_PATH.read_text(encoding="utf-8")
        assert "typo" in content.lower()
        assert "FG4" in content
