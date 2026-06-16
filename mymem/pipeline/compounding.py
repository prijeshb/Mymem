"""
Compounding orchestrator — retrieve → decide → apply for a source's propositions
(ADR-011 / ADR-015 Phase 3c + D19 cross-page).

This is the seam that turns ingest from "overwrite knowledge" into "compound knowledge":
for each atomic proposition, embed it, find the similar active claims *across all pages* via
the global claim vector index, let the LLM decide ADD / MERGE / SUPERSEDE / NOOP, apply that
to the claims ledger, and keep the vector index in sync. The embedder and router are injected,
so the whole loop runs in tests without Ollama. Wiki page bodies are still written by ingest.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mymem.knowledge.claim_index import (
    claims_missing_vector,
    delete_claim,
    index_claim,
    init_index,
)
from mymem.knowledge.claims import Claim
from mymem.knowledge.retrieval import retrieve_candidates
from mymem.observability.logger import get_logger
from mymem.pipeline.reconcile import (
    Candidate,
    Decision,
    Proposition,
    ReconcileResult,
    apply_decision,
    reconcile,
)
from mymem.pipeline.router import ModelRouter
from mymem.rag.embedder import Embedder

log = get_logger(__name__)

# Decisions that create a new claim row (whose vector must be indexed).
_CREATES_CLAIM = (Decision.ADD, Decision.SUPERSEDE)


@dataclass(frozen=True)
class AppliedDecision:
    """What the compounding step did for one proposition — enough to enrich page bodies,
    surface a SUPERSEDE trail, or feed the decision-agreement eval."""
    proposition: Proposition
    candidates: tuple[Candidate, ...]
    result: ReconcileResult
    claim: Claim


async def reconcile_source_claims(
    db_path: Path,
    source_id: str,
    propositions: list[Proposition],
    *,
    router: ModelRouter,
    embedder: Embedder,
    top_k: int = 5,
    min_similarity: float = 0.6,
) -> list[AppliedDecision]:
    """Reconcile each proposition against existing claims (global vector search) and apply.

    Returns one AppliedDecision per proposition, in order, carrying the candidates seen so
    callers can enrich pages, show SUPERSEDE trails, or score decision agreement. Keeps the
    claim vector index in sync: new claims are indexed, superseded claims are de-indexed.
    """
    init_index(db_path)
    applied: list[AppliedDecision] = []
    for prop in propositions:
        query_vec = (await embedder.embed([prop.text]))[0]
        candidates = retrieve_candidates(
            db_path, query_vec, top_k=top_k, min_similarity=min_similarity
        )
        decision = await reconcile(prop, candidates, router=router)
        claim = apply_decision(db_path, decision, prop, source_id=source_id)

        # Keep the vector index consistent with the ledger.
        if decision.decision in _CREATES_CLAIM:
            index_claim(db_path, claim.id, query_vec)  # new claim text == prop.text
        if decision.decision is Decision.SUPERSEDE and decision.target_claim_id is not None:
            delete_claim(db_path, decision.target_claim_id)

        applied.append(
            AppliedDecision(
                proposition=prop,
                candidates=tuple(candidates),
                result=decision,
                claim=claim,
            )
        )

    counts: dict[str, int] = {}
    for a in applied:
        counts[a.result.decision.value] = counts.get(a.result.decision.value, 0) + 1
    log.info("Compounding reconcile complete", source=source_id, **counts)
    return applied


async def backfill_claim_index(db_path: Path, embedder: Embedder) -> int:
    """Embed and index every active claim that has no vector yet. Returns the count indexed.

    Makes cross-page retrieval work over claims written before D19 (or after an index reset).
    Idempotent — already-indexed claims are skipped.
    """
    init_index(db_path)
    missing = claims_missing_vector(db_path)
    if not missing:
        return 0
    vectors = await embedder.embed([text for _, text in missing])
    for (claim_id, _), vec in zip(missing, vectors, strict=True):
        index_claim(db_path, claim_id, vec)
    log.info("Claim index backfilled", db=str(db_path), indexed=len(missing))
    return len(missing)
