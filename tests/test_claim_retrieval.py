"""
Tests for mymem/knowledge/retrieval.py — find similar ACTIVE claims for a proposition
(ADR-011 / ADR-015 Phase 3b).

The embedder is injected (a fake returning deterministic vectors) — no Ollama, no network.
Candidates are scoped to the proposition's page (the compounding case: re-ingesting about
the same concept), ranked by cosine, filtered by a similarity floor.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.knowledge.claims import add_claim, init_db
from mymem.knowledge.retrieval import _cosine, retrieve_candidates
from mymem.rag.embedder import Embedder

PAGE = "01HPAGE0000000000000000001"
OTHER = "01HPAGE0000000000000000002"


class FakeEmbedder(Embedder):
    """Maps each text to a fixed vector so cosine similarity is fully controllable."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self._table = table

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._table.get(t, [0.0, 0.0, 0.0]) for t in texts]


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    return p


# ---------------------------------------------------------------------------
# _cosine
# ---------------------------------------------------------------------------

class TestCosine:
    def test_identical_vectors(self) -> None:
        assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_is_zero(self) -> None:
        assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# retrieve_candidates
# ---------------------------------------------------------------------------

class TestRetrieve:
    @pytest.mark.asyncio
    async def test_empty_when_page_has_no_claims(self, db: Path) -> None:
        emb = FakeEmbedder({"prop": [1.0, 0.0, 0.0]})
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb)
        assert out == []

    @pytest.mark.asyncio
    async def test_ranks_by_similarity_and_applies_floor(self, db: Path) -> None:
        near = add_claim(db, page_id=PAGE, text="near", source_id="raw/a.md")
        far = add_claim(db, page_id=PAGE, text="far", source_id="raw/a.md")
        emb = FakeEmbedder(
            {
                "prop": [1.0, 0.0, 0.0],
                "near": [1.0, 0.0, 0.0],   # cosine 1.0  → kept
                "far": [0.0, 1.0, 0.0],    # cosine 0.0  → below floor, dropped
            }
        )
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb, min_similarity=0.5)
        assert [c.claim_id for c in out] == [near.id]
        assert far.id not in {c.claim_id for c in out}

    @pytest.mark.asyncio
    async def test_orders_most_similar_first(self, db: Path) -> None:
        a = add_claim(db, page_id=PAGE, text="a", source_id="raw/a.md")
        b = add_claim(db, page_id=PAGE, text="b", source_id="raw/a.md")
        emb = FakeEmbedder(
            {
                "prop": [1.0, 0.0],
                "a": [0.7, 0.7],   # cosine ~0.707
                "b": [0.99, 0.14],  # cosine ~0.99 (more similar)
            }
        )
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb, min_similarity=0.1)
        assert [c.claim_id for c in out] == [b.id, a.id]

    @pytest.mark.asyncio
    async def test_top_k_caps_results(self, db: Path) -> None:
        for i in range(5):
            add_claim(db, page_id=PAGE, text=f"c{i}", source_id="raw/a.md")
        emb = FakeEmbedder({f"c{i}": [1.0, 0.0] for i in range(5)} | {"prop": [1.0, 0.0]})
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb, top_k=2, min_similarity=0.1)
        assert len(out) == 2

    @pytest.mark.asyncio
    async def test_excludes_other_pages(self, db: Path) -> None:
        add_claim(db, page_id=OTHER, text="elsewhere", source_id="raw/a.md")
        emb = FakeEmbedder({"prop": [1.0, 0.0], "elsewhere": [1.0, 0.0]})
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb, min_similarity=0.1)
        assert out == []  # only the proposition's own page is in scope

    @pytest.mark.asyncio
    async def test_excludes_superseded_claims(self, db: Path) -> None:
        from mymem.knowledge.claims import supersede_claim

        new = add_claim(db, page_id=PAGE, text="new", source_id="raw/a.md")
        old = add_claim(db, page_id=PAGE, text="old", source_id="raw/a.md")
        supersede_claim(db, old.id, by=new.id)
        emb = FakeEmbedder({"prop": [1.0, 0.0], "new": [1.0, 0.0], "old": [1.0, 0.0]})
        out = await retrieve_candidates(db, "prop", PAGE, embedder=emb, min_similarity=0.1)
        assert {c.claim_id for c in out} == {new.id}  # retired claim not a candidate
