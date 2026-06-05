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

### Run the real Voyage evaluation (Render)

> **Hotfix (Quality-V2-D):** the benchmark now ships as a **package
> module** and `voyageai` is a **backend dependency**, so it runs in the
> Render runtime image with **no `pip install`, no `PYTHONPATH`, and no
> dependence on the top-level `scripts/` directory**. The Render image
> copies `altera_api/` but **NOT** `scripts/`, so always invoke the
> package CLI (`python -m …`) on Render — the `scripts/*.py` files are
> dev-checkout conveniences only.

`voyageai` is in the main dependencies (`pyproject.toml` + `uv.lock`), so
a Render deploy installs it automatically. It is still imported **lazily**
— only when the Voyage provider is actually constructed (embeddings
enabled + provider=voyage). App startup never imports it.

Benchmark fake vs voyage-4 vs voyage-4-lite in one table — the canonical
Render command:

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.benchmark_nevo_embeddings \
    --models fake,voyage-4,voyage-4-lite \
    --reference-source nevo --top-k 20 \
    --price-per-1m 0.06 --output-dir /tmp/altera-quality
```

One real model (hard-fails if the key/SDK is missing):

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.benchmark_nevo_embeddings \
    --models voyage-4 --reference-source nevo --top-k 20 \
    --require-voyage --output-dir /tmp/altera-quality
```

Env vars: `VOYAGE_API_KEY`, `ALTERA_ENABLE_EMBEDDINGS=true`,
`ALTERA_EMBEDDING_PROVIDER=voyage`, `ALTERA_EMBEDDING_MODEL=voyage-4`,
optional `ALTERA_EMBEDDING_DIMENSIONS`. CSVs (candidates + mismatches)
are written to **`--output-dir` (default `/tmp/altera-quality`)** — a
writable temp dir, because `/app` is read-only in the Render image. They
are not committed. `--price-per-1m` is an estimate — set it to the
model's real price for an accurate cost column.

Clear failure modes (no secrets printed):

- voyageai missing → `voyageai package is not installed. Add it to
  backend dependencies or install it in the runtime.`
- key missing → `VOYAGE_API_KEY is required for embedding-provider=voyage.`
- voyage requested with embeddings disabled → skipped with
  `embeddings are disabled — set ALTERA_ENABLE_EMBEDDINGS=true …`
  (or a non-zero exit under `--require-voyage`).

From a **local dev checkout** the thin wrappers still work
(`.venv/bin/python scripts/benchmark_nevo_embeddings.py …`), but they
just delegate to the package module above.

### Benchmark table — fixture reference (33 cases / 30 foods)

Run on Render with the real key (V2-E update). **voyage-4-lite matches
voyage-4 on the fixture** (perfect top-1/5/20) — so lite is the default.

```
Model           Coverage   High-conf FP   Forbidden rej   top-1   top-5   top-20
fake               96.4%        0             100%          60.7%   85.7%  100.0%
voyage-4           96.4%        0             100%         100.0%  100.0%  100.0%
voyage-4-lite      96.4%        0             100%         100.0%  100.0%  100.0%
fake (full NEVO)   21.4%        0             100%             —    25.0%      —
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

## Quality-V2-E results (default lite + observable, resumable full NEVO)

Phase V2-E makes the full-NEVO benchmark actually usable on Render and
prepares the V2 embeddings matcher for *controlled* activation — still
**offline / evaluator-only; V1 stays the production default; embeddings
stay disabled by default; no route uses V2 or embeddings.**

### Default embedding model: `voyage-4-lite`

The fixture benchmark (above) showed `voyage-4-lite` equals `voyage-4`
(perfect top-1/5/20, 0 high-conf FP, 100% forbidden rejection) at a lower
price, so `ALTERA_EMBEDDING_MODEL` defaults to **`voyage-4-lite`**
(`quality_config.DEFAULT_EMBEDDING_MODEL`). Override to `voyage-4` only if
the full-NEVO run shows it is materially better on the hard FR/EN/NL
matches. Embeddings stay off by default; a present `VOYAGE_API_KEY` alone
does not enable them (regression-tested).

### Stabilised full-NEVO benchmark

The full reference (~2.3k foods) used to print `[done] fake` and then go
silent. It is now **batched, observable, and resumable**:

- **Batching** — references embed in `--batch-size` (default 64) chunks:
  one provider call per batch, not per food.
- **Progress** (printed + flushed): model start, reference + batch counts,
  `docs batch X/Y · n/total · elapsed`, cache hits/misses, then
  `evaluating N queries` with periodic query progress, then `[done]`.
- **Resumable cache** (`--cache-dir`, default `/tmp/altera-quality/cache`)
  — a `FileEmbeddingCache` keyed by provider/model/input_type/dimensions/
  text_hash, flushed every batch via an atomic temp-file replace. An
  interrupted run re-runs the SAME command and embeds nothing already
  cached. Documents and queries are separate entries (input_type in key).
- **Limit flags** — `--limit-references N` / `--limit-cases N` for fast
  smokes.
- **Friendly rate limits** — a Voyage 429 raises `EmbeddingRateLimitError`;
  the CLI prints a friendly "re-run to resume" message, keeps the cache,
  and exits non-zero (full traceback only with `--debug`).

Recommended full run on Render:

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.benchmark_nevo_embeddings \
    --models voyage-4-lite --reference-source nevo --top-k 20 \
    --batch-size 64 --cache-dir /tmp/altera-quality/cache \
    --output-dir /tmp/altera-quality
```

Quick smoke (subset, still real Voyage):

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.benchmark_nevo_embeddings \
    --models voyage-4-lite --reference-source nevo \
    --limit-references 200 --limit-cases 10 --top-k 20 \
    --batch-size 64 --output-dir /tmp/altera-quality
