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

from dataclasses import dataclass
from pathlib import Path

from mymem.knowledge.claims import Claim
from mymem.knowledge.retrieval import retrieve_candidates
from mymem.observability.logger import get_logger
from mymem.pipeline.reconcile import (
    Candidate,
    Proposition,
    ReconcileResult,
    apply_decision,
    reconcile,
)
from mymem.pipeline.router import ModelRouter
from mymem.rag.embedder import Embedder

log = get_logger(__name__)


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
    """Reconcile each proposition against existing claims and apply the decision.

    Returns one AppliedDecision per proposition, in order, carrying the candidates seen so
    callers can enrich pages, show SUPERSEDE trails, or score decision agreement.
    """
    applied: list[AppliedDecision] = []
    for prop in propositions:
        candidates = await retrieve_candidates(
            db_path, prop.text, prop.page_id,
            embedder=embedder, top_k=top_k, min_similarity=min_similarity,
        )
        decision = await reconcile(prop, candidates, router=router)
        claim = apply_decision(db_path, decision, prop, source_id=source_id)
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
