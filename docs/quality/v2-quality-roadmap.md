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

## Quality-V2-C results (Voyage embeddings + NEVO vector search)

V2-C adds a real embedding backend (**Voyage AI**) behind explicit
flags and a NEVO **vector candidate search**. Embeddings *generate*
candidates; the precision-first V2 rules still *decide*. **Offline by
default — V1 is still the production default; no route imports the
embeddings stack or Voyage; embeddings are disabled by default; the
normal test suite makes no network calls.**

### Enabling embeddings

| Env var | Default | Meaning |
| --- | --- | --- |
| `ALTERA_ENABLE_EMBEDDINGS` | `false` | master switch for the env factory |
| `ALTERA_EMBEDDING_PROVIDER` | `fake` | `fake` (offline) or `voyage` |
| `ALTERA_EMBEDDING_MODEL` | `voyage-4-lite` | Voyage model name |
| `ALTERA_EMBEDDING_DIMENSIONS` | _(unset)_ | optional output size |
| `VOYAGE_API_KEY` | _(unset)_ | required only when provider=voyage |

`get_embedding_provider()` returns the deterministic `FakeEmbeddingProvider`
unless embeddings are enabled AND `voyage` is selected. The evaluator can
also build a provider explicitly via `build_embedding_provider(name, …)`.
There is **no silent fall-back**: selecting `voyage` without
`VOYAGE_API_KEY` raises a clear `EmbeddingProviderError`.

### Voyage provider behaviour

`VoyageEmbeddingProvider` uses the Voyage retrieval contract:
indexed/reference texts embed with `input_type="document"` and search
queries with `input_type="query"`. The `voyageai` SDK is imported
lazily (never a test-suite dependency); a client can be injected for
tests. API failures surface as `EmbeddingProviderError` — never a silent
fall-back to fake.

### Privacy

Reference and query texts are built by `embeddings/text_builder.py`,
which **raises** on any commercial/physical field (sales, units, weight,
price, margin, …). Only descriptor fields are ever embedded:
product_name, retailer_category, ingredients_text, labels (query);
food_name_en/fr, food group, NEVO code (reference).

### NEVO vector pipeline

`NevoVectorIndex` (cosine over reference embeddings) → top-k candidates
→ each candidate gated by the V2 NEVO rules → first rule-confirmed
candidate accepted (`embedding_plus_rule`); a high-similarity
*abstain* may go to `proxy_review`; otherwise `no_match`. Embeddings can
never override a hard rejection — a trap reference ("Oil olive",
"Potatoes mashed with milk") is killed by the rules even when the index
ranks it first. The decision carries match/no-match, confidence,
`match_type` (exact|alias|embedding|embedding_plus_rule|proxy_review|
no_match), review flag, top + rejected candidates with reasons,
rationale, and the provider/model used.

### Results (fake provider, offline, deterministic)

NEVO rules+embeddings on the 33-case embeddings fixture (87 NEVO eval
cases total across fixtures):

- coverage **0.96**, high-confidence precision **1.00**
- high-confidence false positives **0**
- forbidden-match rejection **100%**
- avg rank of the expected match **~2.5**

### Run the evaluator

Fake / offline (no key, deterministic):

```bash
.venv/bin/python scripts/evaluate_nevo_matching.py \
    --matcher-version v2-embeddings --embedding-provider fake \
    --fixture altera_api/data/eval/nevo/nevo_dataset_embeddings.json \
    --candidates-csv /tmp/nevo_candidates.csv
```

Real Voyage (manual smoke — needs a key; not run in CI):

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=sk-... \
.venv/bin/python scripts/evaluate_nevo_matching.py \
    --matcher-version v2-embeddings --embedding-provider voyage \
    --embedding-model voyage-4-lite --top-k 20
```

Put `VOYAGE_API_KEY` in your shell env (or a local, git-ignored `.env`)
— never commit it.

### Gates (V2-C)

- Fake provider: deterministic tests pass; hard rejections always win;
  forbidden-match rejection = 100%.
- Voyage provider (when a key is present): high-confidence false
  positives = 0 on trap fixtures; forbidden-match rejection = 100%;
  coverage should improve vs rules-only. CI does **not** fail when the
  key is absent — real-provider eval is a manual smoke command.

### Why production still uses V1

Nothing here is wired into an app route. Demos run V1; the embeddings
stack is reachable only from the evaluator + tests, gated by env flags
that default to fake/offline. V2 activation remains a later, staged
opt-in once metrics clear the gates with the real provider.

### Next phase recommendation

- **Quality-V2-D:** persist embeddings (pgvector or an export artifact)
  + a NEVO candidate-retrieval service, and a Voyage-backed run over the
  full NEVO reference table; then extend vector retrieval to PT/WWF
  example matching.

## Quality-V2-D results (real Voyage harness + benchmark + full NEVO)

V2-D makes the real Voyage NEVO evaluation runnable and benchmarkable
(fake vs voyage models), against either the curated fixture reference or
the **full NEVO 2025 reference** (2,327 foods). **Still offline/
evaluator-only — V1 is the production default; a present
`VOYAGE_API_KEY` does NOT change app behaviour; no route imports the
embeddings stack.**

### Run the real Voyage evaluation

The `voyageai` SDK is an OPTIONAL extra (not a runtime dependency).
Install it where you run the eval (e.g. the Render shell):

```bash
pip install voyageai          # or: uv pip install -e '.[eval]'
```

One real model (fails clearly if the key is missing):

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \
.venv/bin/python scripts/evaluate_nevo_voyage.py \
    --model voyage-4 --reference-source nevo --top-k 20
```

