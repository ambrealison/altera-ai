# V2 Quality Roadmap

Phase Quality-V2-A established the safe foundation: feature flags, an
evaluation harness, a rule-engine skeleton, and an embedding
abstraction — all **opt-in**, with V1 remaining the production default.

## Guiding philosophy

1. **Wrong-accepted is worse than review.** A confidently wrong
   classification or NEVO match silently corrupts a retailer's report.
   A row sent to review costs an analyst a few seconds.
2. **Abstain beats confident-wrong.** When a rule isn't sure, it
   abstains (defers to fallback / AI / human) rather than guessing.
3. **Coverage only after precision is safe.** We raise auto-accept
   coverage *after* high-confidence precision is proven on the
   evaluation fixtures — never before.
4. **V1 stays demo-safe.** Nothing in V2 can affect a demo unless an
   operator sets an env flag (see below).

## Feature flags (defaults keep V1)

| Env var | Default | Effect |
|---|---|---|
| `ALTERA_CLASSIFICATION_PIPELINE_VERSION` | `v1` | `v2` selects the rule engine (evaluator/dev only this phase) |
| `ALTERA_NEVO_MATCHER_VERSION` | `v1` | `v2` selects the candidate-gating matcher |
| `ALTERA_ENABLE_EMBEDDINGS` | `false` | `true` would enable a real embedding provider (not yet implemented) |
| `ALTERA_ENABLE_V2_EVALUATION` | `false` | gate for V2 in non-script contexts |

Production routes read **none** of these yet — they call the V1 guards
directly. V2 is reachable only from evaluator scripts and tests.

## Target metrics

### Protein Tracker
- auto-accepted accuracy **≥ 98%**
- usable accuracy **≥ 97%**
- unknown-readable **= 0**
- wrong-accepted **≤ 1%**

### WWF
- auto-accepted accuracy **≥ 98%**
- food-group accuracy **≥ 97%**
- strict accuracy **≥ 93–95%**
- unknown-readable **= 0**
- wrong-accepted **≤ 1–2%**

### NEVO
- high-confidence precision **≥ 98%**
- matched-row precision **≥ 95%**
- false-positive high-confidence **≈ 0**
- abstain allowed whenever uncertain

## Evaluation harness

Fixtures live under `apps/api/altera_api/data/eval/`:

```
classification/pt/{pt_dataset_100,pt_edge_cases}.json
classification/wwf/{wwf_dataset_100,wwf_obvious,wwf_composites,wwf_edge_cases}.json
nevo/{nevo_simple_exact,nevo_french_products,nevo_composite_traps,
      nevo_secondary_ingredient_traps,nevo_should_abstain}.json
```

Run:

```bash
.venv/bin/python scripts/evaluate_quality_v2.py --task pt  --pipeline-version v2
.venv/bin/python scripts/evaluate_quality_v2.py --task wwf --pipeline-version v2
.venv/bin/python scripts/evaluate_nevo_matching.py \
    --fixture altera_api/data/eval/nevo/nevo_composite_traps.json
```

Metric computation lives in
`altera_api/classification_v2/evaluation.py` (importable + unit-tested);
the scripts are thin CLI wrappers. Mismatch CSVs use the columns:
`fixture_id, product_name, expected, actual, confidence, source,
rule_id, pipeline_version, notes, top_candidates`.

## Rollout plan (later phases)

1. **Quality-V2-A (done):** flags, harness, rule skeleton, embedding
   abstraction, docs.
2. **Quality-V2-B:** grow the rule sets + fixtures until the V2
   evaluator beats V1 on every target metric, offline.
3. **Quality-V2-C:** implement a real embedding provider + in-memory
   retriever prototype (still offline-testable via the fake provider).
4. **Quality-V2-D:** pgvector index + NEVO candidate retrieval.
5. **Quality-V2-E:** admin-only "Pipeline version" selector, then a
   staged production opt-in once metrics clear the gates.

Demo safety holds at every step: V1 is the default and any V2 issue is
reverted by leaving the env flag at `v1`.

## Quality-V2-B results (offline; V1 still default)

V2-B grew the deterministic rule sets + evaluation fixtures until V2
beats V1 on every gated metric. **Still offline/evaluator-only — V1
remains the production default; no route imports V2; embeddings stay
disabled.**

### Baseline V1 vs V2 (V2-B fixtures)

