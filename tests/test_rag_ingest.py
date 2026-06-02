"""Tests for mymem.rag.embedder and mymem.rag.ingest (mocked Ollama)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mymem.rag.embedder import EMBED_DIM, embed_query, embed_texts
from mymem.rag.ingest import RagIngestResult, ingest_pdf, ingest_text_chunks
from mymem.rag.store import init_db, list_sources, source_exists


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zero_vec(dim: int = EMBED_DIM) -> list[float]:
    return [0.0] * dim


def _mock_ollama_client(embeddings: list[list[float]]) -> MagicMock:
    """Return a mock AsyncClient whose embed() returns given embeddings."""
    resp = MagicMock()
    resp.embeddings = embeddings
    client = MagicMock()
    client.embed = AsyncMock(return_value=resp)
    return client


def _make_pdf(tmp_path: Path, page_texts: list[str]) -> Path:
    """Create a real-looking fake PDF path and patch PdfReader."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    return pdf


# ---------------------------------------------------------------------------
# embedder.embed_texts
# ---------------------------------------------------------------------------

class TestEmbedTexts:
    @pytest.mark.asyncio
    async def test_returns_one_vector_per_text(self, tmp_path):
        texts = ["hello", "world"]
        expected = [_zero_vec(), _zero_vec()]
        mock_client = _mock_ollama_client(expected)
        with patch("mymem.rag.embedder.AsyncClient", return_value=mock_client):
            result = await embed_texts(texts)
        assert len(result) == 2
        assert result[0] == _zero_vec()

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self):
        with patch("mymem.rag.embedder.AsyncClient") as MockClient:
            result = await embed_texts([])
        assert result == []
        MockClient.return_value.embed.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_on_ollama_error(self):
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(side_effect=ConnectionRefusedError("no ollama"))
        with patch("mymem.rag.embedder.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Ollama embed failed"):
                await embed_texts(["text"])

    @pytest.mark.asyncio
    async def test_batches_large_input(self):
        # 70 texts → 3 batches (BATCH_SIZE=32)
        texts = [f"text {i}" for i in range(70)]
        mock_client = MagicMock()
        mock_client.embed = AsyncMock(
            side_effect=lambda model, input: MagicMock(
                embeddings=[_zero_vec()] * len(input)
            )
        )
        with patch("mymem.rag.embedder.AsyncClient", return_value=mock_client):
            result = await embed_texts(texts)
        assert len(result) == 70
        assert mock_client.embed.call_count == 3   # ceil(70/32)


# ---------------------------------------------------------------------------
# embedder.embed_query
# ---------------------------------------------------------------------------

class TestEmbedQuery:
    @pytest.mark.asyncio
    async def test_returns_single_vector(self):
        mock_client = _mock_ollama_client([_zero_vec()])
        with patch("mymem.rag.embedder.AsyncClient", return_value=mock_client):
            result = await embed_query("what is attention?")
        assert len(result) == EMBED_DIM
        assert isinstance(result[0], float)


# ---------------------------------------------------------------------------
# ingest.ingest_pdf
# ---------------------------------------------------------------------------

def _patch_embedder(embeddings: list[list[float]]):
    """Patch embed_texts in the ingest module."""
    return patch(
        "mymem.rag.ingest.embed_texts",
        new=AsyncMock(return_value=embeddings),
    )


def _patch_parser(chunks):
    """Patch parse_pdf in the ingest module."""
    return patch("mymem.rag.ingest.parse_pdf", return_value=chunks)


def _make_chunks(n: int):
    from mymem.rag.pdf_parser import PdfChunk
    return [PdfChunk(chunk_index=i, page_num=i + 1, text=f"chunk text {i}") for i in range(n)]


