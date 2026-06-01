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
