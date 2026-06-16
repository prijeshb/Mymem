"""
Claim retrieval — find the similar ACTIVE claims a proposition should be reconciled
against (ADR-011 / ADR-015 Phase 3b).

Candidates are scoped to the proposition's own page (its stable ULID): re-ingesting a
concept resolves to the same page_id (ADR-013), so its existing claims are exactly the
ones a MERGE / SUPERSEDE / NOOP would act on. The embedder is injected (Strategy, ADR-006)
so this is fully testable without Ollama. Ranking is in-Python cosine — cheap because the
candidate set is bounded by one page's claims; a sqlite-vec claim index is deferred until
claim counts make that necessary (ADR-015 D8).
"""
from __future__ import annotations

import math
from pathlib import Path

from mymem.knowledge.claims import claims_for_page
from mymem.pipeline.reconcile import Candidate
from mymem.rag.embedder import Embedder


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 when either vector has zero magnitude."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def retrieve_candidates(
    db_path: Path,
    prop_text: str,
    page_id: str,
    *,
    embedder: Embedder,
    top_k: int = 5,
    min_similarity: float = 0.6,
) -> list[Candidate]:
    """Return the page's active claims most similar to `prop_text`, best first.

    Empty when the page has no active claims (e.g. a brand-new page) — the caller then
    falls through to ADD without an LLM round-trip.
    """
    actives = claims_for_page(db_path, page_id, active_only=True)
    if not actives:
        return []

    vectors = await embedder.embed([prop_text] + [c.text for c in actives])
    query_vec = vectors[0]

    scored = [
        (_cosine(query_vec, vec), claim)
        for claim, vec in zip(actives, vectors[1:], strict=True)
    ]
    scored = [pair for pair in scored if pair[0] >= min_similarity]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    return [
        Candidate(claim_id=claim.id, text=claim.text, confidence=claim.confidence)
        for _, claim in scored[:top_k]
    ]
