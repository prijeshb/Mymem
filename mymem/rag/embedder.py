"""
Embedding backend — Strategy pattern.

Design:
  Embedder     — Strategy ABC: one subclass per embedding backend
  OllamaEmbedder — Concrete implementation via ollama SDK
  embed_texts()  — backward-compatible module-level function
  embed_query()  — backward-compatible module-level function

Adding a new embedding backend:
  1. Subclass Embedder
  2. Implement embed()
  3. Inject via dependency injection — no changes needed in rag/ingest.py

The module-level embed_texts() and embed_query() are preserved so existing
callsites (rag/ingest.py, pipeline/query.py) and tests continue to work
without modification.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from mymem.observability.logger import get_logger

log = get_logger(__name__)

try:
    from ollama import AsyncClient  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    AsyncClient = None  # type: ignore[assignment,misc]

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM   = 768
BATCH_SIZE  = 32


# ---------------------------------------------------------------------------
# Embedder ABC — Strategy pattern
# ---------------------------------------------------------------------------

class Embedder(ABC):
    """
    Strategy: embed texts using one backend.

    Open/Closed: extend by subclassing — never modify this file.
    Liskov: every embedder is a drop-in replacement for any other.
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts. Returns one vector per text."""
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Embed a single text. Returns one vector."""
        vectors = await self.embed([text])
        return vectors[0] if vectors else [0.0] * EMBED_DIM


# ---------------------------------------------------------------------------
# OllamaEmbedder — Concrete implementation
# ---------------------------------------------------------------------------

class OllamaEmbedder(Embedder):
    """Embed texts via Ollama nomic-embed-text (or any Ollama embedding model)."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = EMBED_MODEL,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        if AsyncClient is None:
            raise RuntimeError("ollama package is required for RAG embeddings")
        self._base_url = base_url
        self._model = model
        self._batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        client = AsyncClient(host=self._base_url)
        results: list[list[float]] = []

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            try:
                resp = await client.embed(model=self._model, input=batch)
                results.extend(resp.embeddings)
            except Exception as exc:
                raise RuntimeError(
                    f"Ollama embed failed for batch starting at index {i} "
                    f"(model={self._model}, batch_size={len(batch)}): {exc}"
                ) from exc

        return results


# ---------------------------------------------------------------------------
# Backward-compatible module-level functions
# ---------------------------------------------------------------------------

async def embed_texts(
    texts: list[str],
    *,
    base_url: str = "http://localhost:11434",
    model: str = EMBED_MODEL,
) -> list[list[float]]:
    """
    Embed a list of texts.  Returns one float[768] vector per text.

    Delegates to OllamaEmbedder. In new code, instantiate OllamaEmbedder
    directly so the client can be reused across calls.
    """
    return await OllamaEmbedder(base_url=base_url, model=model).embed(texts)


async def embed_query(
    query: str,
    *,
    base_url: str = "http://localhost:11434",
    model: str = EMBED_MODEL,
) -> list[float]:
    """Embed a single query string. Returns a float[768] vector."""
    return await OllamaEmbedder(base_url=base_url, model=model).embed_one(query)
