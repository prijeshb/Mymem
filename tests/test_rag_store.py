"""Tests for mymem.rag.store — sqlite-vec chunk store."""

from __future__ import annotations

import pytest

from mymem.rag.store import (
    EMBED_DIM,
    RagChunk,
    SearchResult,
    delete_source,
    init_db,
    insert_chunks,
    list_sources,
    search_similar,
    source_exists,
)


def _zero_vec(dim: int = EMBED_DIM) -> list[float]:
    return [0.0] * dim


def _unit_vec(index: int, dim: int = EMBED_DIM) -> list[float]:
    v = [0.0] * dim
    v[index % dim] = 1.0
    return v


def _make_chunk(
    source_path: str = "raw/test.pdf",
    source_slug: str = "test",
    chunk_index: int = 0,
    page_num: int | None = 1,
    text: str = "Hello world chunk text.",
) -> dict:
    return {
        "source_path":  source_path,
        "source_slug":  source_slug,
        "chunk_index":  chunk_index,
        "page_num":     page_num,
        "text":         text,
    }


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_tables(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        assert db.exists()

    def test_idempotent(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        init_db(db)   # second call must not raise


# ---------------------------------------------------------------------------
# source_exists
# ---------------------------------------------------------------------------

class TestSourceExists:
    def test_false_when_empty(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        assert source_exists(db, "raw/missing.pdf") is False

    def test_true_after_insert(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunk = _make_chunk()
        insert_chunks(db, [chunk], [_zero_vec()])
        assert source_exists(db, "raw/test.pdf") is True


# ---------------------------------------------------------------------------
# insert_chunks
# ---------------------------------------------------------------------------

class TestInsertChunks:
    def test_basic_insert(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunks = [_make_chunk(chunk_index=i) for i in range(3)]
        embeddings = [_zero_vec() for _ in range(3)]
        insert_chunks(db, chunks, embeddings)
        sources = list_sources(db)
        assert len(sources) == 1
        assert sources[0]["chunk_count"] == 3

    def test_length_mismatch_raises(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        with pytest.raises(ValueError, match="mismatch"):
            insert_chunks(db, [_make_chunk()], [_zero_vec(), _zero_vec()])

    def test_null_page_num(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunk = _make_chunk(page_num=None)
        insert_chunks(db, [chunk], [_zero_vec()])
        sources = list_sources(db)
        assert sources[0]["chunk_count"] == 1


# ---------------------------------------------------------------------------
# search_similar
# ---------------------------------------------------------------------------

class TestSearchSimilar:
    def test_returns_empty_on_empty_db(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        results = search_similar(db, _zero_vec(), top_k=5)
        assert results == []

    def test_returns_closest_first(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        # Insert two chunks with orthogonal embeddings
        chunks = [
            _make_chunk(chunk_index=0, text="chunk A"),
            _make_chunk(chunk_index=1, text="chunk B"),
        ]
        embeddings = [_unit_vec(0), _unit_vec(1)]
        insert_chunks(db, chunks, embeddings)

        # Query close to embedding[0]
        query = _unit_vec(0)
        results = search_similar(db, query, top_k=2)
        assert len(results) == 2
        assert results[0].chunk.text == "chunk A"

    def test_result_type(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        insert_chunks(db, [_make_chunk()], [_zero_vec()])
        results = search_similar(db, _zero_vec(), top_k=1)
        assert len(results) == 1
        r = results[0]
        assert isinstance(r, SearchResult)
        assert isinstance(r.chunk, RagChunk)
        assert isinstance(r.distance, float)

    def test_top_k_respected(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunks = [_make_chunk(chunk_index=i, text=f"chunk {i}") for i in range(10)]
        embeddings = [_unit_vec(i) for i in range(10)]
        insert_chunks(db, chunks, embeddings)
        results = search_similar(db, _zero_vec(), top_k=3)
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------

class TestListSources:
    def test_empty(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        assert list_sources(db) == []

    def test_groups_by_source(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunks_a = [_make_chunk(source_path="a.pdf", source_slug="a", chunk_index=i) for i in range(2)]
        chunks_b = [_make_chunk(source_path="b.pdf", source_slug="b", chunk_index=0)]
        insert_chunks(db, chunks_a, [_zero_vec(), _zero_vec()])
        insert_chunks(db, chunks_b, [_zero_vec()])
        sources = list_sources(db)
        assert len(sources) == 2
        counts = {s["source_path"]: s["chunk_count"] for s in sources}
        assert counts["a.pdf"] == 2
        assert counts["b.pdf"] == 1


# ---------------------------------------------------------------------------
# delete_source
# ---------------------------------------------------------------------------

class TestDeleteSource:
    def test_delete_removes_chunks_and_embeddings(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        chunks = [_make_chunk(chunk_index=i) for i in range(3)]
        insert_chunks(db, chunks, [_zero_vec()] * 3)
        deleted = delete_source(db, "raw/test.pdf")
        assert deleted == 3
        assert source_exists(db, "raw/test.pdf") is False
        assert search_similar(db, _zero_vec(), top_k=10) == []

    def test_delete_missing_source_returns_zero(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        assert delete_source(db, "nonexistent.pdf") == 0

    def test_delete_only_removes_target_source(self, tmp_path):
        db = tmp_path / "test.db"
        init_db(db)
        insert_chunks(db, [_make_chunk(source_path="a.pdf", chunk_index=0)], [_zero_vec()])
        insert_chunks(db, [_make_chunk(source_path="b.pdf", chunk_index=0)], [_zero_vec()])
        delete_source(db, "a.pdf")
        assert source_exists(db, "b.pdf") is True
        assert source_exists(db, "a.pdf") is False
