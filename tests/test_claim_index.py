"""
Tests for mymem/knowledge/claim_index.py — sqlite-vec vector index over claims
(ADR-011 / ADR-015 D19, cross-page retrieval).

Real sqlite-vec on tmp_path with small synthetic vectors (dim=3). No embedder — vectors
are passed in. The index lives in claims.db alongside the `claims` table; search joins back
to it and returns only ACTIVE claims (superseded ones are filtered out).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.knowledge.claim_index import (
    ClaimHit,
    claims_missing_vector,
    count,
    delete_claim,
    index_claim,
    init_index,
    search,
)
from mymem.knowledge.claims import add_claim, init_db, supersede_claim

PAGE_A = "01HPAGE0000000000000000001"
PAGE_B = "01HPAGE0000000000000000002"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "claims.db"
    init_db(p)
    init_index(p, dim=3)
    return p


def _claim(db: Path, page: str, text: str, **kw: object) -> int:
    return add_claim(db, page_id=page, text=text, source_id="raw/a.md", **kw).id  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# init / index / count
# ---------------------------------------------------------------------------

class TestIndexBasics:
    def test_init_idempotent_and_empty(self, db: Path) -> None:
        init_index(db, dim=3)  # second call must not raise
        assert count(db) == 0

    def test_index_then_count(self, db: Path) -> None:
        cid = _claim(db, PAGE_A, "a")
        index_claim(db, cid, [1.0, 0.0, 0.0])
        assert count(db) == 1

    def test_index_is_upsert(self, db: Path) -> None:
        cid = _claim(db, PAGE_A, "a")
        index_claim(db, cid, [1.0, 0.0, 0.0])
        index_claim(db, cid, [0.0, 1.0, 0.0])  # re-index same claim
        assert count(db) == 1  # replaced, not duplicated

    def test_delete_claim(self, db: Path) -> None:
        cid = _claim(db, PAGE_A, "a")
        index_claim(db, cid, [1.0, 0.0, 0.0])
        delete_claim(db, cid)
        assert count(db) == 0


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_empty_index_returns_nothing(self, db: Path) -> None:
        assert search(db, [1.0, 0.0, 0.0]) == []

    def test_finds_cross_page_match(self, db: Path) -> None:
        # A claim on PAGE_B should be retrievable for a query about the same concept,
        # even though the proposition belongs to a different page.
        cid = _claim(db, PAGE_B, "attention is global")
        index_claim(db, cid, [1.0, 0.0, 0.0])
        hits = search(db, [1.0, 0.0, 0.0], min_similarity=0.5)
        assert [h.claim_id for h in hits] == [cid]
        assert hits[0].page_id == PAGE_B
        assert isinstance(hits[0], ClaimHit)
        assert hits[0].similarity == pytest.approx(1.0)

    def test_applies_similarity_floor(self, db: Path) -> None:
        near = _claim(db, PAGE_A, "near")
        far = _claim(db, PAGE_A, "far")
        index_claim(db, near, [1.0, 0.0, 0.0])   # cosine sim 1.0 → kept
        index_claim(db, far, [0.0, 1.0, 0.0])    # cosine sim 0.0 → dropped
        hits = search(db, [1.0, 0.0, 0.0], min_similarity=0.5)
        assert [h.claim_id for h in hits] == [near]

    def test_orders_most_similar_first(self, db: Path) -> None:
        a = _claim(db, PAGE_A, "a")
        b = _claim(db, PAGE_A, "b")
        index_claim(db, a, [0.7, 0.7, 0.0])    # ~0.707 sim
        index_claim(db, b, [0.99, 0.14, 0.0])  # ~0.99 sim
        hits = search(db, [1.0, 0.0, 0.0], min_similarity=0.1)
        assert [h.claim_id for h in hits] == [b, a]

    def test_top_k_caps(self, db: Path) -> None:
        for i in range(5):
            cid = _claim(db, PAGE_A, f"c{i}")
            index_claim(db, cid, [1.0, 0.0, 0.0])
        assert len(search(db, [1.0, 0.0, 0.0], top_k=2, min_similarity=0.1)) == 2

    def test_excludes_superseded_claims(self, db: Path) -> None:
        new = _claim(db, PAGE_A, "new")
        old = _claim(db, PAGE_A, "old")
        index_claim(db, new, [1.0, 0.0, 0.0])
        index_claim(db, old, [1.0, 0.0, 0.0])
        supersede_claim(db, old, by=new)
        hits = search(db, [1.0, 0.0, 0.0], min_similarity=0.1)
        assert {h.claim_id for h in hits} == {new}  # retired claim filtered out

    def test_exclude_page_id(self, db: Path) -> None:
        same = _claim(db, PAGE_A, "same-page")
        other = _claim(db, PAGE_B, "other-page")
        index_claim(db, same, [1.0, 0.0, 0.0])
        index_claim(db, other, [1.0, 0.0, 0.0])
        hits = search(db, [1.0, 0.0, 0.0], min_similarity=0.1, exclude_page_id=PAGE_A)
        assert {h.claim_id for h in hits} == {other}


# ---------------------------------------------------------------------------
# claims_missing_vector (backfill support)
# ---------------------------------------------------------------------------

class TestMissingVector:
    def test_lists_active_claims_without_a_vector(self, db: Path) -> None:
        indexed = _claim(db, PAGE_A, "indexed")
        missing = _claim(db, PAGE_A, "missing")
        index_claim(db, indexed, [1.0, 0.0, 0.0])
        rows = claims_missing_vector(db)
        assert (missing, "missing") in [(cid, text) for cid, text in rows]
        assert indexed not in [cid for cid, _ in rows]

    def test_excludes_superseded(self, db: Path) -> None:
        new = _claim(db, PAGE_A, "new")
        old = _claim(db, PAGE_A, "old")
        supersede_claim(db, old, by=new)
        ids = [cid for cid, _ in claims_missing_vector(db)]
        assert new in ids and old not in ids  # only active claims need a vector