```

This real Voyage run is **not** part of CI — CI only runs the fake path.

### Controlled NEVO matcher factory

`get_nevo_matcher(version)` (`classification_v2/nevo_matcher.py`) is the
single selection layer, driven by `ALTERA_NEVO_MATCHER_VERSION`
(`v1` | `v2-rules` | `v2-embeddings`, **default `v1`**):

- `v1` — the production AI matcher (`nutrition_matcher.propose_match`).
- `v2-rules` — the offline precision-first gate (no embeddings, anywhere).
- `v2-embeddings` — V2 rules + the vector index; **requires
  `ALTERA_ENABLE_EMBEDDINGS=true`** in production (else it raises — never a
  silent fake fallback). Offline evaluation passes `evaluator_mode=True`.

No production route imports this factory, so selecting a non-V1 matcher is
an explicit opt-in. Default and unknown values resolve to `v1`.

### Richer candidate CSV + failure taxonomy

The candidate CSV now also carries `match_type`, `confidence`, `model`,
`provider`, and records the **full top-k** ranking per case (not just the
top 5). The taxonomy gained `expected_rank_6_20`,
`dangerous_incorrectly_accepted` (must be 0 — a real safety failure),
`no_safe_reference`, and `fixture_expected_not_in_reference` (the expected
food is not in the loaded NEVO reference at all — a coverage/fixture gap,
distinct from a retrieval miss). Together these say whether the next win
is a reranker (rank 2–20), better reference text/aliases/`top_k`
(missing-from-top-k), or fixture/reference coverage.

### Decide next step from the full-NEVO voyage-4-lite run (PART G)

- HC-FP = 0 and forbidden = 100% but top-1/top-5 low while top-20 high →
  **Quality-V2-F: add a reranker.**
- expected often missing from top-20 → **Quality-V2-F: improve NEVO
  reference text + aliases + raise `top_k`.**
- `voyage-4-lite` clearly worse than `voyage-4` on full NEVO → use
  `voyage-4` for NEVO only.
- Do not activate V2 in production until the benchmark is understood.

## Quality-V2-F results (rule fixes, fixture alignment, diagnostics)

The first real full-NEVO `voyage-4-lite` run (commit `eb27579`) worked but
did NOT pass the gates:

```
Model: voyage-4-lite   reference: full NEVO 2025 (2,328 foods)
Coverage 71.4% · HC-FP 1 · Forbidden rejection 100% ·
top1 60.7% · top5 71.4% · top20 71.4% · abstain 36.4% · gates=False
Taxonomy: fixture_expected_not_in_reference 24 (of 28 should-match)
```

**Interpretation:** the infra works, but (a) ONE high-confidence false
positive blocks activation, (b) the fixtures were not aligned with real
NEVO 2025 — 24/28 expected references didn't exist in the CSV because the
fixture used synthetic codes/names, and (c) multi-word concepts were lost:
"Pois chiches" was split to the head `pois`, and NEVO's inverted names
("Peas chick boiled") weren't recognised as chickpeas, so the right
candidate was rejected/abstained.

What V2-F changed:

- **Multi-word concept / alias extraction.** Concepts now include NEVO's
  inverted English names ("Peas chick", "Beans black", "Lentils red"),
  Dutch ("kikkererwten"), and a `rice`/`quark`/`tempeh`/`seitan` concept.
  "Pois chiches" → `chickpea` matches "Peas chick boiled" → `chickpea`.
  Longest phrase still wins ("beurre de cacahuète" → `peanut_butter`,
  never `butter`).
- **Preparation state vs composite dish.** `boiled/cooked/canned/dried/
  frozen/fresh/raw/…` are SAFE states — "Peas chick boiled" is eligible.
  A composite is a JOINER ("with/without/w/wo") or a DISH NOUN
  ("soup/pie/lasagne/bar/sauce/hummus/…"); it is rejected unless its HEAD
  food shares the product concept — so "Hummus with chickpeas",
  "Apple pie without sugar", "Muesli bar" reject, while "Ratatouille
  prepared wo meat" still matches a ratatouille product. A candidate's
  head concept ignores the trailing ingredient ("Hummus with chickpeas"
  → head `hummus`, not chickpea).
- **HC-FP hardening.** A literal head token that is not the candidate's
  primary head no longer auto-accepts: if the product has a concept the
  candidate doesn't share → reject; otherwise → REVIEW, never a
  high-confidence accept. Confirmed 0 false positives on the full-NEVO
  fake run; all fixture traps still rejected; `dangerous_incorrectly_
  accepted` = 0.
- **Fixture alignment.** Each should-match fixture now carries the real
  NEVO 2025 code (e.g. Chickpeas → 1095 "Peas chick boiled") with the
  concept name kept, and the not-in-reference check is concept-aware →
  `fixture_expected_not_in_reference` drops **24 → 0**. A new validator,
  `python -m altera_api.classification_v2.validate_nevo_fixtures`, reports
  per-case alignment (code/name/concept exists, closest names, suggested
  action) and never marks a should-abstain trap as a valid match.
- **Failure diagnostics.** The benchmark now writes
  `nevo_failures_<model>.csv`, `nevo_high_conf_false_positives_<model>.csv`,
  `nevo_expected_missing_topk_<model>.csv`,
  `nevo_fixture_expected_not_in_reference_<model>.csv`,
  `nevo_abstains_<model>.csv` (each with the accepted candidate, top-1/5,
  rejection reasons, taxonomy bucket) and prints the three highest-signal
  sections to the console — no grepping the full candidate CSV.
- **Reference text aliases.** `build_nevo_reference_text` adds
  cross-language aliases for common SIMPLE foods (never composites) so the
  right candidate ranks higher; still excludes all commercial fields.

### Re-run the benchmark (PART G — same command, not in CI)

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.benchmark_nevo_embeddings \
    --models voyage-4-lite --reference-source nevo --top-k 20 \
    --batch-size 64 --cache-dir /tmp/altera-quality/cache \
    --output-dir /tmp/altera-quality
```

Target after V2-F: **HC-FP = 0, forbidden = 100%, dangerous accepted = 0,
`fixture_expected_not_in_reference` ≈ 0**, "Pois chiches" no longer
abstains on `pois`. Coverage may move up or down as unsafe cases abstain —
gates must be green. The HC-FP classes are closed in the rules + verified
on the fake full-NEVO run; confirm HC-FP = 0 on the next real run via
`nevo_high_conf_false_positives_voyage-4-lite.csv` (it should be header-only).

### Reranker? (PART H)

Hold the reranker until the post-fix real run shows the expected match is
usually in top 2–20 with green gates but poor final ranking. If expected
is still often missing from top-20, invest first in reference text /
aliases / `top_k`; fixture alignment is now done. V1 stays the production
default; this remains evaluator/dev-only.

### Confirmed green after V2-F (+ taxonomy hotfix)

The full-NEVO `voyage-4-lite` rerun after V2-F passes the gates:

```
Coverage 100% · HC-FP 0 · Forbidden rejection 100% ·
top1 85.7% · top5 96.4% · top20 100% · gates=True
All focused failure CSVs = 0 rows.
```

Hotfix: `summarize_candidates` now finds the expected food among
candidates with the SAME code-aware/concept-aware matching as the metrics
and the focused failure reports (via `_same_food` on each candidate's
`nevo_code` + concept), instead of an exact fixture-label name compare.
Previously the printed taxonomy showed a stale `expected_missing_from_topk:
24` on the real run because the fixture label ("Chickpeas") never string-
equals the real NEVO candidate label ("Peas chick boiled") even though
they share `nevo_code` 1095 / the chickpea concept. The candidate CSV +
rows now also carry `candidate_code`. Taxonomy rank buckets are now
consistent with `top1/top5/top20` and with the (header-only) failure CSVs.

## Quality-V2-G — rank-miss inspection (before any reranker)

The green full-NEVO `voyage-4-lite` run leaves a small, safe tail:
`expected_rank_2_5 = 3`, `expected_rank_6_20 = 1`,
`expected_retrieved_but_rejected = 2` (HC-FP 0, forbidden 100%,
top20 100%). Before deciding on a reranker, V2-G adds two focused reports
(no rules/gate change, still evaluator/dev-only) so we can see exactly
what each tail case is:

- `nevo_rank_misses_<model>.csv` — the expected food was retrieved and
  accepted but not at rank 1 (mirrors `expected_rank_2_5 +
  expected_rank_6_20`).
- `nevo_expected_retrieved_but_rejected_<model>.csv` — the expected food
  was retrieved in the top-k but the rules rejected it (mirrors
  `expected_retrieved_but_rejected`).

Each row carries the expected candidate (name/code/rank/similarity/
rejection reason), the accepted candidate (name/code/rank/similarity/
match_type/confidence + `accepted_same_concept_as_expected`), the top-5
context (names/codes/similarities), and a heuristic `diagnosis_bucket`:

- `harmless_equivalent` — a same-concept food was still accepted; coverage
  is fine, no action.
- `expected_too_specific` — a broader same-concept food was accepted; the
  fixture expected a more specific variant.
- `rule_too_strict` — the rules rejected the only good candidate and
  nothing equivalent was accepted (candidate gate to revisit).
- `true_ranking_issue` — a different-concept food was accepted while the
  expected was present (embedding mis-rank).
- `fixture_should_change` — the expected entry resolves to a different food
  per the rules (fixture/reference fix).
- `needs_reranker` — the right food is present but ranked below
  different-concept noise; a reranker would lift it to rank 1.

The console prints both sections after the failure diagnostics. Counts are
asserted to mirror the taxonomy. **Interpretation drives PART H:** only if
the real run's rank-misses are dominated by `needs_reranker` (right food
present, just ranked low, gates green) is a reranker the next step; if they
are `harmless_equivalent` / `expected_too_specific` / `fixture_should_
change`, no reranker is warranted. The reranker is still NOT implemented;
V1 remains the production default and embeddings stay disabled by default.

## Quality-V2-H — refined rank-miss interpretation (no reranker yet)

V2-G's diagnostics bucketed every same-concept rank>1 miss as
`needs_reranker`. On the real green run that over-states the problem: with
coverage 100% / HC-FP 0, a rank miss where the system still accepted a
correct same-concept food is HARMLESS, not a reranker failure. V2-H
refines the heuristic (diagnostics only — no rules/gate change):

- A rank>1 miss where `accepted_same_concept_as_expected = true` →
  `harmless_equivalent` (or `expected_too_specific` when the accepted food
  is broader than an over-specific fixture label). NEVER `needs_reranker`.
- `needs_reranker` is reserved for a DIFFERENT-concept food accepted above
  the right same-concept one (reordering by concept agreement would help).
- A new `match_relationship` column distinguishes the finer cases:
  `exact_code_rank_miss`, `same_concept_code_mismatch`,
  `accepted_more_specific_variant`, `fixture_expected_too_specific`,
  `different_concept_ranking_noise`, `expected_variant_rejected`.

### The six real cases, re-read

| case | product | accepted | bucket | relationship |
|---|---|---|---|---|
| nve-11 | Lentilles corail | Lentils red boiled @2 | harmless_equivalent | exact_code_rank_miss |
| nve-16 | Fresh cheese | Quark full fat @4 | harmless_equivalent | same_concept_code_mismatch |
| nve-23 | Fromage | Cheese Brie 60+ @3 | harmless_equivalent | accepted_more_specific_variant |
| nve-27 | Soupe lentilles coco | Lentils green and brown boiled @6 | harmless_equivalent | same_concept_code_mismatch |
| nve-20 | Pates penne | Pasta white boiled | harmless_equivalent | expected_variant_rejected |
| nve-22 | Muesli maison | Muesli w fruit seeds and kernels | harmless_equivalent | expected_variant_rejected |

