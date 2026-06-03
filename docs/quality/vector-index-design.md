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
(with `VOYAGE_API_KEY`). The `voyageai` SDK is imported lazily and can
be injected for tests, so the normal test suite never needs it and never
hits the network.

### Cache

`embedding_cache_key(provider, model, input_type, text)` keys vectors by
all four parts: the same text embedded as `document` vs `query` is a
different entry, and a provider/model change invalidates the cache.
`NevoVectorIndex` uses an `InMemoryEmbeddingCache` so identical
reference texts embed once.

### NEVO vector index (prototype)

`classification_v2/nevo_index.py` builds a cosine index over NEVO
reference foods (`build` embeds reference texts as documents; `search`
embeds the product query and returns the top-k nearest references).
`classification_v2/nevo_pipeline.py` then gates those candidates with
the V2 rules (`decide_with_embeddings`). Still in-memory + offline; the
pgvector/persistent backend is Quality-V2-D.

**Contract reaffirmed:** retrieval only proposes candidates; the rules
decide, and a hard rejection can never be overridden by similarity.
