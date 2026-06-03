"""Phase Quality-V2-A — embedding provider abstraction (opt-in).

Default-disabled (``ALTERA_ENABLE_EMBEDDINGS=false``); the fake
deterministic provider is the only wired backend. No network calls in
the normal test suite. Not used by any production route yet.
"""

from altera_api.embeddings.cache import (
    EmbeddingCache,
    InMemoryEmbeddingCache,
    embedding_cache_key,
    embedding_key,
)
from altera_api.embeddings.fake_provider import (
    FakeEmbeddingProvider,
    cosine_similarity,
)
from altera_api.embeddings.provider import (
    EmbeddingProvider,
    EmbeddingProviderError,
    build_embedding_provider,
    get_embedding_provider,
)
from altera_api.embeddings.voyage_provider import VoyageEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "get_embedding_provider",
    "build_embedding_provider",
    "FakeEmbeddingProvider",
    "VoyageEmbeddingProvider",
    "cosine_similarity",
    "EmbeddingCache",
    "InMemoryEmbeddingCache",
    "embedding_key",
    "embedding_cache_key",
]
