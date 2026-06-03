"""Phase Quality-V2-A — embedding provider abstraction.

A thin interface so the rest of the codebase never depends on a
specific vendor. The retrieval contract follows the Voyage-style
``input_type`` distinction (documented in the embeddings reference):
indexed/corpus texts embed with ``input_type="document"``; search
queries embed with ``input_type="query"``. We capture this in the
interface WITHOUT hardcoding any vendor.

No provider here makes network calls unless explicitly constructed and
enabled via env (see ``ALTERA_ENABLE_EMBEDDINGS``). The default,
test-safe provider is the deterministic fake (``fake_provider.py``).
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

InputType = Literal["document", "query"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal embedding interface.

    ``embed_documents`` indexes corpus texts (examples / references);
    ``embed_query`` embeds a single search text. Implementations must
    use the retrieval-appropriate ``input_type`` internally
    (document vs query) when the backing model supports it.
    """

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class EmbeddingProviderError(RuntimeError):
    """Raised when a real provider is requested but not configured."""


class EmbeddingRateLimitError(EmbeddingProviderError):
    """Phase Quality-V2-E — raised when the embedding backend signals a
    rate limit (HTTP 429 / provider RateLimitError). Distinct from the
    generic error so the benchmark can print a friendly message, keep the
    on-disk cache intact, and exit non-zero without a traceback."""


def build_embedding_provider(
    provider_name: str,
    *,
    model: str | None = None,
    dimensions: int | None = None,
) -> EmbeddingProvider:
    """Construct a provider explicitly (used by the evaluator/CLI).

    ``fake`` → deterministic offline provider (no network, no key).
    ``voyage`` → real Voyage provider (requires ``VOYAGE_API_KEY``);
    a missing key raises :class:`EmbeddingProviderError` — never a
    silent fall-back to fake.
    """
    name = (provider_name or "fake").strip().lower()
    if name == "fake":
        from altera_api.embeddings.fake_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider()
    if name == "voyage":
        from altera_api.embeddings.voyage_provider import VoyageEmbeddingProvider
        from altera_api.quality_config import DEFAULT_EMBEDDING_MODEL

        return VoyageEmbeddingProvider(
            model=model or DEFAULT_EMBEDDING_MODEL, dimensions=dimensions
        )
    raise EmbeddingProviderError(f"Unknown embedding provider: {provider_name!r}")


def get_embedding_provider() -> EmbeddingProvider:
    """Env-driven factory. Returns the deterministic fake provider
    unless embeddings are enabled AND a real backend is selected.

    Default-safe: with ``ALTERA_ENABLE_EMBEDDINGS`` unset/false this
    returns the fake provider and never imports a vendor SDK or makes a
    network call. When embeddings are enabled it honours
    ``ALTERA_EMBEDDING_PROVIDER`` (fake | voyage).
    """
    from altera_api.quality_config import (
        embedding_dimensions,
        embedding_model,
        embedding_provider_name,
        embeddings_enabled,
    )

    if not embeddings_enabled():
        from altera_api.embeddings.fake_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider()

    return build_embedding_provider(
        embedding_provider_name(),
        model=embedding_model(),
        dimensions=embedding_dimensions(),
    )
