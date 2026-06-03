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