class TestIngestPdf:
    @pytest.mark.asyncio
    async def test_successful_ingest(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")
        chunks = _make_chunks(3)

        with _patch_parser(chunks), _patch_embedder([_zero_vec()] * 3):
            result = await ingest_pdf(pdf, db_path=db)

        assert result.ok
        assert result.chunk_count == 3
        assert result.skipped is False
        assert source_exists(db, str(pdf.resolve()))

    @pytest.mark.asyncio
    async def test_skip_already_indexed(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")
        chunks = _make_chunks(2)

        with _patch_parser(chunks), _patch_embedder([_zero_vec()] * 2):
            await ingest_pdf(pdf, db_path=db)
            result = await ingest_pdf(pdf, db_path=db)   # second call

        assert result.skipped is True
        assert result.skip_reason == "already indexed"
        assert list_sources(db)[0]["chunk_count"] == 2   # unchanged

    @pytest.mark.asyncio
    async def test_force_reindexes(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")

        with _patch_parser(_make_chunks(2)), _patch_embedder([_zero_vec()] * 2):
            await ingest_pdf(pdf, db_path=db)

        with _patch_parser(_make_chunks(5)), _patch_embedder([_zero_vec()] * 5):
            result = await ingest_pdf(pdf, db_path=db, force=True)

        assert result.ok
        assert result.chunk_count == 5

    @pytest.mark.asyncio
    async def test_missing_file_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "missing.pdf"   # does not exist
        result = await ingest_pdf(pdf, db_path=db)
        assert result.error != ""
        assert not result.ok

    @pytest.mark.asyncio
    async def test_parse_error_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "bad.pdf"
        pdf.write_bytes(b"not a pdf")

        with patch("mymem.rag.ingest.parse_pdf", side_effect=Exception("bad PDF")):
            result = await ingest_pdf(pdf, db_path=db)

        assert "PDF parse failed" in result.error
        assert not result.ok

    @pytest.mark.asyncio
    async def test_empty_pdf_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"%PDF fake")

        with _patch_parser([]):   # no chunks extracted
            result = await ingest_pdf(pdf, db_path=db)

        assert result.error != ""
        assert not result.ok

    @pytest.mark.asyncio
    async def test_embed_failure_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")

        with _patch_parser(_make_chunks(2)), \
             patch("mymem.rag.ingest.embed_texts", new=AsyncMock(side_effect=RuntimeError("no ollama"))):
            result = await ingest_pdf(pdf, db_path=db)

        assert "Embedding failed" in result.error
        assert not result.ok

    @pytest.mark.asyncio
    async def test_store_insert_failure_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")

        with _patch_parser(_make_chunks(2)), \
             _patch_embedder([_zero_vec()] * 2), \
             patch("mymem.rag.ingest.insert_chunks", side_effect=RuntimeError("disk full")):
            result = await ingest_pdf(pdf, db_path=db)

        assert "Store insert failed" in result.error
        assert not result.ok

    @pytest.mark.asyncio
    async def test_result_ok_property(self, tmp_path):
        db = tmp_path / "mymem.db"
        pdf = tmp_path / "paper.pdf"
        pdf.write_bytes(b"%PDF fake")

        with _patch_parser(_make_chunks(1)), _patch_embedder([_zero_vec()]):
            result = await ingest_pdf(pdf, db_path=db)

        assert result.ok is True

        error_result = RagIngestResult(source_path="x", error="something went wrong")
        assert error_result.ok is False

        skipped_result = RagIngestResult(source_path="x", skipped=True, skip_reason="already indexed")
        assert skipped_result.ok is False


# ---------------------------------------------------------------------------
# ingest_text_chunks
# ---------------------------------------------------------------------------

class TestIngestTextChunks:
    @pytest.mark.asyncio
    async def test_indexes_raw_text(self, tmp_path):
        db = tmp_path / "mymem.db"
        text = "Alpha paragraph.\n\nBeta paragraph with more words to fill the chunk up nicely."
        # embed_texts returns one vector per chunk; use side_effect to match length dynamically
        async def _embed(texts, **_kw):
            return [_zero_vec()] * len(texts)

        with patch("mymem.rag.ingest.embed_texts", new=AsyncMock(side_effect=_embed)):
            result = await ingest_text_chunks(text, source_id="my-note", db_path=db)
        assert result.ok
        assert result.chunk_count >= 1
        assert source_exists(db, "my-note")

    @pytest.mark.asyncio
    async def test_skip_already_indexed(self, tmp_path):
        db = tmp_path / "mymem.db"
        text = "Some content here."
        async def _embed(texts, **_kw):
            return [_zero_vec()] * len(texts)

        with patch("mymem.rag.ingest.embed_texts", new=AsyncMock(side_effect=_embed)):
            await ingest_text_chunks(text, source_id="note-a", db_path=db)
            result = await ingest_text_chunks(text, source_id="note-a", db_path=db)
        assert result.skipped
        assert result.skip_reason == "already indexed"

    @pytest.mark.asyncio
    async def test_force_reindexes(self, tmp_path):
        db = tmp_path / "mymem.db"
        text = "Content for reindex test."
        async def _embed(texts, **_kw):
            return [_zero_vec()] * len(texts)

        with patch("mymem.rag.ingest.embed_texts", new=AsyncMock(side_effect=_embed)):
            await ingest_text_chunks(text, source_id="note-b", db_path=db)
            result = await ingest_text_chunks(text, source_id="note-b", db_path=db, force=True)
        assert result.ok

    @pytest.mark.asyncio
    async def test_empty_text_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        result = await ingest_text_chunks("   ", source_id="empty", db_path=db)
        assert not result.ok
        assert "Empty text" in result.error

    @pytest.mark.asyncio
    async def test_embed_failure_returns_error(self, tmp_path):
        db = tmp_path / "mymem.db"
        text = "Some text to embed."
        with patch("mymem.rag.ingest.embed_texts", new=AsyncMock(side_effect=RuntimeError("no ollama"))):
            result = await ingest_text_chunks(text, source_id="note-c", db_path=db)
        assert "Embedding failed" in result.error
        assert not result.ok
