"""Phase Quality-V2-C — Voyage AI embedding provider (opt-in).

A real embedding backend behind explicit flags. It is constructed only
when ``ALTERA_ENABLE_EMBEDDINGS=true`` AND
``ALTERA_EMBEDDING_PROVIDER=voyage``; the normal test suite never
reaches it. Document/corpus texts embed with ``input_type="document"``
and search queries with ``input_type="query"`` (the Voyage retrieval
contract).

Design notes
------------
* The ``voyageai`` SDK is imported lazily inside ``_default_client`` so
  the package is never a hard dependency of the test suite.
* A ``client`` can be injected (any object with
  ``embed(texts, model, input_type, output_dimension)`` returning an
  object with an ``embeddings`` attribute). Tests inject a mock — no
  network. There is no silent fall-back to the fake provider: if voyage
  is requested without a key/SDK, construction raises a clear error.
"""

from __future__ import annotations

import os
from typing import Any

from altera_api.embeddings.provider import EmbeddingProviderError, InputType


def _default_client(api_key: str, timeout: float, max_retries: int) -> Any:
    """Lazily build the real Voyage SDK client (network-capable)."""
    try:
        import voyageai  # type: ignore
    except ImportError as exc:  # pragma: no cover - SDK optional
        raise EmbeddingProviderError(
            "voyageai package is not installed. Add it to backend dependencies "
            "or install it in the runtime."
        ) from exc
    return voyageai.Client(
        api_key=api_key, timeout=timeout, max_retries=max_retries
    )


class VoyageEmbeddingProvider:
    """Voyage AI embeddings. Network only when actually invoked."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        dimensions: int | None = None,
        client: Any | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._model = model
        # ``dimensions`` is the configured output size (Voyage supports a
        # few). When None we report a sensible default for index sizing;
        # the real vector length comes from the API response.
        self._dims = dimensions
        if client is None:
            key = api_key or os.environ.get("VOYAGE_API_KEY")
            if not key:
                raise EmbeddingProviderError(
                    "VOYAGE_API_KEY is required for embedding-provider=voyage."
                )
            client = _default_client(key, timeout, max_retries)
        self._client = client
        # Phase Quality-V2-D — accumulate token usage across calls for
        # cost reporting (Voyage responses expose ``total_tokens``).
        self.total_tokens: int = 0
        self.call_count: int = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dims or 1024

    def _embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, Any] = {"model": self._model, "input_type": input_type}
        if self._dims is not None:
            kwargs["output_dimension"] = self._dims
        try:
            resp = self._client.embed(texts, **kwargs)
        except Exception as exc:  # surface clearly — no silent fallback
            raise EmbeddingProviderError(
                f"Voyage embedding call failed ({type(exc).__name__}: {exc})."
            ) from exc
        embeddings = getattr(resp, "embeddings", None)
        if embeddings is None:
            raise EmbeddingProviderError(
                "Voyage response has no 'embeddings' attribute."
            )
        self.call_count += 1
        tokens = getattr(resp, "total_tokens", None)
        if isinstance(tokens, int):
            self.total_tokens += tokens
        return [list(v) for v in embeddings]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        out = self._embed([text], "query")
        return out[0] if out else []