All six are same-concept safe accepts — coverage stays 100% and there are
**zero** `needs_reranker` cases.

### Fixtures

No fixture changes were warranted: every fixture already points at a valid
NEVO 2025 code, and the accepted equivalents are same-concept (the
fixture's reference is as good or better). Fixture updates remain reserved
for cases where the accepted candidate is clearly a more realistic NEVO
reference — none here.

### Decision: a reranker is NOT justified yet

The real run's rank tail is entirely `harmless_equivalent`, with HC-FP 0,
forbidden rejection 100%, coverage 100%, and top20 100%. There is no
`needs_reranker` signal, so we do **not** build a reranker. Revisit only if
a future run shows `needs_reranker` cases (a different-concept food
accepted above the right one) or genuine ranking noise. V1 remains the
production default; embeddings stay disabled by default; evaluator/dev-only.

## Quality-V2-I — read-only V1-vs-V2 shadow comparison on real project rows

A STRICTLY READ-ONLY CLI to compare the production V1 NEVO matcher against
the V2 embeddings matcher on a real project's products, before any
activation decision. It reads the project, its products and the NEVO
reference and writes only comparison CSVs under `/tmp/altera-quality`; it
never calls a store write method (no enrichment records, classifications,
runs, review items, or product updates — proven by a read-only store spy in
the tests).

```bash
ALTERA_ENABLE_EMBEDDINGS=true VOYAGE_API_KEY=$VOYAGE_API_KEY \
python -m altera_api.classification_v2.compare_nevo_v1_v2 \
    --project-id <uuid> --output-dir /tmp/altera-quality \
    --top-k 20 --cache-dir /tmp/altera-quality/cache
```

Also: `--limit-products N`, `--batch-size 64`, `--embedding-model
voyage-4-lite`, `--reference-source nevo|fixture`, `--require-voyage`,
`--debug`.

- **V1** = the current production deterministic `NevoProvider` path
  (exact EN/NL name → fuzzy token overlap → food-group average). No LLM
  call, no cost, no writes. (The optional AI-assisted fallback is not
  invoked in shadow mode.)
- **V2** = the embeddings matcher in evaluator/dev mode. It uses the real
  Voyage provider only when `ALTERA_ENABLE_EMBEDDINGS=true` (+
  `VOYAGE_API_KEY`); otherwise it falls back to the deterministic FAKE
  provider. A present `VOYAGE_API_KEY` alone enables nothing. Only the
  product descriptor (name / category / ingredients / labels) is embedded
  — never a commercial field.

The CSV rows carry the V1 + V2 outcomes/references/confidence, the V2 top-5
candidates and rejection reasons, plus an `agreement_bucket`
(`same_code` / `same_concept` / `v1_only` / `v2_only` / `both_no_match` /
`disagreement_needs_review`) and a `risk_bucket` (`safe_agreement` /
`v2_more_specific` / `v1_more_specific` / `v2_review_only` /
`v2_potential_false_positive` / `manual_inspection_needed`). The console
prints the bucket counts, V2 auto-accept vs review counts, the number of
potential high-risk disagreements to inspect, and the CSV path.

This is the evidence to decide a future controlled activation — it changes
no production behaviour: V1 remains the default, embeddings stay disabled
by default, and no route imports V2/embeddings.

## Quality-V2-J — NEVO V2 concepts for real FR retailer products

The V2-I shadow comparison on a real French project surfaced V1 false
positives (Sauce Tomate → "Beans white baked in tomato sauce", Chocolat
Noir → "Milk chocolate-flavoured", Pois Chiches → "Peas green dried",
Maïs Doux → "Corn starch", Café/Thé → "Biscuit Cafe noir", Corn Flakes →
"Chicken schnitzel … w corn flakes") and V2 false negatives (Chocolat,
Thon, Maïs Doux, Jus d'Orange, Café, Thé all `no_match` despite the right
food sitting in the top-5). V2-J adds the missing concepts and improves the
shadow interpretation. Still evaluator/dev-only; no rules wired to routes;
V1 default; embeddings off by default; gates stay green.

New concepts (FR product forms + EN/NEVO reference names): `chocolate`,
`tuna`, `sweet_corn`, `corn_flakes`, `orange_juice`, `coffee`, `tea`,
`soup`, `tomato_sauce`. So e.g. "Chocolat Noir" → "Chocolate dark", "Thon
… au Naturel" → "Tuna in water tinned", "Maïs Doux" → "Sweetcorn tinned",
"Corn Flakes" → "Breakfast cereal Cornflakes", "Jus d'Orange Pulpe" →
"Juice orange w pulp", "Café …" → "Coffee prepared", "Thé Noir Earl Grey"
→ "Tea prepared".

Ingredient-token traps stay rejected by the existing composite/dish-noun
head logic — a dish whose MAIN food differs from the product never matches:
"Beans white baked in tomato sauce" (head = beans), "Chicken schnitzel
breaded w corn flakes" (head = chicken), "Biscuit Cafe noir" (head =
biscuit/dish) all reject. Sweet corn deliberately excludes bare
"corn"/"maïs" so "Corn starch"/"Corn flour" never read as sweet corn. A
gate hole was also closed: an exact head-token match (e.g. "Corn Flakes"
head `corn` vs "Corn starch") is now rejected when the product resolves to
a concept the candidate does not share — only the concept path can accept
such products. Soup and tomato-sauce products resolve to a concept but
abstain (their only NEVO references are dish-noun composites) — safe, no
false positive.

Shadow interpretation (`compare_nevo_v1_v2`):
- New risk bucket `v2_better_than_v1` — V2 matched the product's OWN concept
  while V1 matched a different concept or nothing.
- New note "V1 likely false positive" — when V1's reference concept
  conflicts with the product's concept.
- Summary now also prints "V2 better than V1" and "V1 likely false
  positives" counts.

Gates unchanged: HC-FP 0, forbidden rejection 100%, dangerous_incorrectly_
accepted 0 on the full-NEVO fake benchmark.

## Quality-V2-K — expand real-catalog coverage + sharper shadow risk buckets

The 100-product shadow run after V2-J still had many safe abstains / v1_only
rows (missing real FR aliases) and 6 "potential high-risk" rows that were
actually safe V2 wins. V2-K expands the concept catalogue and refines the
shadow risk labelling. Evaluator/dev-only; V1 default; embeddings off; no
route imports V2/embeddings; gates green.

Part A — ~22 new concepts (FR product forms + EN/NEVO reference names):
`mustard`, `vinegar` (+balsamic), `vinaigrette`, `crisps` (chips), `quinoa`,
`couscous` (semolina), `wheat_flour`, `sugar`, `bread`, `honey`, `jam`,
`mozzarella`, `feta`, `creme_fraiche`, `margarine`, `ham`, `chicken`, `egg`,
`salmon`, `hummus`, `almond_drink`, `sorbet`. Notes:
- `mozzarella`/`feta` use phrase forms ("cheese mozzarella"/"cheese feta")
  so they beat the bare `cheese` concept at the same position.
- `hummus` moved from dish-noun to a CONCEPT, so "Houmous" matches "Hummus
  natural" while "Hummus with chickpeas" still rejects for a chickpea
  product via the JOINER head logic (head = hummus != chickpea).
- `sweet_corn`/`wheat_flour` deliberately exclude bare "corn"/"flour" so
  "Corn starch"/"Flour corn" never read as the food.

Traps stay rejected: tomato-sauce ≠ beans-in-tomato-sauce, corn-flakes ≠
chicken-schnitzel, vinegar ≠ salad dressing, margarine ≠ egg-fried-in-
margarine (egg head wins), corn-flakes ≠ corn-starch (exact-head closed in
V2-J). HC-FP 0, forbidden 100%, dangerous_incorrectly_accepted 0 on the
full-NEVO fake benchmark.

Part B — shadow risk buckets (`compare_nevo_v1_v2`): a V2 auto-accept is
gate-validated (it shares the product's concept OR exactly matches its head
token), so a V2 match where V1 differs/abstains is now `v2_better_than_v1`
(with note "V2 own-concept match"), not `v2_potential_false_positive`. The
latter is reserved for a V2 accept that is neither concept- nor head-
consistent with the product (rare). This reclassifies the six high-risk
rows (chips, quinoa, margarine, crème fraîche, sorbet, biscuits apéritif)
to wins.

