# Vector Index Design (V2 retrieval)

Design note for the embeddings/vector-search track. Phase Quality-V2-A
ships only the **provider abstraction + fake provider + text builders +
cache**. No pgvector, no network, no production wiring yet.

## Why embeddings

The V1 guards are token/keyword rules. They're precise on known
vocabulary but blind to paraphrase ("wrap aux falafels" vs "falafel
wrap"), synonyms, and unseen products. Embeddings let us retrieve the
*most similar labelled examples* (for classification) and the *most
similar reference foods* (for NEVO) by meaning, not exact tokens — as
a candidate generator that the precise rules then gate.

Embeddings are a **retrieval aid**, never the final decision. The rule
engine + gates still decide; retrieval only proposes candidates.

## Provider abstraction

`altera_api/embeddings/provider.py` defines `EmbeddingProvider`
(`embed_documents` / `embed_query`, `model`, `dimensions`). The
retrieval contract follows the Voyage-style `input_type` split:
indexed corpus texts use `input_type="document"`, search queries use
`input_type="query"`. The interface captures this without hardcoding a
vendor. `get_embedding_provider()` returns the deterministic
`FakeEmbeddingProvider` unless `ALTERA_ENABLE_EMBEDDINGS=true` (a real
Voyage/OpenAI provider is a later-phase placeholder).

## pgvector vs in-memory prototype

- **In-memory prototype (next):** load all example/reference vectors
  into a list, cosine-rank in Python. Fine for ≤ a few thousand
  references; zero infra; fully offline-testable with the fake
  provider. Use this to validate retrieval quality before any DB work.
- **pgvector (later):** once retrieval proves out, store vectors in
  Postgres `vector` columns with an IVFFlat/HNSW index. Same
  `EmbeddingProvider` interface; only the retriever's storage backend
  changes.

## Tables needed (pgvector phase)

- `classification_examples(id, methodology, text, text_hash,
  embedding vector, label_json, embedding_model, created_at)`
- `reference_embeddings(nevo_code, text, text_hash, embedding vector,
  embedding_model, created_at)`
- `product_embeddings` *(optional)* — cache per-product query vectors
  keyed by `text_hash` to avoid re-embedding on re-runs.

## Embedding versioning + text hashing

- Cache key = `sha256(model + "\n" + text)` (see `embeddings/cache.py`).
- The `embedding_model` column + the hashed key mean a model change
  invalidates stale vectors automatically (new key → recompute).
- Store `text_hash` so a changed source text re-embeds; an unchanged
  one is a cache hit.

## Privacy policy (hard rule)

Embedding texts contain **product descriptors only** — name, retailer
category, ingredients, labels, and (for examples) the assigned
category. They MUST NOT contain commercial/physical fields
(`items_purchased`, `items_sold`, weights, sales, margin, price).
`embeddings/text_builder.py` enforces this: every builder raises
`ForbiddenEmbeddingField` if a commercial key is present, mirroring the
AI prompt policy in `ai/policy.py`.

## Retriever flow (classification)

1. Build the product query text (`build_product_text`).
2. `embed_query(text)` → query vector.
3. Cosine-rank against `classification_examples` for the methodology.
4. Take top-K labelled examples → feed as candidates to the rule
   engine / AI prompt as supporting evidence.
5. Rules/gates decide; retrieval never auto-accepts on its own.

## NEVO candidate flow

1. Build the product query text.
2. Retrieve top-K `reference_embeddings` candidates.
3. Run each candidate through the V2 gates (`nevo_rules`):
   head-match-required, reject-secondary-ingredient,
   reject-with/without-trap.
4. Accept the best gate-passing candidate at high confidence; if none
   passes, **abstain** (better than a confident wrong match).

## Rollback strategy

- Embeddings are gated by `ALTERA_ENABLE_EMBEDDINGS` (default false).
- The matcher/pipeline version flags select V1 by default.
- The in-memory prototype is process-local and trivially disabled.
- pgvector tables are additive; dropping the feature means not
  reading them — V1 is unaffected.

## Quality-V2-C update — Voyage provider + NEVO vector index (offline)

The provider abstraction now has a **real backend**:
`VoyageEmbeddingProvider` (`embeddings/voyage_provider.py`). It honours
the `input_type` split — `document` for reference/corpus texts, `query`
for product searches — and is constructed only when
`ALTERA_ENABLE_EMBEDDINGS=true` + `ALTERA_EMBEDDING_PROVIDER=voyage`
(with `VOYAGE_API_KEY`). The `voyageai` SDK is a backend dependency (so
it ships in the Render image — see the Quality-V2-D hotfix), but it is
imported **lazily** — only when the Voyage provider is actually
constructed — and can be injected for tests, so the normal test suite
never imports it, app startup never imports it, and neither hits the
network.

### Cache

`embedding_cache_key(provider, model, input_type, text, dimensions)` keys
vectors by all five parts: the same text embedded as `document` vs
`query` is a different entry (documents and queries are separate caches),
and a provider/model/dimensions change invalidates the cache. Two
backends implement the `EmbeddingCache` protocol (`get`/`set`/`flush`):

- `InMemoryEmbeddingCache` — process-local; identical texts embed once.
- `FileEmbeddingCache` (Quality-V2-E) — a **persistent, resumable** JSON
  cache, flushed every `autosave_every` writes (and on `flush`) via an
  atomic temp-file replace, with `hits`/`misses` counters. An interrupted
  full-NEVO run resumes from disk without re-embedding completed batches.

### NEVO vector index

`classification_v2/nevo_index.py` builds a cosine index over NEVO
reference foods. `build` embeds reference texts as documents **in
`batch_size` batches** (one provider call per batch, de-duplicating
repeated texts and serving cache hits first), emitting `BuildProgress`
events so a long run is observable. `search` embeds the product query and
returns the top-k nearest references. `NevoVectorIndex.load_or_build(...)`
reuses a (persistent) cache: a second run over the same references/model
embeds nothing; a model/provider/dimensions or reference-text change
re-embeds only the affected entries.

`classification_v2/nevo_pipeline.py` then gates those candidates with the
V2 rules (`decide_with_embeddings`; `full_trace=True` records the full
top-k ranking for the evaluator). The pgvector/persistent table backend
is a later phase; the file cache covers the per-run/dev need today.

### Controlled matcher selection (Quality-V2-E)

`classification_v2/nevo_matcher.get_nevo_matcher(version)` selects
`v1` (production AI matcher) | `v2-rules` (offline gate) | `v2-embeddings`
(rules + vector index), driven by `ALTERA_NEVO_MATCHER_VERSION` and
**defaulting to `v1`**. `v2-embeddings` requires
`ALTERA_ENABLE_EMBEDDINGS=true` in production (no silent fake fallback;
`evaluator_mode=True` opts into the fake offline). No route imports it —
selecting a non-V1 matcher is an explicit, evaluator/dev-only action.

**Contract reaffirmed:** retrieval only proposes candidates; the rules
decide, and a hard rejection can never be overridden by similarity. This
remains evaluator/dev-only — V1 is the production default.
