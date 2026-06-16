"""
Tests for mymem/knowledge/retrieval.py — the thin adapter mapping the global claim index
to reconcile `Candidate`s (ADR-011 / ADR-015 D19).

The heavy lifting (KNN, active-filter, threshold) is tested in test_claim_index.py; here we
verify retrieve_candidates delegates correctly and returns Candidates ranked best-first,
including cross-page matches.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.knowledge.claim_index import index_claim, init_index
from mymem.knowledge.claims import add_claim, init_db
from mymem.knowledge.retrieval import retrieve_candidates
from mymem.pipeline.reconcile import Candidate

PAGE_A = "01HPAGE0000000000000000001"
PAGE_B = "01HPAGE0000000000000000002"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    init_index(p, dim=3)
    return p


def _indexed_claim(
    db: Path, page: str, text: str, vec: list[float], confidence: float = 1.0
) -> int:
    cid = add_claim(db, page_id=page, text=text, source_id="raw/a.md", confidence=confidence).id
    index_claim(db, cid, vec)
    return cid


class TestRetrieveCandidates:
    def test_empty_index_returns_empty(self, db: Path) -> None:
        assert retrieve_candidates(db, [1.0, 0.0, 0.0]) == []

    def test_maps_hits_to_candidates(self, db: Path) -> None:
        cid = _indexed_claim(db, PAGE_A, "a claim", [1.0, 0.0, 0.0], confidence=0.8)
        out = retrieve_candidates(db, [1.0, 0.0, 0.0], min_similarity=0.5)
        assert out == [Candidate(claim_id=cid, text="a claim", confidence=0.8)]

    def test_finds_cross_page_candidate(self, db: Path) -> None:
        # Claim lives on PAGE_B; a proposition (any page) still retrieves it globally.
        cid = _indexed_claim(db, PAGE_B, "shared concept", [1.0, 0.0, 0.0])
        out = retrieve_candidates(db, [1.0, 0.0, 0.0], min_similarity=0.5)
        assert [c.claim_id for c in out] == [cid]

    def test_orders_best_first_and_applies_floor(self, db: Path) -> None:
        a = _indexed_claim(db, PAGE_A, "a", [0.7, 0.7, 0.0])    # ~0.707
        b = _indexed_claim(db, PAGE_A, "b", [0.99, 0.14, 0.0])  # ~0.99
        _indexed_claim(db, PAGE_A, "far", [0.0, 1.0, 0.0])      # ~0.0 → dropped
        out = retrieve_candidates(db, [1.0, 0.0, 0.0], min_similarity=0.3)
        assert [c.claim_id for c in out] == [b, a]

    def test_exclude_page_id_passthrough(self, db: Path) -> None:
        _indexed_claim(db, PAGE_A, "same", [1.0, 0.0, 0.0])
        other = _indexed_claim(db, PAGE_B, "other", [1.0, 0.0, 0.0])
        out = retrieve_candidates(db, [1.0, 0.0, 0.0], min_similarity=0.1, exclude_page_id=PAGE_A)
        assert [c.claim_id for c in out] == [other]
