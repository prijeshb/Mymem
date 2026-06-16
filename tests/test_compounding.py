"""
Tests for mymem/pipeline/compounding.py — the retrieve → decide → apply orchestrator
(ADR-011 / ADR-015 Phase 3c).

Embedder and router are both injected (fakes) — no Ollama, no network. Exercised against
a real claims.db on tmp_path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.knowledge.claims import ClaimsStats, add_claim, claims_for_page, init_db, stats
from mymem.pipeline.compounding import reconcile_source_claims
from mymem.pipeline.reconcile import Decision, Proposition
from mymem.pipeline.router import ModelRouter
from mymem.rag.embedder import Embedder

PAGE = "01HPAGE0000000000000000001"


class StubEmbedder(Embedder):
    """All texts map to the same unit vector → every comparison is maximally similar,
    so any existing claim becomes a candidate (lets the router's decision drive the test)."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


def _router(decision_json: str) -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return decision_json

    return ModelRouter(llm_fn=fake_llm)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    return p


def _props(*texts: str) -> list[Proposition]:
    return [Proposition(text=t, page_id=PAGE, source_span="span") for t in texts]


class TestReconcileSourceClaims:
    @pytest.mark.asyncio
    async def test_new_page_adds_without_llm(self, db: Path) -> None:
        # No prior claims → retrieval empty → ADD short-circuit (router never consulted).
        out = await reconcile_source_claims(
            db, "raw/a.md", _props("first claim", "second claim"),
            router=_router("ignored"), embedder=StubEmbedder(),
        )
        assert [a.result.decision for a in out] == [Decision.ADD, Decision.ADD]
        assert out[0].candidates == ()  # first proposition saw an empty page
        assert stats(db) == ClaimsStats(total=2, active=2, superseded=0)

    @pytest.mark.asyncio
    async def test_noop_corroborates_existing(self, db: Path) -> None:
        existing = add_claim(
            db, page_id=PAGE, text="known fact", source_id="raw/old.md", confidence=0.5
        )
        router = _router(f'{{"decision":"NOOP","target_claim_id":{existing.id}}}')
        out = await reconcile_source_claims(
            db, "raw/a.md", _props("known fact restated"), router=router, embedder=StubEmbedder()
        )
        assert out[0].result.decision is Decision.NOOP
        assert out[0].candidates[0].claim_id == existing.id  # the candidate it judged
        # No new claim; the existing one was corroborated.
        assert stats(db) == ClaimsStats(total=1, active=1, superseded=0)
        assert claims_for_page(db, PAGE)[0].confidence == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_supersede_retires_and_adds(self, db: Path) -> None:
        old = add_claim(db, page_id=PAGE, text="It launched in 2017.", source_id="raw/old.md")
        router = _router(f'{{"decision":"SUPERSEDE","target_claim_id":{old.id}}}')
        out = await reconcile_source_claims(
            db, "raw/a.md", _props("It launched in 2014."), router=router, embedder=StubEmbedder()
        )
        assert out[0].result.decision is Decision.SUPERSEDE
        # Old retired, new active — nothing hard-deleted.
        assert stats(db) == ClaimsStats(total=2, active=1, superseded=1)

    @pytest.mark.asyncio
    async def test_reingest_same_source_does_not_duplicate(self, db: Path) -> None:
        # First pass adds (new page); second pass with a NOOP router corroborates.
        await reconcile_source_claims(
            db, "raw/a.md", _props("stable fact"), router=_router("x"), embedder=StubEmbedder()
        )
        first_id = claims_for_page(db, PAGE)[0].id
        await reconcile_source_claims(
            db, "raw/a.md", _props("stable fact"),
            router=_router(f'{{"decision":"NOOP","target_claim_id":{first_id}}}'),
            embedder=StubEmbedder(),
        )
        # corroborated, not duplicated
        assert stats(db) == ClaimsStats(total=1, active=1, superseded=0)