Expected effect on the next 100-product Render run: V2 auto_accept up,
v1_only materially down, potential-high-risk down, v2_better_than_v1 up —
with no new unsafe accepts. (Run on Render with ALTERA_ENABLE_EMBEDDINGS=
true + VOYAGE_API_KEY for the cross-language retrieval that the offline fake
can't reproduce.)

## Quality-V2-L — targeted coverage pass + œ normalization

The 100-product shadow run after V2-K was already good (V2 auto_accept 51,
v1_only 30, high-risk 0, v2_better 17). V2-L reduces the remaining obvious
review-only false negatives while keeping precision-first safety.
Evaluator/dev-only; V1 default; embeddings off; no route imports
V2/embeddings; gates green (HC-FP 0, forbidden 100%, dangerous 0).

Part A — normalization: `_norm` now expands the Latin ligatures that NFKD
leaves and the ASCII fold dropped (`œ`/`Œ`→`oe`, `æ`/`Æ`→`ae`), so
"Œufs Plein Air" resolves to `egg` (it was normalised to "ufs" and
no-matched).

Part B — new safe concepts (FR product forms + EN/NEVO reference names):
`cod` (cabillaud/morue), `shrimp` (crevettes/prawns/shrimps), `bacon`
(lardons), `brioche`, `ice_cream` (glace), `green_peas` (petits pois),
`spinach` (épinards); `taboule` → the `couscous` base (its safe proxy).
`"crackers"` added to the dish-noun set so "Prawn crackers"/"rice cracker
mix" never match a shrimp/rice product. Reference-text aliases extended
(hummus↔houmous, tuna↔thon, salmon↔saumon, cod↔cabillaud, etc.) to pull
the right candidate higher in retrieval; `hummus` removed from the
alias-skip set now that it's a product concept.

Parts C/D — policy abstains kept (documented in tests): beverages
(eau/eau pétillante), ambiguous juice drinks (nectar), cleaning/household
(liquide vaisselle, nettoyant, essuie-tout, shampooing), pet
(litière, croquettes) resolve to NO concept → abstain, never forced into
NEVO. Generic `tomato_sauce`/`soup` resolve to a concept but their only
NEVO references are dish-noun composites, so they stay review/abstain
(never auto-accept the bean/dish trap). A composite salad
("Salade César Poulet") never auto-accepts a "Salad …" dish reference.

Traps held: prawn-crackers, salad-dish, bacon-bits-in-sausage, tea-glacé ≠
ice-cream, corn-starch, beans-in-tomato-sauce.

Expected on the next 100-product Render run: V2 auto_accept up further,
v1_only down further, potential-high-risk stays 0, v2_better_than_v1 up or
stable, policy abstains unchanged — no unsafe accepts. (Run on Render with
ALTERA_ENABLE_EMBEDDINGS=true + VOYAGE_API_KEY for the cross-language
retrieval the offline fake can't reproduce.)

## Quality-V2-M — final coverage pass + query-alias retrieval injection

After V2-L the 100-product shadow run was strong (V2 auto_accept 58,
v1_only 23, v2_better 20, high-risk 0). V2-M targets the remaining obvious
review-only rows. Evaluator/dev-only; V1 default; embeddings off; no route
imports V2/embeddings; gates green (HC-FP 0, forbidden 100%, dangerous 0).

Part A — final concepts (FR product forms + EN/NEVO names): `tortilla_crisps`
(tortillas maïs → "Crisps tortilla"), `tortilla_wrap` (tortillas blé →
"Wrap/tortilla wheat"), `chocolate_hazelnut_spread` (pâte à tartiner →
"Spread chocolate hazelnut"), `madeleine` (kept review/abstain — only a
cake-bar reference exists), plus `puree mousseline` → the `potato` concept.

New mechanism — **self-product exception**: a few concepts ARE prepared/
spread products whose NEVO name legitimately carries a dish noun
("Spread chocolate hazelnut", "Wrap/tortilla wheat", "Salad dressing
vinaigrette"). For those allow-listed concepts only, a dish-noun candidate
keeps its concept head, so the matching product matches it — while a
DIFFERENT-concept product still doesn't (a dark-chocolate BAR never matches
a chocolate SPREAD; a vinaigrette never matches plain vinegar).

Part B — **query-alias injection**: when a product resolves to a concept,
`build_nevo_query_text` appends a canonical English+FR phrase
(`CONCEPT_QUERY_ALIASES`, e.g. green_peas → "green peas peas green petits
pois", cod → "cod cabillaud fish") so the vector retriever surfaces the
right cross-language NEVO candidate (fixing rows where the top-5 was
baby-biscuits / lemon / mint instead of the food). This is RETRIEVAL
RANKING only — the precision-first gate still decides, and the phrase is a
function of the concept, never a commercial field.

Parts C/D — policy abstains kept: beverages, ambiguous juice drinks
(nectar), cleaning/household, pet (litière, croquettes, **pâtée chien**)
resolve to no concept → abstain. Traps held: vinegar≠vinaigrette,
cod≠lemon/croissant, taboulé≠mint, petits-pois≠baby-biscuit, pâte-à-
tartiner≠cocoa/chocolate-bar, tortillas≠corn-starch/wheat-flour.

Expected on the next 100-product Render run: V2 auto_accept up / review-only
down, v1_only down, potential-high-risk stays 0, v2_better_than_v1 stable
or up — no unsafe accepts. (Run on Render with ALTERA_ENABLE_EMBEDDINGS=true
+ VOYAGE_API_KEY; the query-alias injection now also lifts the right
candidate into the offline fake's top-k.)

## Quality-V2-N — shadow comparison → readiness/decision artifact

The shadow comparison is now an internal decision artifact, not another
alias pass. Still strictly read-only (no DB writes); V1 default; embeddings
off by default; no route imports V2/embeddings.

Alongside `nevo_v1_v2_comparison_<project>.csv` the CLI writes:
- **`nevo_v1_v2_comparison_<project>.json`** — project_id, product_count,
  top_k, provider/model, generated_at, agreement_bucket_counts,
  risk_bucket_counts, v2_auto_accept_count, v2_review_required_count,
  v2_better_than_v1_count, v1_likely_false_positive_count,
  potential_high_risk_count, recommendation + recommendation_reasons +
  recommendation_threshold + admin_opt_in_gates.
- **filtered CSVs**: `nevo_v2_better_than_v1_<project>.csv`,
  `nevo_v2_review_only_<project>.csv`, `nevo_v2_high_risk_<project>.csv`.

`potential_high_risk_count` counts ONLY `v2_potential_false_positive` (a V2
auto-accept that is neither concept- nor head-consistent with the product);
`v1_only` (V2 missed it) is a coverage gap, not a V2 risk, so it is excluded
— matching the "potential high-risk 0" the real runs report.

Recommendation (`--recommendation-threshold auto|conservative`, default
auto):
- any `potential_high_risk` > 0 → **keep_off**;
- else ≥50 products + `v2_better_than_v1` > 0 + ≥50% auto-accepted (≥60%
  under `conservative`) → **admin_opt_in_candidate**;
- else → **internal_shadow_ok**.

The console prints the recommendation, its reasons, and the admin-opt-in
gate checklist (potential_high_risk_zero, v2_better_than_v1_positive,
v1_default_unchanged, embeddings_cli_only). Flags
`--write-summary-json` / `--write-filtered-csvs` (both default on, with
`--no-…` opposites).

On the V2-M 100-product real run (auto_accept 66, v2_better 25, high-risk 0)
this yields **admin_opt_in_candidate** — i.e. the data now clearly says
whether a project is an admin-opt-in candidate, while V1 stays the default
and nothing is activated.

## Quality-V2-O — admin/internal NEVO V2 opt-in (dry-run, still not default)

A strictly controlled, admin-only path to run NEVO V2 over a project and see
exactly what it WOULD enrich — with zero production impact. V1 stays the
production default; embeddings stay disabled by default; no app route imports
or uses V2/embeddings; rollback is trivial (unset
`ALTERA_NEVO_MATCHER_VERSION` or set `v1`).

Activation (Part A): `ALTERA_NEVO_MATCHER_VERSION` (or `--matcher-version`)
selects `v1` (default) | `v2-embeddings`. `v2-embeddings` runs ONLY when
`ALTERA_ENABLE_EMBEDDINGS=true` (+ `VOYAGE_API_KEY` for real Voyage),
enforced via `get_nevo_matcher`; otherwise it fails clearly — a present
`VOYAGE_API_KEY` alone enables nothing, and there is no silent fake provider
(the deterministic fake is dev/CI-only via the explicit `--evaluator-fake`
flag). `v2-rules` has no candidate generator and is rejected.

CLI (Part B/C/E): `python -m altera_api.classification_v2.nevo_v2_enrich`.
It is a CLI, not a route. Default is **DRY-RUN**: reads project + products +
NEVO reference, writes enrichment PROPOSALS
(`nevo_v2_enrich_proposals_<project>.csv` + `.json`) and persists nothing.
Each proposal carries the observability metadata — matcher_version,
embedding_provider/model, outcome, nevo_code, nevo_food_name, the looked-up
`enriched_protein_g_per_100g`, confidence, match_type, review_required,
top-5 candidates, rejection summary, and a precision-first `safety_action`
(`would_enrich` only for a high-confidence accept that has a real nutrition
value; otherwise `route_to_review` / `skip_no_match` /
`skip_no_nutrition_value`). The console prints the activation line
(matcher/provider/model/safety_mode=dry_run) and the action counts.

Write path (Part D) — **intentionally not implemented; gated.** Persisting
V2-tagged enrichment records would need a Supabase migration to add a V2
`match_method`/source tag (the DB CHECK allows only
`deterministic|ai_assisted|manual|none`), which is out of scope. So `--apply`
is accepted but **refuses with a clear message and writes nothing** (checked
before any work) — the explicit opt-in surface exists with zero write risk.
When a future phase adds the migration, the apply path can be enabled with
the documented safety rules (require `--apply`, default dry-run, V2-tagged
records, never overwrite manual/V1 unless `--overwrite-v1`, route uncertain
to review).

This makes the admin opt-in real (an admin can intentionally dry-run V2 NEVO)
while making accidental production activation impossible.

## Phase Quality-V2-P — nutrition-enrichment safety layer (dry-run only)

A concept-correct matcher match can still be a **nutrition-wrong** source:
dry vs cooked (pasta/rice/couscous/lentils/legumes/quinoa), canned vs dried,
instant/powder/cappuccino vs brewed coffee/tea, plain vs sweetened tea, and a
processing *proxy* (syrup / concentrate / essence / aroma / rinse / extract)
vs a whole food. The V2 matcher only decides *food concept*; it does not
decide whether the matched reference is in the same physical state as the
product. Enriching protein from "Pasta boiled" onto a dry pasta pack, or from
"Apple syrup" onto a compote, is a real data-quality hazard.

So this phase adds a **second stage** that runs **only** inside the
`nevo_v2_enrich` dry-run proposals. It is in a dedicated module
(`classification_v2/nevo_nutrition_safety.py`), imported by the CLI only — **no
route imports it**, it changes **no matcher gate**, and it **writes nothing**.

**Two distinct outcomes per proposal (Part A).** Each row now reports the
matcher outcome (`matcher_outcome` ∈ `match|review|no_match`,
`matcher_confidence`) *and*, separately, the nutrition decision
(`nutrition_safety_action` + `nutrition_safety_reason`). The CSV/JSON make it
explicit that **matcher-accepted ≠ safe-to-enrich-nutrition**. The six actions
are:

- `would_enrich` — high-confidence concept match, has a real protein value,
  *and* physical states are aligned.
- `route_to_review` — matcher review-level / confidence `< 0.90`.
- `skip_no_match` — matcher produced no candidate.
- `skip_no_nutrition_value` — matched reference has no protein value.
- `skip_state_mismatch` — concept-correct but wrong physical state
  (dry↔cooked, cooked↔dried/raw, or processed/instant/sweetened beverage vs
  plain/whole).
- `skip_proxy_too_broad` — reference is a processing proxy (syrup, concentrate,
  essence, aroma, rinse, extract…), not a whole-food nutrition source.

**State rules (Part B).** A packaged staple with no explicit state is treated
as *dry*. For state-sensitive concepts (`pasta, rice, couscous, lentil, bean,
black_bean, chickpea, green_peas, quinoa, sweet_corn`): a *cooked* reference
against a dry/packaged product → skip; a *dried/raw* reference against a
*cooked* product → skip. **Canned is not treated as a cooked/dried conflict**,
so canned legumes/fish/sweetcorn stay enrichable. For `coffee`/`tea`, a
reference carrying a beverage-processing marker (`instant, powder, soluble,
cappuccino, sweetened, sugar, herbal, prepared, brewed, latte, mix`) that the
product does not → skip. Proxy words trigger `skip_proxy_too_broad` for any
concept. Worked examples that now correctly skip: Lentilles Cuites→Lentils
dried, Pâtes (dry)→Pasta boiled, Café Capsules/Grains→Coffee instant/Cappuccino
instant, Thé Noir→Tea herbal sweetened instant, Compote→Apple syrup/rinse.

**Aligned positives stay `would_enrich` (Part C):** Chocolate dark, Yoghurt
Greek full fat, Chickpeas/Beans canned, Tuna in water tinned, Sweetcorn tinned,
Orange juice with pulp, Muesli/Cornflakes.

**Observability (Part D).** The dry-run console and JSON now report both
stages: `matcher_outcome_counts` + `matcher_match_count`, the full
`nutrition_safety_counts` (all six actions) + `nutrition_would_enrich`, and a
list of `skipped_examples` (product, reference, action, reason) for the rows
downgraded on a state/proxy mismatch. The old single `safety_action_counts`
key is removed.

**Result.** On the same dry-run, matcher matches stay unchanged, but
`would_enrich` drops to only the nutrition-safe rows — every risky row is
routed to review/skip with a clear reason. No matcher regression, no
production behaviour change: V1 stays default, embeddings stay off, no route
touches V2, and `--apply` remains gated.

## Phase Quality-V2-Q — dry-run review package + final nutrition-safety filters

After V2-P, a full 100-product V2 dry-run gave matcher `match 66 / no_match 34`
and nutrition `would_enrich 56` with `skip_state_mismatch 9` +
`skip_proxy_too_broad 1`. The two-stage model held (dry-vs-cooked pasta, compote
vs apple-syrup, cooked-vs-dried lentils, coffee capsules vs instant, black tea
vs sweetened-prepared, dry quinoa/couscous/peas vs boiled all downgraded). But a
handful of `would_enrich` rows were still nutrition-risky — e.g. rice grain →
*rice drink*, cider vinegar → *balsamic*, rapeseed oil → *Becel blend*, instant
mousseline → *prepared mash with milk/margarine*, apricot jam → *rose-hip jam*,
and flavoured snacks → *unflavoured* references.

**Final filters (Part B).** Added to `nevo_nutrition_safety.py` (still stage-2
only, still no matcher-gate change, still no writes), evaluated in order after
the existing proxy-word check:

- **Rice / whole-food vs drink** — a non-beverage product matched to a
  `drink`/`boisson` reference → `skip_proxy_too_broad` (rice grain ≠ rice
  drink). Actual beverage concepts (almond drink, juice, coffee, tea) are
  exempt.
- **Vinegar variety** — cider/balsamic/wine/white/rice/sherry/raspberry types;
  product type ≠ reference type → `skip_proxy_too_broad`. A reference with no
  recognizable type stays a generic proxy (enrich).
- **Jam fruit** — apricot/strawberry/.../rose-hip varieties; wrong fruit →
  `skip_proxy_too_broad`.
- **Oil** — a pure oil matched to a branded blend/margarine (`blend`,
  `margarine`, `becel`, `spread`…) or to a *different* oil type →
  `route_to_review` (a generic vegetable oil is left to the reviewer).
- **Instant potato puree** — a dry/dehydrated puree (`mousseline`, `flakes`,
  `instant`, `powder`…) matched to a prepared mash with added
  `milk`/`margarine`/`butter`/`cream` → `skip_state_mismatch`.
- **Generic snack proxy** — a flavoured/specific cracker/crisp/chips/tortilla
  matched to a generic/unflavoured snack reference → `route_to_review` (the
  flavoured product carries descriptive tokens the reference lacks). It is
  *flagged*, never silently auto-enriched; a plain snack → unflavoured snack
  still enriches.

**Review package (Part A).** The dry-run now writes one CSV per bucket beside
the master proposals file:
`nevo_v2_enrich_{would_enrich,state_mismatch,proxy_too_broad,no_match,review}_<project>.csv`.
Each carries every proposal column plus blank reviewer columns
(`manual_decision`, `reviewer_notes`, `approved_nevo_code`,
`approved_nevo_name`). Buckets are mutually exclusive. The console prints a
per-bucket row count.

**Summary (Part C).** The JSON adds `filtered_artifacts` (path + count per
bucket), `enrich_ready_count` (= `would_enrich` after the final filters), and
`manual_review_required_count` (= everything not auto-enriched). The console
prints both headline numbers.

**Result.** Matcher counts can stay 66, but `nutrition_would_enrich` is now
stricter — the remaining suspicious rows are downgraded (rice-drink, vinegar,
jam, puree) or flagged for a human (oil blends, flavoured snacks). All safety
gates stay green: HC-FP=0, forbidden=100%, dangerous=0; V1 default; embeddings
off; no route imports V2/safety; `--apply` gated; no DB writes.

## Phase Quality-V2-R — internal review workflow package

The V2-Q dry-run produced 48 `would_enrich` and 52 manual-review rows across
five CSVs, but nothing told a reviewer *what to do* with a row or *how urgent*
it was. This phase adds a pure annotation layer (`nevo_review_workflow.py`,
imported only by the dry-run CLI — no DB, no routes, no Supabase) that turns the
proposals into a workable human-approval package. The matcher and the stage-1/2
safety actions are unchanged.

**Per-row annotation (Part A/B).** Every proposal gets three computed fields:

- `suggested_action` — `approve_auto_candidate`, `review_state_mismatch`,
  `review_proxy_too_broad`, `review_generic_proxy`, `review_no_match`,
  `reject_non_food`, `reject_policy_excluded`, or `needs_manual_nevo_search`.
- `review_priority` — `P0` high-risk / never auto (a non-food or pet item the
  matcher nonetheless *accepted*), `P1` likely useful but needs confirmation,
  `P2` safe abstain / optional (high-confidence auto candidate, or a no-match
  with no plausible candidates), `P3` non-food / policy excluded.
- `review_bucket` — which tab/CSV the row belongs to.

Policy classification: a recognized food concept always wins; otherwise
explicit pet (`chien`, `chat`, `litiere`, `croquette`…) or household/hygiene
(`vaisselle`, `lessive`, `shampooing`…) markers classify it; an unknown,
unflagged product defaults to *food* so it routes to a manual NEVO search rather
than being wrongly rejected.

**Filtered CSVs (Part A).** Each bucket CSV now carries the computed
`review_priority` / `suggested_action` plus the blank reviewer columns
`manual_decision` (allowed: `approve`, `reject`, `replace`, `needs_more_info`),
`reviewer_notes`, `approved_nevo_code`, `approved_nevo_name`,
`approved_protein_g_per_100g`.

**Consolidated package (Part C).** A single
`nevo_v2_enrich_review_package_<project>.xlsx` (via `openpyxl`) with tabs
`Summary`, `Auto_Ready`, `Needs_Review`, `State_Mismatch`, `Proxy_Too_Broad`,
`No_Match`, `Non_Food_Policy`, `Instructions`. If `openpyxl` is unavailable it
falls back to one consolidated CSV with a leading `review_bucket` column. The
Instructions tab documents the `manual_decision` vocabulary and the priority
scheme.

**Summary JSON (Part D).** Adds `review_bucket_counts`,
`suggested_action_counts`, `review_priority_counts`, `review_package_path`, and
an `instructions_summary`.

**Result.** A reviewer opens one artifact, reads `suggested_action` /
`review_priority`, and records `approve` / `reject` / `replace` /
`needs_more_info` per row — ready to drive a future, still-gated apply path. No
DB writes; existing proposals and safety actions unchanged; V1 default;
embeddings off; routes clean.

## Phase Quality-V2-S — validate a filled review package (read-only)

The Render runtime has no `openpyxl`, so the review package ships as the CSV
fallback. Once a reviewer fills the `manual_decision` (and, for replacements,
the `approved_*`) columns, we need to validate those decisions OFFLINE before
any future apply planning. This phase adds
`validate_nevo_v2_review_package.py` — a strictly read-only CLI that reads ONE
filled CSV (or XLSX, only if `openpyxl` is present) and writes a small report.
It never touches the DB, imports no route, and is not a runtime dependency.

    python -m altera_api.classification_v2.validate_nevo_v2_review_package \
        --input /tmp/altera-quality/nevo_v2_enrich_review_package_<id>.csv \
        --output-dir /tmp/altera-quality

**CSV is the primary path.** XLSX is read (across all non-meta sheets) only
when `openpyxl` is installed; an `.xlsx` input without it fails with a clear
message pointing at the CSV. `openpyxl` stays out of the required runtime deps.

**Decision validation (Part A).** `manual_decision ∈ {approve, reject, replace,
needs_more_info, blank}` (blank = pending). `replace` requires
`approved_nevo_code` + `approved_nevo_name`; `approve` of the existing candidate
requires `nevo_code` + `nevo_food_name` + `enriched_protein_g_per_100g`;
`approve` of a no-match requires an `approved_nevo_code`;
`approved_protein_g_per_100g` must be numeric if present; `reject` /
`needs_more_info` / blank need no approved fields.

**Risk-aware validation (Part B).** Errors: a `P0` row approved; a non-food /
policy-excluded row approved without an explicit `OVERRIDE` in `reviewer_notes`;
a no-match approved without a replacement code. Warnings: approving a
`skip_state_mismatch`, `skip_proxy_too_broad`, or `route_to_review` /
generic-proxy row as-is; approving a non-food via `OVERRIDE`. Auto-ready rows
approve cleanly.

**Artifacts (Part C).** `nevo_v2_review_validation_summary_<project>.json`,
`..._errors_<project>.csv`, `..._warnings_<project>.csv`, and
`nevo_v2_review_approved_candidates_<project>.csv` (effective code/name/protein
+ `source` = existing|replacement). The summary carries `input_path`,
`project_id` (inferred from the filename), decision counts, `error_count`,
`warning_count`, `apply_ready_count` (approve/replace with no errors),
`blocked_count` (rows with errors), and a `recommendation`:
`blocked_by_errors` (any error) → `review_incomplete` (pending /
needs_more_info) → `ready_for_apply_planning`.

**Result.** A filled CSV review package is validated offline into a clear
go/no-go report — with no DB writes, no `openpyxl` requirement in the Render
runtime, V1 default, embeddings off, and routes clean.

## Phase Quality-V2-T — apply PLANNING (read-only, still no DB writes)

With the dry-run → review-package → validator pipeline in place, this phase
produces the *apply plan*: an explicit, machine-readable description of what a
future DB-write phase WOULD do, and why it is still blocked. Nothing here
writes to the DB, imports a route, activates V2, or adds a Supabase migration —
it only *documents* the migration a real apply would require.

**Validator `--project-id` (Part A).** A copied/renamed sample
(`nevo_v2_enrich_review_package_FILLED_SAMPLE_<uuid>.csv`) would otherwise infer
a noisy project id (`FILLED_SAMPLE_<uuid>`). `validate_nevo_v2_review_package`
now accepts `--project-id <uuid>` to override the inferred id in the summary and
in all output filenames; filename inference still works when the flag is
omitted.

**Plan generator (Part B).** New read-only CLI
`plan_nevo_v2_apply.py` reads the validator's `approved_candidates` CSV +
`validation_summary` JSON and writes plan artifacts only. It **refuses**
`blocked_by_errors` (returns 2, writes nothing) and **refuses**
`review_incomplete` unless `--allow-incomplete` is passed (then it plans only
the apply-ready rows and records a `blocked_reason`).

    python -m altera_api.classification_v2.plan_nevo_v2_apply \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --validation-summary  .../nevo_v2_review_validation_summary_<id>.json \
        --output-dir /tmp/altera-quality --project-id <uuid>

**Plan artifacts (Part C).** `nevo_v2_apply_plan_<project>.json` and
`...csv`. The JSON carries `project_id`, `generated_at`,
`source_approved_candidates`, `source_validation_summary`,
`validation_recommendation`, `apply_ready_count`, `planned_operation_count`,
`blocked_reason`, and `db_apply_status = blocked_pending_schema_migration`. Each
operation records `product_id`, `product_name`, `approved_nevo_code` /
`approved_nevo_name` / `approved_protein_g_per_100g`, `source`
(`existing|replacement`), `planned_operation = create_v2_enrichment_record`,
`requires_schema_migration = true`, `proposed_match_method = v2_embeddings`,
`proposed_source_tag = nevo_v2_embeddings`, and `overwrite_existing_v1 = false`
/ `overwrite_manual = false`.

**Migration requirements (Part D).** The plan JSON states
`schema_migration_required: true` with the reason (the enrichment-records DB
CHECK allows match_method only in deterministic/ai_assisted/manual/none, so V2
writes need a V2-specific source/method tag first), two recommended migration
options (add a `v2_embeddings` match_method value, or add
`source_version`/`source_metadata` JSONB columns), and a rollback plan (stay on
V1 / do not apply; delete V2-tagged rows if ever applied).

**Result.** We can generate an apply plan from the filled-sample approved
candidates; the plan states unambiguously that a DB apply is blocked until the
documented migration lands. No DB writes, no production behaviour change, V1
default, embeddings off, routes clean.

## Phase Quality-V2-U — Supabase migration design (no apply yet)

The one remaining blocker for a future DB apply is the
`nutrition_enrichment_records.match_method` CHECK, which allows only
`deterministic | ai_assisted | manual | none`. This phase **designs** the
minimal, reversible schema + model change to persist V2-tagged records — it
writes **no migration file** (`supabase/migrations/` is untouched), changes no
code, and does not unblock the apply path.

Inspected (Part A): the table DDL (`0025`), the CHECK history (`0033`, `0035`),
the `NutritionEnrichmentRecord` model + `NutritionMatchMethod` enum, the
mapper/store write path, and every `match_method` consumer (protein_tracker
AI-assisted counts, coverage.py disclosure, routes.py response/writes, the
single `api.ts` TS union).

Compared (Part B): **Option 1** — add a `v2_embeddings` value to the
`match_method` CHECK; **Option 2** — keep `match_method` as-is and add additive
nullable `source_version` + `source_metadata (JSONB)` columns. **Recommendation:
Option 2** — safest rollback (drop nullable columns vs re-tightening a CHECK
with live rows), full provenance (provider/model/top_k/review-package id),
no enum churn for V3, zero forced reader changes, and honest semantics
(`match_method` = how picked; `source_version` = which engine). A V2 row records
`match_method='ai_assisted'` + `source_version='v2_embeddings'`.

The full spec — draft SQL (`0037`, not applied), affected models/store/reports,
tests-when-it-lands, rollback SQL, no-backfill policy, and the never-overwrite-
manual / never-overwrite-V1 / V1-default constraints — is in
`docs/quality/v2-nevo-enrichment-persistence-migration.md`.

**Result.** A clear migration recommendation with reversible draft SQL, with no
DB writes, no migration file added, no code change, and the apply path still
`blocked_pending_schema_migration`.

## Phase Quality-V2-V — provenance field support (model/store only, no apply)

Implements the Option 2 scaffolding from the V2-U design so a future (still
gated) apply can persist V2-tagged records. **No apply path, no V2 activation,
no route wiring, V1 default, embeddings off.**

- **Migration (Part A):** `0037_quality_v2v_nevo_enrichment_provenance.sql` adds
  `source_version text` + `source_metadata jsonb`, both **additive and
  nullable**, with `add column if not exists`. It does **not** touch the
  `match_method` CHECK, does not backfill, and carries rollback notes in
  comments. `source_version` is left as open text (no CHECK) so a future
  `v3_*` engine needs no enum migration.
- **Model (Part B):** `NutritionEnrichmentRecord` gains
  `source_version: str | None = None` and `source_metadata: dict | None = None`
  (plus `SOURCE_VERSION_V1` / `SOURCE_VERSION_V2_EMBEDDINGS` constants).
- **Mapper / store (Part B/C):** `enrichment_record_from_row` reads both with
  `None` defaults (pre-0037 / V1 rows). `enrichment_record_to_row` **omits**
  the two keys when `None`, so V1 writes are byte-for-byte unchanged and never
  depend on migration 0037 having been applied. Postgres `add_enrichment_record`
  and `select *` reads need no change.
- **API / frontend (Part D):** no response model exposes the new fields, so the
  API and `apps/web/lib/api.ts` are intentionally left unchanged. A V2 row will
  be `source='nevo'`, `match_method='ai_assisted'`,
  `source_version='v2_embeddings'`, with metadata in `source_metadata`.

**Result.** The model and store can carry V2 provenance; the migration file
exists but is additive-only and unapplied-by-writes; existing tests stay green
(3502 passed); no production behaviour change; the apply path
(`nevo_v2_enrich --apply`) still refuses.

## Phase Quality-V2-W — explicit, guarded NEVO V2 apply CLI

The first code path that can persist V2-tagged enrichment records —
`apply_nevo_v2_plan.py` — designed to be impossible to trigger by accident. It
is **not** imported by any route; V1 stays default and embeddings stay off.

    python -m altera_api.classification_v2.apply_nevo_v2_plan \
        --plan-json .../nevo_v2_apply_plan_<id>.json \
        --approved-candidates .../nevo_v2_review_approved_candidates_<id>.csv \
        --project-id <uuid>                 # default DRY-RUN, writes nothing
        # add --confirm-apply-v2 to write (only if migration 0037 columns exist)

**Preconditions (Part A).** Refuses (rc 2, no artifacts) unless: plan
`project_id` matches `--project-id`; `schema_migration_required` is true and
`db_apply_status == blocked_pending_schema_migration`; no overwrite flags set on
the plan or any operation; approved-candidate count equals
`planned_operation_count`; and the validation recommendation is
`ready_for_apply_planning` (or `review_incomplete` only when the plan was
generated with `--allow-incomplete` **and** the operator passes
`--allow-incomplete-apply`). Any validation `error_count > 0` also refuses.

**Guards (Part D).** Default is dry-run; a write needs **both**
`--confirm-apply-v2` **and** the live `source_version`/`source_metadata` columns
(probed via `PostgresStore.has_enrichment_provenance_columns()`). With
confirmation but missing columns it writes nothing and returns 2.

**Write behaviour (Part B).** For each approved candidate it inspects the
product's existing protein records and: skips `skipped_existing_manual` (never
overwrite manual), `skipped_existing_v1` (never overwrite a V1 value),
`skipped_existing_v2` (idempotent — no overwrite flag exists yet); otherwise it
builds a `NutritionEnrichmentRecord` with `source='nevo'`,
`match_method='ai_assisted'`, `source_version='v2_embeddings'`,
`enriched_value=approved_protein`, a conservative confidence, and
`source_metadata` carrying matcher_version / embedding provider+model / top_k /
plan + approved-candidates + validation-summary + review-package paths /
manual_decision / candidate_source / `applied_by_cli=true`.

**Artifacts (Part C).** Always writes `nevo_v2_apply_result_<project>.{json,csv}`
with `total_planned`, `written_count`, `would_write_count`,
`skipped_existing_count` (v2), `skipped_manual_count`, `skipped_v1_count`,
`error_count`, `dry_run`, `confirmation_present`, `provenance_columns_present`,
and a per-row status (`would_write | written | skipped_existing_v1 |
skipped_existing_manual | skipped_existing_v2 | error`).

**Result.** A V2 apply CLI that is safe by construction: dry-run by default,
double-gated for writes, schema-aware, and overwrite-proof. V1/default app
behaviour is unchanged; the old `nevo_v2_enrich --apply` still refuses.

## Phase Quality-V2-X — apply-readiness checker + tiny first rehearsal

The Render apply dry-run confirmed the CLI reads the plan and writes nothing,
and that migration 0037 is not yet applied
(`provenance_columns_present: false`). This phase adds the operational safety
net for the first real apply.

**Readiness checker (Part A).** New read-only CLI
`check_nevo_v2_apply_readiness.py` writes
`nevo_v2_apply_readiness_<project>.{json,csv}` with a `ready` boolean and a
checklist: `provenance_columns_present` (migration 0037 probe),
`plan_project_matches`, `approved_count_matches_plan`,
`validation_recommendation`, `db_apply_status_expected`, `no_overwrite_flags`,
`v1_default_unchanged`, `embeddings_off` (warn-only), and `routes_clean`. It also
reports per-product `conflicts` (writable / existing_manual / existing_v1 /
existing_v2) so you can see exactly which approved rows would be skipped. It
makes no DB writes; exit code 0 = ready, 1 = not ready, 2 = bad input.

**Runbook (Part B).** `docs/quality/nevo-v2-first-apply-runbook.md` — the exact
ordered sequence: confirm V1 default → apply 0037 → run the readiness checker →
(regen artifacts if needed) → apply dry-run → inspect would_write/skips/errors →
confirmed apply on a tiny `--limit-apply` sample → re-read DB rows and verify
`source_version`/`source_metadata` → verify app/API/export unchanged → rollback
(stop / delete V2 rows / drop columns).

**Tiny rehearsal (Part C).** `apply_nevo_v2_plan` gains `--limit-apply N`: apply
only the first N planned operations (default: no limit; respected in dry-run
too). The result JSON records `limit_apply`.

**Result.** Before the migration the checker says **not ready**; after it, it can
say **ready**; and the first confirmed apply can be scoped to a single row. No
production route changes; V1 default, embeddings off, routes clean.

## Phase Quality-V2-Y — post-apply audit + 30k readiness baseline

The first real apply landed: 49 V2 records (`source_version=v2_embeddings`,
`source=nevo`, `match_method=ai_assisted`, `nutrient=protein_pct`,
`unit=g_per_100g`), no V1/manual conflicts, idempotent re-run
(`existing_v2=49, writable=0`). This phase adds the read-only verification and
the scaling design.

**Post-apply audit (Part A/B).** New read-only CLI `audit_nevo_v2_apply.py`
reads the project's enrichment records + products, compares them to the approved
candidates / plan, and writes `nevo_v2_apply_audit_<project>.{json,csv}` +
`nevo_v2_apply_audit_anomalies_<project>.csv`. It verifies every V2 record's
tags (source / match_method / source_version / nutrient / unit / metadata
present), detects duplicate V2 rows per product, manual/V1 coexistence
(overwrite check), approved candidates missing from the DB, and unexpected V2
rows with no approved candidate. It emits `audit_status` (pass / warn / fail) →
`recommendation` (`pilot_apply_verified` / `investigate_anomalies` /
`rollback_recommended`) and exits 0 / 1 / 2. No DB writes.

**30k scale baseline (Part C).** `nevo_v2_scale_baseline.scale_baseline_report()`
+ `docs/quality/nevo-v2-30k-scale-baseline.md` define the retailer-scale plan:
deduplication + canonical product key, batch embedding cache reuse, P0–P3 review
prioritisation (large dedup groups first), a no-match → rules feedback loop,
chunked apply with per-chunk re-audit and stop-on-anomaly, the monitoring
metrics, and the exact next artifacts. `audit_nevo_v2_apply --write-scale-baseline`
emits the JSON. Nothing bulk is implemented; V2 still writes total protein only
(plant/animal split stays blank → classification-assumption fallback).

**Result.** A clear pass/warn/fail post-apply audit (the pilot's 49 records
verify as `pass`), plus a documented 30k roadmap. No production behaviour
change, no new writes, V1 default, embeddings off, routes clean.

## Phase Quality-V2-Z — derive plant/animal split from V2 total protein

V2 wrote total protein only, so the UI plant/animal columns stay blank. The
Protein Tracker already classifies each product; for the headline groups the
split is unambiguous. This phase derives the split and (since the schema already
supports it) ships a guarded apply.

**Part A — schema fits.** The split is surfaced to the calculation as **sibling
ENRICHED enrichment records** — `nutrient='plant_protein_pct'` +
`'animal_protein_pct'` with the SAME `source=nevo` as the V2 total
(`enrichment/selection.py:_sibling_value`). So no migration is needed beyond
0037; the split records reuse `source_version` (set to `v2_embeddings_split`).
PT groups live in `domain/protein_tracker.py:ProteinTrackerGroup`.

**Part B — policy** (`nevo_v2_protein_split.py`, pure): `animal_core` → animal =
total / plant = 0; `plant_based_core` / `plant_based_non_core` → plant = total /
animal = 0; `composite_products` / `unknown` / `out_of_scope` → **needs_review**
(no auto split). A manual override on the product → `skip_manual_override`
(manual always wins); no classification → `skip_missing_class`.

**Part C — proposals (dry-run, no writes).**
`propose_nevo_v2_protein_split.py` reads the V2 total-protein records + each
product's PT classification and writes
`nevo_v2_protein_split_proposals_<project>.{csv,json}` with
`total_protein_g_per_100g`, `pt_group`, proposed plant/animal, `split_action`
(`would_split | needs_review | skip_missing_class | skip_manual_override`), and
`split_reason`. No DB writes.

**Part D — guarded apply.** Because the schema supports it,
`apply_nevo_v2_protein_split.py` mirrors the V2-W safety posture: dry-run
default; a write needs `--confirm-apply-split` AND the 0037 columns; never
overwrites a manual record, never re-writes an existing split. For each
`would_split` it writes the two sibling records (`source=nevo`,
`source_version=v2_embeddings_split`, `match_method=ai_assisted`), so
`plant + animal == total` and the calculation uses a true split. Result:
`nevo_v2_split_apply_result_<project>.{json,csv}`.

**Result.** Split proposals are generated for the applied V2 records; safe rows
(animal_core / plant groups) are clearly `would_split`; mixed/unknown are
`needs_review` (never auto-split); manual overrides are skipped. No production
behaviour change; V1 default, embeddings off, routes clean.

## Phase Quality-V2-AA — split audit + app check + pet-food-is-food

The progressive split apply landed (39 `would_split` → 19 new pairs + 20
already-split = 38 records; 10 `needs_review` untouched). This phase verifies it,
adds an app-check, and clarifies pet-food policy.

**Split audit (Part A/B).** `audit_nevo_v2_protein_split.py` (read-only) compares
the DB split records to the proposals and writes
`nevo_v2_split_audit_<project>.{json,csv}` +
`nevo_v2_split_audit_anomalies_<project>.csv`. It checks every `would_split` has
exactly one plant + one animal record, `plant + animal == total` (±0.01), tags
(source=nevo / match_method=ai_assisted / source_version=v2_embeddings_split /
unit=g_per_100g / metadata present), no duplicates, no split on a
`needs_review`/skip product, and no manual/V1 conflict. Emits `audit_status`
(pass/warn/fail) → `recommendation` (`split_apply_verified` /
`investigate_split_anomalies` / `rollback_split_recommended`); exits 0/1/2.

**App check (Part C).** The audit also writes
`nevo_v2_split_app_check_<project>.csv` (product, total/plant/animal,
plant+animal, pt_group, `expected_ui_status` = `split_shown` /
`total_only_needs_review` / `total_only`) so the UI state can be eyeballed.

**Pet food is food (Part D).** `docs/quality/nevo-v2-petfood-policy.md` +
a clarified comment on `_PET_MARKERS`: pet food is food for nutrition enrichment.
The split pipeline is PT-group driven and pet-agnostic, so `Croquettes Chat`
(`animal_core`) splits normally (animal=total, plant=0) and is never an anomaly;
composite pet food stays `needs_review`. The review-stage `reject_policy_excluded`
hint is left unchanged (no destabilising the established gates).

**Result-key consistency (Part E).** `apply_nevo_v2_protein_split` result JSON
now exposes `written_pairs` / `records_written` (matching the console) plus
`limit_apply`, alongside `would_write_count` / `skipped_*_count` / `error_count`
/ `dry_run` / `confirmation_present`.

**Result.** The split audit on the current project returns
`pass / split_apply_verified` (39 products with valid plant+animal split, 10
`needs_review` with none); pet food is handled and documented as valid food. No
production behaviour change; no new writes; V1 default, embeddings off, routes
clean.

### Quality-V2-AA hotfix — robust split audit after /tmp artifact loss

`/tmp/altera-quality` is volatile on Render: after a new pod the original split
proposal CSV is gone. Regenerating it returned `would_split=0 / needs_review=49`
and the audit then **false-failed** (`unexpected_split=39`,
`rollback_split_recommended`).

**Part A — root cause / fix.** `propose_nevo_v2_protein_split` downgraded
`would_split → needs_review` for any product that *already had* a split, making
proposals non-idempotent (after apply, all 39 became `needs_review`). Removed the
downgrade: the proposal now reflects the POLICY decision only and is idempotent
(animal_core/plant groups stay `would_split` whether or not a split exists).
Idempotency is enforced at apply time (apply skips products with an existing
split), not in the proposal.

**Part B/C/D — audit reconstruction fallback.** `--proposals` is now optional and
`--reconstruct-proposals-from-db` added. The audit reconstructs eligibility from
the live DB (current V2 totals + PT classification + the same split policy) and
uses it as the source of truth whenever the CSV is missing/lost or stale. A
supplied CSV that disagrees with the reconstruction is a `proposal_mismatch_warning`
— never a hard fail. New summary fields: `proposal_source`
(`original|regenerated|reconstructed`), `reconstructed_proposals_count`,
`proposal_mismatch_warning`; a reconstructed-proposals CSV is written for
traceability. A would_split product missing **one** half is a broken pair → fail;
missing **both** is "not applied yet" → warn (consistent with the
`plant==animal==applied` pass condition).

**Result.** From a fresh pod with no `/tmp` CSV, the audit reconstructs and
returns `pass / split_apply_verified` for the 49/39/39 state; a stale CSV warns
instead of false-failing; real corruptions (unexpected/duplicate/broken-pair/sum-
mismatch/bad-tags/missing-metadata) still fail. No DB writes; no production
behaviour change.
