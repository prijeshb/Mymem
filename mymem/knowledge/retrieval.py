"""
Claim retrieval — find the similar ACTIVE claims a proposition should be reconciled against
(ADR-011 / ADR-015 D19, cross-page).

Thin adapter over the global `claim_index`: it takes the proposition's already-computed
embedding (the embedder lives in the compounding layer) and returns reconcile `Candidate`s,
ranked by cosine similarity across ALL pages — so MERGE / SUPERSEDE can act on a contradicting
or duplicate claim wherever it lives, not just on the proposition's own page.
"""
from __future__ import annotations

from pathlib import Path

from mymem.knowledge.claim_index import search
from mymem.pipeline.reconcile import Candidate


def retrieve_candidates(
    db_path: Path,
    query_embedding: list[float],
    *,
    top_k: int = 5,
    min_similarity: float = 0.6,
    exclude_page_id: str | None = None,
) -> list[Candidate]:
    """Return the active claims most similar to `query_embedding`, best first.

    Empty when nothing clears `min_similarity` (or the index is empty) — the caller then
    falls through to ADD without an LLM round-trip. `exclude_page_id` is unused by default;
    same-page claims are valid candidates (re-ingesting a concept should MERGE/NOOP onto them).
    """
    hits = search(
        db_path,
        query_embedding,
        top_k=top_k,
        min_similarity=min_similarity,
        exclude_page_id=exclude_page_id,
    )
    return [
        Candidate(claim_id=h.claim_id, text=h.text, confidence=h.confidence)
        for h in hits
    ]
