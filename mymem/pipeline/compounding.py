"""
Compounding orchestrator — retrieve → decide → apply for a source's propositions
(ADR-011 / ADR-015 Phase 3c).

This is the seam that turns ingest from "overwrite knowledge" into "compound knowledge":
for each atomic proposition, find the similar active claims on its page, let the LLM decide
ADD / MERGE / SUPERSEDE / NOOP, and apply that to the claims ledger. Pure orchestration —
both the embedder (retrieval) and the router (decision) are injected, so the whole loop runs
in tests without Ollama. Wiki page bodies are still written by ingest (single responsibility).
"""
from __future__ import annotations

from pathlib import Path

from mymem.knowledge.claims import Claim
from mymem.knowledge.retrieval import retrieve_candidates
from mymem.observability.logger import get_logger
from mymem.pipeline.reconcile import (
    Proposition,
    ReconcileResult,
    apply_decision,
    reconcile,
)
from mymem.pipeline.router import ModelRouter
from mymem.rag.embedder import Embedder

log = get_logger(__name__)


async def reconcile_source_claims(
    db_path: Path,
    source_id: str,
    propositions: list[Proposition],
    *,
    router: ModelRouter,
    embedder: Embedder,
    top_k: int = 5,
    min_similarity: float = 0.6,
) -> list[tuple[ReconcileResult, Claim]]:
    """Reconcile each proposition against existing claims and apply the decision.

    Returns one (decision, resulting-claim) pair per proposition, in order, so callers can
    enrich page bodies on MERGE or surface SUPERSEDE trails.
    """
    results: list[tuple[ReconcileResult, Claim]] = []
    for prop in propositions:
        candidates = await retrieve_candidates(
            db_path, prop.text, prop.page_id,
            embedder=embedder, top_k=top_k, min_similarity=min_similarity,
        )
        decision = await reconcile(prop, candidates, router=router)
        claim = apply_decision(db_path, decision, prop, source_id=source_id)
        results.append((decision, claim))

    counts: dict[str, int] = {}
    for decision, _ in results:
        counts[decision.decision.value] = counts.get(decision.decision.value, 0) + 1
    log.info("Compounding reconcile complete", source=source_id, **counts)
    return results
