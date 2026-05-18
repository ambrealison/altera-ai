"""Engine behaviour: pass-through, single match, agreed-multi, collision."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from altera_api.domain.common import ClassificationSource, Methodology
from altera_api.domain.protein_tracker import ProteinTrackerGroup
from altera_api.domain.wwf import (
    WWFCompositeStep1Bucket,
    WWFFG1Subgroup,
    WWFFG2Subgroup,
    WWFFoodGroup,
)
from altera_api.rules.engine import (
    PTMatched,
    PTPassThrough,
    PTRuleCollision,
    WWFMatched,
    WWFPassThrough,
    WWFRuleCollision,
    classify_protein_tracker,
    classify_wwf,
)
from altera_api.rules.schema import (
    ConditionNode,
    PTRule,
    WWFRule,
    WWFRuleCategory,
)


# --------------------------------------------------------------------------
# PT engine
# --------------------------------------------------------------------------
class TestProteinTrackerEngine:
    def _rule(
        self,
        rule_id: str,
        category: ProteinTrackerGroup,
        needle: str,
        *,
        priority: int = 100,
        exclude: ConditionNode | None = None,
    ) -> PTRule:
        return PTRule(
            id=rule_id,
            methodology=Methodology.PROTEIN_TRACKER,
            category=category,
            priority=priority,
            match=ConditionNode(product_name_contains=(needle,)),
            exclude=exclude,
        )

    def test_pass_through_when_no_rule_matches(self, make_pt_product, now: datetime) -> None:
        rules = [self._rule("pt.r.beef", ProteinTrackerGroup.ANIMAL_CORE, "beef")]
        product = make_pt_product(name="Red Lentil Soup")
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTPassThrough)
        assert verdict.product_id == product.id

    def test_single_match(self, make_pt_product, now: datetime) -> None:
        rules = [self._rule("pt.r.lentil", ProteinTrackerGroup.PLANT_BASED_CORE, "lentil")]
        product = make_pt_product(name="Red Lentil Soup")
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.classification.pt_group is ProteinTrackerGroup.PLANT_BASED_CORE
        assert verdict.classification.source is ClassificationSource.DETERMINISTIC
        assert verdict.classification.confidence == Decimal("1")
        assert verdict.classification.rule_id == "pt.r.lentil"
        assert verdict.fired_rule_ids == ("pt.r.lentil",)

    def test_two_rules_agreeing_concatenate_ids(self, make_pt_product, now: datetime) -> None:
        rules = [
            self._rule("pt.r.lentil", ProteinTrackerGroup.PLANT_BASED_CORE, "lentil"),
            self._rule("pt.r.soup", ProteinTrackerGroup.PLANT_BASED_CORE, "soup", priority=200),
        ]
        product = make_pt_product(name="Red Lentil Soup")
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTMatched)
        assert verdict.fired_rule_ids == ("pt.r.lentil", "pt.r.soup")
        assert verdict.classification.rule_id == "pt.r.lentil,pt.r.soup"

    def test_rule_collision(self, make_pt_product, now: datetime) -> None:
        rules = [
            self._rule("pt.r.lentil", ProteinTrackerGroup.PLANT_BASED_CORE, "lentil"),
            self._rule("pt.r.soup", ProteinTrackerGroup.COMPOSITE_PRODUCTS, "soup"),
        ]
        product = make_pt_product(name="Red Lentil Soup")
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTRuleCollision)
        assert set(verdict.conflicting_rule_ids) == {"pt.r.lentil", "pt.r.soup"}
        assert set(verdict.conflicting_categories) == {
            ProteinTrackerGroup.PLANT_BASED_CORE,
            ProteinTrackerGroup.COMPOSITE_PRODUCTS,
        }

    def test_exclude_blocks_match(self, make_pt_product, now: datetime) -> None:
        rules = [
            self._rule(
                "pt.r.cheese",
                ProteinTrackerGroup.ANIMAL_CORE,
                "cheese",
                exclude=ConditionNode(labels_contains=("vegan",)),
            )
        ]
        product = make_pt_product(name="Vegan Cheese", labels=("vegan",))
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTPassThrough)

    def test_priority_ordering_does_not_affect_outcome_when_agreeing(
        self, make_pt_product, now: datetime
    ) -> None:
        rules = [
            self._rule("pt.r.b", ProteinTrackerGroup.PLANT_BASED_CORE, "lentil", priority=200),
            self._rule("pt.r.a", ProteinTrackerGroup.PLANT_BASED_CORE, "soup", priority=100),
        ]
        product = make_pt_product(name="Lentil Soup")
        verdict = classify_protein_tracker(product, rules, now=now)
        assert isinstance(verdict, PTMatched)
        # Sorted by (priority asc, id asc) — pt.r.a fires first
        assert verdict.fired_rule_ids == ("pt.r.a", "pt.r.b")


# --------------------------------------------------------------------------
# WWF engine
# --------------------------------------------------------------------------
class TestWWFEngine:
    def _rule(
        self,
        rule_id: str,
        category: WWFRuleCategory,
        needle: str,
        *,
        priority: int = 100,
        exclude: ConditionNode | None = None,
    ) -> WWFRule:
        return WWFRule(
            id=rule_id,
            methodology=Methodology.WWF,
            category=category,
            priority=priority,
            match=ConditionNode(product_name_contains=(needle,)),
            exclude=exclude,
        )

    def test_single_match_produces_wwf_classification(
        self, make_wwf_product, now: datetime
    ) -> None:
        rules = [
            self._rule(
                "wwf.r.beef",
                WWFRuleCategory(
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                ),
                "beef",
            )
        ]
        product = make_wwf_product(name="Beef Mince 500g")
        verdict = classify_wwf(product, rules, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.wwf_food_group is WWFFoodGroup.FG1
        assert verdict.classification.fg1_subgroup is WWFFG1Subgroup.RED_MEAT
        assert verdict.classification.source is ClassificationSource.DETERMINISTIC

    def test_wwf_composite_rule_produces_classification_with_bucket(
        self, make_wwf_product, now: datetime
    ) -> None:
        rules = [
            self._rule(
                "wwf.r.lasagna",
                WWFRuleCategory(
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                    wwf_is_composite=True,
                    wwf_composite_step1_bucket=WWFCompositeStep1Bucket.MEAT_BASED,
                ),
                "lasagna",
            )
        ]
        product = make_wwf_product(name="Beef Lasagna")
        verdict = classify_wwf(product, rules, now=now)
        assert isinstance(verdict, WWFMatched)
        assert verdict.classification.composite_step1_bucket is WWFCompositeStep1Bucket.MEAT_BASED
        assert verdict.classification.wwf_is_composite is True

    def test_wwf_rule_collision(self, make_wwf_product, now: datetime) -> None:
        rules = [
            self._rule(
                "wwf.r.beef",
                WWFRuleCategory(
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                ),
                "beef",
            ),
            self._rule(
                "wwf.r.cheese",
                WWFRuleCategory(
                    wwf_food_group=WWFFoodGroup.FG2,
                    wwf_fg2_subgroup=WWFFG2Subgroup.CHEESE,
                ),
                "burger",
            ),
        ]
        product = make_wwf_product(name="Beef Cheese Burger")
        verdict = classify_wwf(product, rules, now=now)
        assert isinstance(verdict, WWFRuleCollision)
        assert len(verdict.conflicting_categories) == 2

    def test_wwf_pass_through(self, make_wwf_product, now: datetime) -> None:
        rules = [
            self._rule(
                "wwf.r.beef",
                WWFRuleCategory(
                    wwf_food_group=WWFFoodGroup.FG1,
                    wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
                ),
                "beef",
            )
        ]
        product = make_wwf_product(name="Mystery Product")
        verdict = classify_wwf(product, rules, now=now)
        assert isinstance(verdict, WWFPassThrough)


# --------------------------------------------------------------------------
# Methodology isolation
# --------------------------------------------------------------------------
def test_pt_engine_ignores_wwf_rules(make_pt_product, now: datetime) -> None:
    # WWFRules in the input list should be silently ignored by PT classifier.
    wwf_rules_list: list = [
        WWFRule(
            id="wwf.r.beef",
            methodology=Methodology.WWF,
            category=WWFRuleCategory(
                wwf_food_group=WWFFoodGroup.FG1,
                wwf_fg1_subgroup=WWFFG1Subgroup.RED_MEAT,
            ),
            match=ConditionNode(product_name_contains=("beef",)),
        )
    ]
    product = make_pt_product(name="Beef Mince")
    verdict = classify_protein_tracker(product, wwf_rules_list, now=now)
    assert isinstance(verdict, PTPassThrough)
