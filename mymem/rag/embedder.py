"""
Embedding wrapper — nomic-embed-text via Ollama.

Falls back to zero vectors when Ollama is unreachable so the rest of the
pipeline degrades gracefully instead of hard-crashing.
"""

from __future__ import annotations

from mymem.observability.logger import get_logger

log = get_logger(__name__)

try:
    from ollama import AsyncClient  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    AsyncClient = None  # type: ignore[assignment,misc]

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM   = 768
BATCH_SIZE  = 32   # texts per Ollama call


async def embed_texts(
    texts: list[str],
    *,
    base_url: str = "http://localhost:11434",
    model: str = EMBED_MODEL,
) -> list[list[float]]:
    """
    Embed a list of texts.  Returns one float[768] vector per text.
    On connection error the corresponding vectors are zero-filled.
    """
    if AsyncClient is None:
        raise RuntimeError("ollama package is required for RAG embeddings")

    if not texts:
        return []

    client = AsyncClient(host=base_url)
    results: list[list[float]] = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i : i + BATCH_SIZE]
        try:
            resp = await client.embed(model=model, input=batch)
            results.extend(resp.embeddings)
        except Exception as exc:
            raise RuntimeError(
                f"Ollama embed failed for batch starting at index {i} "
                f"(model={model}, batch_size={len(batch)}): {exc}"
            ) from exc

    return results


async def embed_query(
    query: str,
    *,
    base_url: str = "http://localhost:11434",
    model: str = EMBED_MODEL,
) -> list[float]:
    """Embed a single query string. Returns a float[768] vector."""
    vectors = await embed_texts([query], base_url=base_url, model=model)
    return vectors[0] if vectors else [0.0] * EMBED_DIM
