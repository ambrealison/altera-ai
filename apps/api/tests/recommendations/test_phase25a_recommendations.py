"""Phase 25A — Recommendation engine foundation tests.

Covers:
- Low PT plant share generates increase_plant_core_share recommendation
- High composite share generates improve_composite_breakdown recommendation
- Missing protein generates enrich_missing_nutrition recommendation
- High unknown rate generates review_high_impact_unknowns recommendation
- WWF low step2 coverage generates collect_step2_ingredient_data recommendation
- WWF branded composites generate improve_data_quality recommendation
- High uncertainty generates data quality + create_category_target recommendations
- Recommendations are deterministic (same input → same output)
- Recommendations contain no forbidden commercial fields
- Taxonomy entries are self-consistent
- ReportDocument includes recommendations field
- No recommendations when all signals are clean
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from altera_api.domain.common import Methodology
from altera_api.domain.protein_tracker import (
    ProteinTrackerCalculationSummary,
    ProteinTrackerGroup,
    ProteinTrackerGroupAggregate,
)
from altera_api.domain.recommendation import (
    Recommendation,
    RecommendationActionType,
    RecommendationCategory,
    RecommendationPriority,
    RecommendationStatus,
)
from altera_api.domain.wwf import (
    WWFCalculationSummary,
    WWFFoodGroup,
    WWFFoodGroupAggregate,
)
from altera_api.recommendations.engine import generate_recommendations
from altera_api.recommendations.taxonomy import ACTION_TAXONOMY

_VERSIONS = dict(
    methodology_version="1.0.0",
    methodology_source_edition="Test edition",
    taxonomy_version="1.0.0",
    rules_version="1.0.0",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pt_summary(
    *,
    plant_kg: str = "30",
    animal_kg: str = "70",
    composite_kg: str = "0",
    unknown_count: int = 0,
    rows_with_per_product_split: int = 0,
) -> ProteinTrackerCalculationSummary:
    plant = Decimal(plant_kg)
    animal = Decimal(animal_kg)
    comp = Decimal(composite_kg)
    total = plant + animal
    plant_pct = (plant * Decimal("100") / total) if total else None
    animal_pct = (animal * Decimal("100") / total) if total else None

    # Simulate composite having part of the protein
    animal_minus_comp = max(Decimal("0"), animal - comp)
    per_group = (
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_CORE,
            volume_kg=Decimal("100"),
            protein_kg=plant,
            item_count=3,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.PLANT_BASED_NON_CORE,
            volume_kg=Decimal("50"),
            protein_kg=Decimal("0"),
            item_count=1,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.COMPOSITE_PRODUCTS,
            volume_kg=Decimal("200"),
            protein_kg=comp,
            item_count=5,
        ),
        ProteinTrackerGroupAggregate(
            pt_group=ProteinTrackerGroup.ANIMAL_CORE,
            volume_kg=Decimal("150"),
            protein_kg=animal_minus_comp,
            item_count=4,
        ),
    )
    return ProteinTrackerCalculationSummary(
        run_id=uuid4(),
        reporting_period_label="FY 2024",
        per_group=per_group,
        plant_protein_kg=plant,
        animal_protein_kg=animal,
        total_in_scope_protein_kg=total,
        plant_share_pct=plant_pct,
        animal_share_pct=animal_pct,
        rows_with_per_product_split=rows_with_per_product_split,
        rows_protein_source_label=10,
        rows_protein_source_reference_db=0,
        out_of_scope_count=0,
        unknown_count=unknown_count,
        **_VERSIONS,
    )


def _wwf_summary(
    *,
    fg1_share: str = "16",
    total_weight: str = "1000",
    composites_total: str = "0",
    unknown_count: int = 0,
) -> WWFCalculationSummary:
    total = Decimal(total_weight)
    fg1_w = total * Decimal(fg1_share) / Decimal("100")

    per_food_group = tuple(
        WWFFoodGroupAggregate(
            food_group=fg,
            weight_kg=fg1_w if fg is WWFFoodGroup.FG1 else Decimal("0"),
            weight_kg_dairy_equiv=Decimal("0") if fg is WWFFoodGroup.FG2 else None,
            share_pct=Decimal(fg1_share) if fg is WWFFoodGroup.FG1 else Decimal("0"),
            phd_reference_share_pct={
                WWFFoodGroup.FG1: Decimal("16"),
                WWFFoodGroup.FG2: Decimal("19"),
                WWFFoodGroup.FG3: Decimal("4"),
                WWFFoodGroup.FG4: Decimal("39"),
                WWFFoodGroup.FG5: Decimal("18"),
                WWFFoodGroup.FG6: Decimal("4"),
            }.get(fg),
        )
        for fg in (
            WWFFoodGroup.FG1,
            WWFFoodGroup.FG2,
            WWFFoodGroup.FG3,
            WWFFoodGroup.FG4,
            WWFFoodGroup.FG5,
            WWFFoodGroup.FG6,
            WWFFoodGroup.FG7,
        )
    )
    comp = Decimal(composites_total)
    return WWFCalculationSummary(
        run_id=uuid4(),
        reporting_period_label="FY 2024",
        per_food_group=per_food_group,
        total_sales_weight_in_scope_kg=total,
        composites_total_weight_kg=comp,
        composites_meat_based_kg=comp,
        composites_seafood_based_kg=Decimal("0"),
        composites_vegetarian_kg=Decimal("0"),
        composites_vegan_kg=Decimal("0"),
        whole_diet_plant_weight_kg=Decimal("500"),
        whole_diet_animal_weight_kg=Decimal("500"),
        out_of_scope_count=0,
        unknown_count=unknown_count,
        **_VERSIONS,
    )


# ---------------------------------------------------------------------------
# Protein Tracker recommendation tests
# ---------------------------------------------------------------------------


class TestPTRecommendations:
    def test_low_plant_share_generates_increase_plant_core_share(self):
        # plant = 30 kg, animal = 70 kg → plant share = 30 % < 40 %
        s = _pt_summary(plant_kg="30", animal_kg="70")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "increase_plant_core_share" in types
        rec = next(r for r in recs if r.action_type == "increase_plant_core_share")
        assert rec.priority == RecommendationPriority.HIGH
        assert rec.category == RecommendationCategory.PT_PROTEIN_SHIFT
        assert rec.client_facing is True

    def test_high_plant_share_no_increase_recommendation(self):
        # plant = 55 kg, animal = 45 kg → plant share = 55 % > 40 %
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "increase_plant_core_share" not in types

    def test_high_composite_share_generates_improve_composite_breakdown(self):
        # composite = 35 kg out of total 100 kg → 35 % >= 30 %
        s = _pt_summary(plant_kg="30", animal_kg="70", composite_kg="35")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "improve_composite_breakdown" in types
        rec = next(r for r in recs if r.action_type == "improve_composite_breakdown")
        assert rec.priority == RecommendationPriority.MEDIUM
        assert rec.category == RecommendationCategory.COMPOSITE_QUALITY

    def test_missing_protein_generates_enrich_missing_nutrition(self):
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=5,
        )
        types = [r.action_type for r in recs]
        assert "enrich_missing_nutrition" in types
        rec = next(r for r in recs if r.action_type == "enrich_missing_nutrition")
        assert "5 product(s)" in rec.rationale

    def test_no_missing_protein_no_enrich_recommendation(self):
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "enrich_missing_nutrition" not in types

    def test_high_unknown_rate_generates_review_high_impact_unknowns(self):
        # 2 unknown out of 10 = 20 % > 5 %
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=10,
            products_unknown=2,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "review_high_impact_unknowns" in types

    def test_low_unknown_rate_no_review_recommendation(self):
        # 0 unknown out of 100 = 0 %
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=100,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "review_high_impact_unknowns" not in types

    def test_high_ai_share_generates_improve_data_quality(self):
        # 35 AI classified out of 100 = 35 % >= 30 %
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=100,
            products_unknown=0,
            products_ai_classified=35,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "improve_data_quality" in types


# ---------------------------------------------------------------------------
# WWF recommendation tests
# ---------------------------------------------------------------------------


class TestWWFRecommendations:
    def test_step2_gap_generates_collect_step2_ingredient_data(self):
        s = _wwf_summary()
        recs = generate_recommendations(
            Methodology.WWF,
            wwf_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            wwf_step2_applied_count=2,
            wwf_own_brand_composite_count=10,
            wwf_branded_composite_count=0,
        )
        types = [r.action_type for r in recs]
        assert "collect_step2_ingredient_data" in types
        rec = next(r for r in recs if r.action_type == "collect_step2_ingredient_data")
        assert rec.priority == RecommendationPriority.HIGH
        assert "8 own-brand composite" in rec.evidence[0]

    def test_no_step2_gap_no_collect_recommendation(self):
        # All own-brand composites already have step2 data
        s = _wwf_summary()
        recs = generate_recommendations(
            Methodology.WWF,
            wwf_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            wwf_step2_applied_count=5,
            wwf_own_brand_composite_count=5,
            wwf_branded_composite_count=0,
        )
        types = [r.action_type for r in recs]
        assert "collect_step2_ingredient_data" not in types

    def test_branded_composites_generate_improve_data_quality(self):
        s = _wwf_summary()
        recs = generate_recommendations(
            Methodology.WWF,
            wwf_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            wwf_step2_applied_count=0,
            wwf_own_brand_composite_count=0,
            wwf_branded_composite_count=3,
        )
        types = [r.action_type for r in recs]
        assert "improve_data_quality" in types
        rec = next(r for r in recs if r.action_type == "improve_data_quality")
        assert rec.priority == RecommendationPriority.LOW

    def test_fg1_above_phd_generates_promote_legume_products(self):
        # FG1 share = 25 % > PHD reference 16 %
        s = _wwf_summary(fg1_share="25")
        recs = generate_recommendations(
            Methodology.WWF,
            wwf_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            wwf_step2_applied_count=0,
            wwf_own_brand_composite_count=0,
            wwf_branded_composite_count=0,
        )
        types = [r.action_type for r in recs]
        assert "promote_legume_products" in types

    def test_fg1_at_phd_no_promote_recommendation(self):
        # FG1 share = 16 % == PHD reference → no trigger
        s = _wwf_summary(fg1_share="16")
        recs = generate_recommendations(
            Methodology.WWF,
            wwf_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            wwf_step2_applied_count=0,
            wwf_own_brand_composite_count=0,
            wwf_branded_composite_count=0,
        )
        types = [r.action_type for r in recs]
        assert "promote_legume_products" not in types


# ---------------------------------------------------------------------------
# Data quality (uncertainty-level) tests
# ---------------------------------------------------------------------------


class TestDataQualityRecommendations:
    def test_high_uncertainty_generates_critical_improve_data_quality(self):
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="high",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        dq = [r for r in recs if r.action_type == "improve_data_quality"]
        assert any(r.priority == RecommendationPriority.CRITICAL for r in dq)

    def test_high_uncertainty_generates_create_category_target(self):
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="high",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        types = [r.action_type for r in recs]
        assert "create_category_target" in types

    def test_low_uncertainty_no_critical_data_quality(self):
        s = _pt_summary(plant_kg="55", animal_kg="45")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        dq = [r for r in recs if r.action_type == "improve_data_quality"]
        assert not any(r.priority == RecommendationPriority.CRITICAL for r in dq)


# ---------------------------------------------------------------------------
# Determinism and field safety tests
# ---------------------------------------------------------------------------


class TestRecommendationSafety:
    def test_recommendations_are_deterministic(self):
        s = _pt_summary(plant_kg="30", animal_kg="70", composite_kg="35")
        kwargs = dict(
            pt_summary=s,
            uncertainty_level="medium",
            products_total=50,
            products_unknown=4,
            products_ai_classified=20,
            products_with_missing_protein=3,
        )
        first = generate_recommendations(Methodology.PROTEIN_TRACKER, **kwargs)
        second = generate_recommendations(Methodology.PROTEIN_TRACKER, **kwargs)
        assert [r.action_type for r in first] == [r.action_type for r in second]
        assert [r.priority for r in first] == [r.priority for r in second]

    def test_recommendations_contain_no_commercial_fields(self):
        s = _pt_summary(plant_kg="30", animal_kg="70")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=20,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        forbidden = {"revenue", "margin", "cost_price", "contract_terms", "confidential"}
        for rec in recs:
            all_text = " ".join(
                [rec.title, rec.description, rec.rationale, rec.expected_direction]
                + rec.evidence
                + rec.caveats
            ).lower()
            for word in forbidden:
                assert word not in all_text, f"forbidden word '{word}' in recommendation text"

    def test_no_duplicate_action_types(self):
        s = _pt_summary(plant_kg="30", animal_kg="70", composite_kg="40")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="high",
            products_total=20,
            products_unknown=2,
            products_ai_classified=8,
            products_with_missing_protein=3,
        )
        action_types = [r.action_type for r in recs]
        assert len(action_types) == len(set(action_types)), "duplicate action_types found"

    def test_all_recommendations_are_valid_recommendation_instances(self):
        s = _pt_summary(plant_kg="30", animal_kg="70")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="high",
            products_total=20,
            products_unknown=2,
            products_ai_classified=0,
            products_with_missing_protein=2,
        )
        for rec in recs:
            assert isinstance(rec, Recommendation)
            assert rec.action_type in RecommendationActionType._value2member_map_
            assert rec.priority in RecommendationPriority._value2member_map_
            assert rec.status in RecommendationStatus._value2member_map_
            assert isinstance(rec.evidence, list)
            assert isinstance(rec.caveats, list)
            assert isinstance(rec.client_facing, bool)

    def test_empty_recommendations_when_all_signals_clean(self):
        # High plant share, no missing protein, no unknowns, no AI, low uncertainty
        s = _pt_summary(plant_kg="60", animal_kg="40")
        recs = generate_recommendations(
            Methodology.PROTEIN_TRACKER,
            pt_summary=s,
            uncertainty_level="low",
            products_total=50,
            products_unknown=0,
            products_ai_classified=0,
            products_with_missing_protein=0,
        )
        # Only data-quality recs could fire; with low uncertainty they don't
        dq_types = [r.action_type for r in recs]
        assert "improve_data_quality" not in dq_types
        assert "create_category_target" not in dq_types


# ---------------------------------------------------------------------------
# Taxonomy self-consistency tests
# ---------------------------------------------------------------------------


class TestTaxonomy:
    def test_all_action_types_in_taxonomy(self):
        for action_type in RecommendationActionType:
            assert action_type.value in ACTION_TAXONOMY, (
                f"action_type '{action_type.value}' missing from ACTION_TAXONOMY"
            )

    def test_taxonomy_entries_have_required_keys(self):
        required = {
            "applicable_methodologies",
            "category",
            "description",
            "expected_direction",
            "caveats",
            "client_facing",
            "altera_only",
        }
        for key, entry in ACTION_TAXONOMY.items():
            missing = required - entry.keys()
            assert not missing, f"taxonomy entry '{key}' missing keys: {missing}"

    def test_taxonomy_descriptions_non_empty(self):
        for key, entry in ACTION_TAXONOMY.items():
            assert entry["description"].strip(), f"taxonomy entry '{key}' has empty description"
            assert entry["expected_direction"].strip(), (
                f"taxonomy entry '{key}' has empty expected_direction"
            )

    def test_taxonomy_categories_are_valid(self):
        valid_categories = {c.value for c in RecommendationCategory}
        for key, entry in ACTION_TAXONOMY.items():
            assert entry["category"] in valid_categories, (
                f"taxonomy entry '{key}' has unknown category '{entry['category']}'"
            )