| Task | Metric | V1 | V2 | Δ |
| --- | --- | --- | --- | --- |
| PT (n=65)  | top-level accuracy | 0.708 | **1.000** | +0.292 |
| PT         | wrong-accepted | 0 | **0** | — |
| PT         | unknown-readable | 16 | **0** | −16 |
| PT         | improvements / regressions | — | **19 / 0** | — |
| WWF (n=68) | food-group accuracy | 0.838 | **1.000** | +0.162 |
| WWF        | composite-bucket accuracy | 0.500 | **1.000** | +0.500 |
| WWF        | wrong-accepted | 0 | **0** | — |
| WWF        | unknown-readable | 2 | **0** | −2 |
| WWF        | improvements / regressions | — | **11 / 0** | — |
| NEVO (n=54)| high-confidence precision | n/a | **1.00** | — |
| NEVO       | high-confidence false positives | n/a | **0** | — |
| NEVO       | forbidden-match rejection | n/a | **100%** | — |
| NEVO       | coverage (precision-first) | n/a | 42/42 matched | — |

(V1 has no offline NEVO matcher; NEVO gates are absolute, not relative.)

Reproduce:

```bash
.venv/bin/python scripts/evaluate_quality_v2.py --task pt   --compare
.venv/bin/python scripts/evaluate_quality_v2.py --task wwf  --compare
.venv/bin/python scripts/evaluate_quality_v2.py --task nevo --compare
```

`--compare` prints the V1/V2 table + the quality gates and exits non-zero
if a gate fails. `--mismatches-csv` writes V2 + `*.v1.csv` mismatch
files; `--improvements-csv` writes the cases where V1 and V2 disagree.

### Quality gates (all PASS at V2-B)

- **PT:** V2 accuracy ≥ V1; V2 wrong-accepted ≤ V1; unknown-readable = 0.
- **WWF:** V2 food-group accuracy ≥ V1; V2 composite-bucket accuracy ≥
  V1; V2 wrong-accepted ≤ V1; unknown-readable = 0.
- **NEVO:** zero high-confidence false positives; 100% forbidden-match
  rejection (coverage may be lower than V1 — precision-first).

A failing gate means **do not activate V2** — print the failing cases
and leave V1 default. Gate computation is unit-tested
(`pt_gates` / `wwf_gates` / `nevo_gates`).

### Rules added in V2-B

- **PT:** animal-core (meat/poultry/fish/egg/dairy), plant-core
  (legumes/soy/tofu/seitan/plant-meat alternatives), plant-non-core
  (fruit/veg/grain/snack/sweet/oil/condiment), composite = animal
  protein + plant component (prepared dish with meat/fish/egg/dairy, or
  known animal-dish phrase, or both protein families), vegan
  multi-ingredient correction (falafel wrap / chickpea curry / bean
  burger / chili sin carne stay plant-core, NOT composite), pet food
  classified by protein (in scope) vs pet accessories (out of scope),
  and a readable fallback so nothing readable is left unknown.
- **WWF:** ordered rules — composite prepared dishes (+ Step-1 bucket
  meat→seafood→vegetarian→vegan), then precedence fixes (plant cheese →
  FG2 alt, plant drinks → FG2 alt, bakery/desserts → FG7, muesli/cereal
  → FG5, peanut butter → FG3 plant fat, seafood incl. in-oil → FG1),
  then FG1–FG7, tubers before fruit/veg, readable fallback.
- **NEVO:** product-head + FR/EN concept extraction, hard rejection of
  composite/secondary-ingredient candidates ("with/without/mashed/sauce"
  and "X à l'huile d'olive" / "ail & persil"), safe alias matches
  (pois chiches ↔ chickpeas, lait ↔ milk, beurre de cacahuète ↔ peanut
  butter, …), exact/alias/proxy/abstain decision levels, and a decision
  trace per candidate.

### Known remaining gaps

- Rules are keyword/phrase based; they will miss long-tail or
  mis-spelled names that no token covers (those land in the readable
  fallback → review, never auto-accepted).
- Composite buckets without an explicit animal token (e.g. a bare
  "quiche lorraine" in WWF) rely on a small known-dish phrase list.
- NEVO coverage is intentionally precision-first; ambiguous heads
  abstain rather than guess — semantic retrieval is the next lever.
- Fixtures are curated (≥50 PT / ≥50 WWF / ≥50 NEVO, FR + EN); they are
  not a random sample of a full retailer assortment.

### Next phase recommendation

- **Quality-V2-C:** add a real embedding provider behind the existing
  abstraction + an in-memory retriever, and/or NEVO vector candidate
  search, to cover the long-tail names the keyword rules miss — still
  offline-testable via the fake provider, V1 still default.
