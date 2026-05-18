# Recommendation Action Taxonomy (Phase 25A)

This document defines the static action taxonomy used by the deterministic
recommendation engine. Each entry is keyed by `action_type` and referenced
directly in `apps/api/altera_api/recommendations/taxonomy.py`.

**Phase 25A constraints:**

- All recommendations are deterministic. No LLM is called.
- No numeric impact estimates are generated.
- Recommendations are directional only (`expected_direction`).
- No scenario modelling is performed.
- No unsupported health or nutrition claims are made.

---

## action_type reference

### `increase_plant_core_share`

| Field | Value |
|-------|-------|
| Category | `pt_protein_shift` |
| Methodologies | Protein Tracker |
| Trigger | `plant_share_pct < 40 %` |
| Priority | High |
| Client-facing | Yes |

Increase the share of plant-based core protein (legumes, nuts, seeds,
plant-based alternatives) in the product range.

**Expected direction:** Likely increases plant-source protein share,
improving the Protein Tracker plant ratio.

---

### `reduce_animal_core_dependency`

| Field | Value |
|-------|-------|
| Category | `pt_protein_shift` |
| Methodologies | Protein Tracker |
| Trigger | (not yet auto-triggered; reserved for future rule) |
| Priority | Medium |
| Client-facing | Yes |

Review the animal-core product range for categories where plant-based
alternatives exist and where reduction or substitution is feasible.

---

### `improve_composite_breakdown`

| Field | Value |
|-------|-------|
| Category | `composite_quality` |
| Methodologies | Protein Tracker |
| Trigger | Composite protein pool ≥ 30 % of total in-scope protein |
| Priority | Medium |
| Client-facing | Yes |

Improve classification of composite products by providing per-product
ingredient split data (plant % / animal % of protein).

**Expected direction:** Improves methodological accuracy; removes reliance
on the 50/50 default split.

---

### `improve_data_quality`

| Field | Value |
|-------|-------|
| Category | `data_quality` |
| Methodologies | Both |
| Trigger | Uncertainty level = high; or AI share ≥ 30 %; or branded composites present (WWF) |
| Priority | Critical (high uncertainty) / Medium (AI share) / Low (branded composites) |
| Client-facing | Yes |

Address data quality gaps before setting or reporting against targets.

---

### `enrich_missing_nutrition`

| Field | Value |
|-------|-------|
| Category | `enrichment` |
| Methodologies | Protein Tracker |
| Trigger | `products_with_missing_protein > 0` |
| Priority | Medium |
| Client-facing | Yes |

Provide label-level protein % for products missing this field, or apply
stored enrichment records in the next calculation run.

---

### `review_high_impact_unknowns`

| Field | Value |
|-------|-------|
| Category | `data_quality` |
| Methodologies | Both |
| Trigger | Unknown product share ≥ 5 % of total products |
| Priority | High |
| Client-facing | Yes |

Manually review unknown-classified products to assign them to a
methodology group.

---

### `collect_step2_ingredient_data`

| Field | Value |
|-------|-------|
| Category | `composite_quality` |
| Methodologies | WWF |
| Trigger | Own-brand composites exist and `step2_applied_count < own_brand_composite_count` |
| Priority | High |
| Client-facing | Yes |

Upload WWF Step 2 ingredient data for own-brand composite products to
replace whole-product-weight Step 1 attribution with ingredient-level
food group attribution.

---

### `promote_legume_products`

| Field | Value |
|-------|-------|
| Category | `wwf_food_group` |
| Methodologies | WWF |
| Trigger | FG1 share > PHD reference share (16 %) |
| Priority | Medium |
| Client-facing | Yes |

Consider expanding the legume and plant-protein assortment within FG1 to
diversify the protein-rich food group towards plant sources.

---

### `reformulate_composites`

| Field | Value |
|-------|-------|
| Category | `composite_quality` |
| Methodologies | Both |
| Trigger | (not yet auto-triggered; reserved for future rule) |
| Priority | Medium |
| Client-facing | Yes |

Explore reformulation opportunities for composite products to reduce
animal-source ingredients and increase plant-source content.

---

### `replace_or_rebalance_category`

| Field | Value |
|-------|-------|
| Category | `wwf_food_group` |
| Methodologies | Both |
| Trigger | (not yet auto-triggered; reserved for future rule) |
| Priority | Medium |
| Client-facing | Yes |

Review dominant food groups or subgroups for rebalancing opportunities
towards Planetary Health Diet reference proportions.

---

### `create_category_target`

| Field | Value |
|-------|-------|
| Category | `data_quality` |
| Methodologies | Both |
| Trigger | Uncertainty level = high |
| Priority | Low |
| Client-facing | No (Altera only) |

Once data quality is sufficient (low uncertainty), work with the Altera
methodology team to set a category-level target for the next reporting
period.

---

## Trigger thresholds

| Signal | Threshold | Action type(s) triggered |
|--------|-----------|--------------------------|
| PT plant share | < 40 % | `increase_plant_core_share` |
| PT composite protein pool | ≥ 30 % of total | `improve_composite_breakdown` |
| PT / WWF unknown product share | ≥ 5 % of total | `review_high_impact_unknowns` |
| PT / WWF AI classification share | ≥ 30 % of total | `improve_data_quality` |
| PT missing protein | > 0 products | `enrich_missing_nutrition` |
| WWF Step 2 gap | own-brand composites without ingredient data | `collect_step2_ingredient_data` |
| WWF FG1 share | > PHD reference (16 %) | `promote_legume_products` |
| WWF branded composites | > 0 products | `improve_data_quality` (low) |
| Uncertainty level | high | `improve_data_quality` (critical) + `create_category_target` (low) |

## Phase 25A limitations

- **No numeric impact estimates.** `expected_direction` is directional only.
- **No LLM-generated recommendations.** All text is static and deterministic.
- **No scenario modelling.** Future phases may add "what if we shift X by Y".
- **No per-product recommendations.** Recommendations are run-level only.
- **`status` is always `draft`.** Accepted/dismissed lifecycle is Phase 25B+.

## What this module never does

- It never makes unsupported health or nutrition claims.
- It never references commercial fields (revenue, margin, cost, contract terms).
- It never names specific products or suppliers.
- It never call an LLM or external API.
- It never exceed the evidence it can derive from the run summary and coverage section.
