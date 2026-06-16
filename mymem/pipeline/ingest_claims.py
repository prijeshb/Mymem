"""
Ingest-side claims persistence + wiki sync (split out of ingest.py).

Bridges an ingest's extracted propositions to the compounding ledger (ADR-011 / ADR-015):
  _persist_claims        — retrieve → decide → apply per proposition, with a naive fallback
  _sync_claims_sections  — refresh each touched page's "Knowledge Claims" markdown section
  _build_claim_embedder  — embedder factory, isolated so tests can patch it

All best-effort — knowledge recording must never fail an ingest.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from mymem.observability.logger import get_logger
from mymem.wiki.page import read_page, write_page

if TYPE_CHECKING:
    from mymem.pipeline.compounding import AppliedDecision
    from mymem.pipeline.router import ModelRouter
    from mymem.rag.embedder import Embedder

log = get_logger(__name__)


def _sync_claims_sections(db_path: Path, touched_pages: list[tuple[Path, str]]) -> None:
    """Refresh each touched page's "Knowledge Claims" section from its current claims
    (ADR-015 D13). Best-effort and idempotent — never raises into ingest. Writes with
    stamp_updated=False so the page's real last-edited date (set during the compile loop)
    is preserved.
    """
    claims_db = db_path.parent / "claims.db"
    if not claims_db.exists():
        return
    try:
        from mymem.knowledge.claims import claims_for_page
        from mymem.knowledge.render import sync_claims_section

        for page_path, page_id in touched_pages:
            try:
                page = read_page(page_path)
                new_body = sync_claims_section(page.body, claims_for_page(claims_db, page_id))
                if new_body != page.body:
                    write_page(dataclasses.replace(page, body=new_body), stamp_updated=False)
            except Exception as exc:
                log.debug("Claims-section sync skipped", page=str(page_path), error=str(exc))
    except Exception as exc:
        log.warning("Claims-section sync failed (non-fatal)", error=str(exc))


def _build_claim_embedder() -> Embedder:
    """Construct the embedder used for claim retrieval. Isolated so tests can patch it."""
    from mymem.rag.embedder import OllamaEmbedder

    return OllamaEmbedder()


def _naive_persist(claims_db: Path, source_id: str, records: list[tuple[str, str, str]]) -> None:
    """Fallback when the compounding pipeline is unavailable (e.g. no embedder): record
    provenance with an idempotent per-source replace so re-ingest can't duplicate."""
    from mymem.knowledge.claims import NewClaim, replace_source_claims

    replace_source_claims(
        claims_db,
        source_id,
        [NewClaim(page_id=pid, text=text, source_span=span) for pid, text, span in records],
    )


async def _persist_claims(
    db_path: Path,
    source_id: str,
    records: list[tuple[str, str, str]],
    *,
    router: ModelRouter,
) -> list[AppliedDecision]:
    """Compound this source's propositions into claims.db (ADR-015 Phase 3c).

    For each proposition: retrieve similar active claims on its page → LLM decides
    ADD/MERGE/SUPERSEDE/NOOP → apply to the bi-temporal ledger. Returns the applied
    decisions (for the decision-agreement eval); empty on the naive-fallback path. If
    retrieval/decision is unavailable (embedder down), fall back to idempotent naive
    provenance. Never raises — knowledge recording must not break an ingest.
    """
    if not records:
        return []
    from mymem.knowledge.claims import init_db
    from mymem.pipeline.compounding import reconcile_source_claims
    from mymem.pipeline.reconcile import Proposition

    claims_db = db_path.parent / "claims.db"
    try:
        init_db(claims_db)
        propositions = [
            Proposition(text=text, page_id=pid, source_span=span)
            for pid, text, span in records
        ]
        applied = await reconcile_source_claims(
            claims_db, source_id, propositions,
            router=router, embedder=_build_claim_embedder(),
        )
        log.info("Claims compounded", source=source_id, propositions=len(propositions))
        return applied
    except Exception as exc:
        log.warning(
            "Claims compounding failed; falling back to naive persist",
            source=source_id, error=str(exc),
        )
        try:
            _naive_persist(claims_db, source_id, records)
        except Exception as exc2:
            log.warning(
                "Claims persistence failed (non-fatal)", source=source_id, error=str(exc2)
            )
        return []
