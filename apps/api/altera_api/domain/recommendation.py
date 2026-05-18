"""Recommendation domain models (Phase 25A).

Recommendations are assembled deterministically from run/coverage data at
report time — no LLM involvement. They are directional and methodology-aware;
they do not carry numeric impact estimates or commercial fields.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class RecommendationActionType(StrEnum):
    """The set of action types the engine can emit."""

    INCREASE_PLANT_CORE_SHARE = "increase_plant_core_share"
    REDUCE_ANIMAL_CORE_DEPENDENCY = "reduce_animal_core_dependency"
    IMPROVE_COMPOSITE_BREAKDOWN = "improve_composite_breakdown"
    IMPROVE_DATA_QUALITY = "improve_data_quality"
    ENRICH_MISSING_NUTRITION = "enrich_missing_nutrition"
    REVIEW_HIGH_IMPACT_UNKNOWNS = "review_high_impact_unknowns"
    COLLECT_STEP2_INGREDIENT_DATA = "collect_step2_ingredient_data"
    PROMOTE_LEGUME_PRODUCTS = "promote_legume_products"
    REFORMULATE_COMPOSITES = "reformulate_composites"
    REPLACE_OR_REBALANCE_CATEGORY = "replace_or_rebalance_category"
    CREATE_CATEGORY_TARGET = "create_category_target"


class RecommendationPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendationStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    ARCHIVED = "archived"


class RecommendationCategory(StrEnum):
    PT_PROTEIN_SHIFT = "pt_protein_shift"
    WWF_FOOD_GROUP = "wwf_food_group"
    DATA_QUALITY = "data_quality"
    COMPOSITE_QUALITY = "composite_quality"
    ENRICHMENT = "enrichment"


class Recommendation(BaseModel):
    """A single deterministic recommendation item included in a ReportDocument.

    Fields mirror what the frontend `RecommendationsSection` renders.
    No commercial, revenue, or supplier fields appear here.
    """

    action_type: str
    category: str
    title: str
    description: str
    rationale: str
    expected_direction: str
    priority: str
    confidence: str
    evidence: list[str]
    status: str
    caveats: list[str]
    client_facing: bool
