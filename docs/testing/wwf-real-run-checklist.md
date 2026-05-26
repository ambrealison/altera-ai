# WWF real-run validation checklist

Use this checklist when an operator runs the WWF classification pipeline
end-to-end against a real (non-fixture) retailer CSV. Each step has a
verification line so a regression caught in production matches the
exact failure surface.

The checklist covers two scenarios: a WWF-only project (Section A) and
a PT+WWF project (Section B). Section C captures the metrics to record
in the run log so we can track WWF accuracy drift over time.

## Pre-flight

- [ ] Branch you are testing is deployed to Render and Vercel.
- [ ] WWF guard fixture audit (`scripts/evaluate_wwf_classification.py`)
      passes at strict accuracy ≥ 0.97 against the curated 110-case
      fixture (regression gate).
- [ ] `OPENAI_API_KEY` is set in the Render environment.
- [ ] `ALTERA_AI_CLASSIFIER_ENABLED=true` and
      `ALTERA_AI_PROVIDER=openai` are set.

## A. WWF-only project

1. Create a new project with `methodologies_enabled=["wwf"]`.
2. Upload a 100-line WWF CSV with the required columns:
   `external_product_id`, `product_name`, `weight_per_item_kg`,
   `items_sold`, `retail_channel`, `is_own_brand`.
3. Verify the preview-mapping response (Phase WWF-E):
   - `missing_required_wwf` is empty.
   - `missing_required_pt` is empty (PT not enabled).
4. Confirm the wizard renders only WWF steps (Phase WWF-G):
   - No NEVO step.
   - No Nutrition Validation step.
   - Classification step shows "Catégorisation WWF".
5. Click "Lancer la catégorisation WWF" → confirm the job runs
   end-to-end without falling back to manual review for >5% of rows.
6. Verify after completion:
   - [ ] `unknown_rate` < 1% (readable-name fallback should catch
         the rest — Phase WWF-D).
   - [ ] `schema_failures` near 0 (Phase WWF-A/B/C contract).
   - [ ] Every classified row has `wwf_food_group ∈ {FG1..FG7,
         out_of_scope}`.
   - [ ] Every FG1 row has `wwf_fg1_subgroup`.
   - [ ] Every FG2 row has `wwf_fg2_subgroup`.
   - [ ] Every FG3 row has `wwf_fg3_subgroup`.
   - [ ] Every FG5 row has `wwf_fg5_grain_kind`.
   - [ ] Every FG7 row has `wwf_fg7_snack_kind`.
   - [ ] Every composite row has `wwf_composite_step1_bucket ∈
         {meat_based, seafood_based, vegetarian, vegan}`.
7. Click "Voir la validation WWF" → the validation table opens with
   `?methodology=wwf` and renders the WWF view: food group + subgroup
   + composite + bucket columns (Phase WWF-I).
8. Confirm the calculation step shows "Calcul WWF" copy, not protein
   wording (Phase WWF-G).

## B. PT+WWF project

1. Create a new project with `methodologies_enabled=["protein_tracker",
   "wwf"]`.
2. Upload a 100-line dual-methodology CSV (PT fields + WWF fields).
3. Confirm both `missing_required_pt` and `missing_required_wwf` are
   empty.
4. On the AI classification step (Phase WWF-H), the wizard renders the
   **dual classification panel** — two cards (Protein Tracker + WWF).
5. Run "Lancer la catégorisation Protein Tracker" → wait for
   completion.
6. Run "Lancer la catégorisation WWF" → wait for completion. Both
   classifications must coexist.
7. Verify:
   - [ ] `classification_by_methodology.protein_tracker.status =
         "complete"`.
   - [ ] `classification_by_methodology.wwf.status = "complete"`.
   - [ ] Same product can have both a PT block AND a WWF block on the
         `/classifications` row.
   - [ ] Running WWF did NOT change the PT classification (or vice
         versa).
8. Click "Voir la validation Protein Tracker" → URL becomes
   `?methodology=protein_tracker` and the table renders PT columns.
9. Click "Voir la validation WWF" → URL becomes `?methodology=wwf` and
   the table renders WWF columns.
10. Toggle the in-table methodology selector — confirm columns swap.

## C. Metrics to record

Run `scripts/evaluate_wwf_classification.py --export-csv <export>.csv
--mismatches-csv /tmp/mismatches.csv` against the live classifications
export. Record:

| Metric                       | Target  | Recorded |
|------------------------------|---------|----------|
| strict accuracy              | ≥ 0.85  |          |
| food-group accuracy          | ≥ 0.97  |          |
| subgroup accuracy            | ≥ 0.95  |          |
| composite-bucket accuracy    | ≥ 0.90  |          |
| unknown rate                 | < 0.01  |          |
| failed rate                  | < 0.01  |          |
| review rate                  | < 0.20  |          |

Performance (Phase 35 budget — 10K target ≤ 1 hour):

| Scale  | Wall clock | Products / minute | Notes                  |
|--------|-----------:|------------------:|------------------------|
| 100    |            |                   |                        |
| 1 000  |            |                   |                        |
| 10 000 |            |                   | projection from 1 000  |

If any of the metrics above breach the target, log:

- The specific row(s) that mismatched (use `--mismatches-csv`).
- The guard rule (or absence of one) that fired on each row.
- Whether the failure was deterministic (guard) or AI-side (prompt /
  schema / parse).

## D. Privacy spot-check

Pick three random products from the run and verify (via the
classification job's logged sample-errors or by replaying the prompt):

- [ ] No `items_sold` in the AI payload.
- [ ] No `items_purchased` in the AI payload.
- [ ] No `weight_per_item_kg` in the AI payload.
- [ ] No `retail_channel` in the AI payload.
- [ ] No `is_own_brand` in the AI payload.
- [ ] No price / margin / revenue fields in the AI payload.

If any of these appear, do NOT promote the branch — the privacy
contract (Phase WWF-F tests, `altera_api.ai.policy`) is broken.
