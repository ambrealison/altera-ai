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


def get_embedding_provider() -> EmbeddingProvider:
    """Factory. Returns the fake provider unless embeddings are enabled
    AND a real backend is configured. Importing a real SDK is deferred
    so the normal test suite never needs it.

    Phase Quality-V2-A: only the fake provider is wired. A real Voyage/
    OpenAI provider is a placeholder for a later phase — this factory
    raises a clear error rather than silently making network calls.
    """
    from altera_api.quality_config import embeddings_enabled

    if not embeddings_enabled():
        from altera_api.embeddings.fake_provider import FakeEmbeddingProvider

        return FakeEmbeddingProvider()

    # Embeddings explicitly enabled — a real provider would be selected
    # here in a later phase (Voyage/OpenAI, behind its own API key env).
    raise EmbeddingProviderError(
        "Real embedding provider not yet implemented. Set "
        "ALTERA_ENABLE_EMBEDDINGS=false to use the deterministic fake "
        "provider, or wait for the V2 retrieval phase."
    )