Benchmark fake vs voyage-4 vs voyage-4-lite in one table:

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=... \
.venv/bin/python scripts/benchmark_nevo_embeddings.py \
    --models fake,voyage-4,voyage-4-lite \
    --reference-source nevo --top-k 20 --price-per-1m 0.06
```

Env vars: `VOYAGE_API_KEY`, `ALTERA_ENABLE_EMBEDDINGS=true`,
`ALTERA_EMBEDDING_PROVIDER=voyage`, `ALTERA_EMBEDDING_MODEL=voyage-4`,
optional `ALTERA_EMBEDDING_DIMENSIONS`. CSVs (candidates + mismatches)
land in `local_data/quality/` (git-ignored). `--price-per-1m` is an
estimate — set it to the model's real price for an accurate cost column.

### Benchmark table (fill the voyage rows from a Render run)

Fake rows below were produced offline in this phase. The voyage rows
require the key and must be run on Render (or locally with the key).

```
Model           Coverage   High-conf FP   Forbidden rej   Expected top-5   Cost
fake (fixture)     96.4%        0             100%            85.7%         $0
fake (full NEVO)   21.4%        0             100%            25.0%         $0
voyage-4           <run>        <run>         <run>           <run>         <run>
voyage-4-lite      <run>        <run>         <run>           <run>         <run>
```

**Key offline finding:** the deterministic fake provider is keyword-bag
similarity, so against the full 2,327-food NEVO set its coverage
collapses to ~21% (27/28 expected matches fall outside top-20) — it is a
plumbing/CI tool, not a real matcher. The safety gates still hold (0
high-confidence false positives, 100% forbidden rejection): wrong fake
retrievals abstain or go to review, never auto-accept. The real semantic
lift is exactly what voyage-4 should provide on the full reference — run
the benchmark to populate the table.

### Gates (V2-D, enforced by the benchmark exit code)

- forbidden-match rejection = 100%
- high-confidence false positives = 0 (auto-accept wrong = 0; review-
  level wrong is allowed — it is routed to a human, not auto-accepted)
- no hard-rejected candidate may be accepted by embeddings
- no production behaviour changed

A gate hole found + fixed this phase: a product that resolves to a
specific concept (e.g. *peanut butter* → `peanut_butter`) no longer
accepts a bare sub-token literal match to an unrelated food
(*Biscuit peanut*) — the concept path is the only accept route for such
products (regression-tested).

### Failure taxonomy (from the candidate CSV)

`summarize_candidates` buckets each should-match case: expected at
rank-1, rank 2–5, retrieved-but-rejected, missing-from-top-k, plus
dangerous candidates that ranked high but were correctly rejected. On
the full-NEVO fake run, "missing-from-top-k" dominates (keyword
retrieval misses) — pointing to: bigger/better retrieval (real
embeddings), more aliases, or a reranker. Re-run the taxonomy with
voyage-4 to see which failures are retrieval vs rule vs fixture.

### Recommendation

- Run `voyage-4` and `voyage-4-lite` on the **full NEVO reference** and
  compare top-5 / coverage / cost using the benchmark.
- If `voyage-4-lite` matches `voyage-4` on top-k, prefer **lite** for
  production-scale indexing (cheaper); keep `voyage-4` if it clearly
  wins the hard FR/EN/NL semantic matches.
- Either way: a present key changes nothing in production. Activation
  stays a later staged opt-in once the real-provider gates pass.

### Why production still uses V1

`get_embedding_provider()` returns the fake provider unless
`ALTERA_ENABLE_EMBEDDINGS=true`; adding `VOYAGE_API_KEY` alone does not
enable embeddings (regression-tested). No app route imports the
embeddings stack or Voyage. V1 remains the demo-safe default.

### Next phase recommendation

- **Quality-V2-E:** persist NEVO embeddings (pgvector or an export
  artifact) so the index isn't rebuilt per run; add a reranker for the
  rank 2–5 / missing-from-top-k tail; then extend vector retrieval to
  PT/WWF example matching.
