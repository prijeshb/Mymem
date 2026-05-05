"""Tests for mymem.rag.pdf_parser — PDF extraction and chunking."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mymem.rag.pdf_parser import (
    CHUNK_SIZE,
    PdfChunk,
    chunk_text,
    extract_pages,
    parse_pdf,
)


# ---------------------------------------------------------------------------
# chunk_text  (pure — no disk I/O)
# ---------------------------------------------------------------------------

class TestChunkText:
    def test_empty_text_returns_empty(self):
        assert chunk_text("") == []

    def test_short_text_is_single_chunk(self):
        text = "Short paragraph."
        chunks = chunk_text(text, page_num=1)
        assert len(chunks) == 1
        assert chunks[0].text == "Short paragraph."
        assert chunks[0].page_num == 1
        assert chunks[0].chunk_index == 0

    def test_long_text_splits_into_multiple_chunks(self):
        # Build text well over CHUNK_SIZE
        para = "Word " * 40 + "\n\n"   # ~200 chars per paragraph
        text = para * 10               # ~2000 chars total
        chunks = chunk_text(text, page_num=2)
        assert len(chunks) > 1

    def test_chunk_indices_are_monotonic(self):
        para = "Word " * 40 + "\n\n"
        text = para * 10
        chunks = chunk_text(text)
        indices = [c.chunk_index for c in chunks]
        assert indices == sorted(indices)
        assert indices[0] == 0

    def test_chunk_offset_applied(self):
        text = "Short text."
        chunks = chunk_text(text, chunk_offset=5)
        assert chunks[0].chunk_index == 5

    def test_no_empty_chunks(self):
        text = "\n\n".join(["para"] * 20)
        chunks = chunk_text(text)
        for c in chunks:
            assert c.text.strip() != ""

    def test_chunk_size_respected(self):
        # Each chunk should be close to CHUNK_SIZE (within 2x for overlap)
        para = "A" * 200 + "\n\n"
        text = para * 20
        chunks = chunk_text(text)
        for c in chunks[:-1]:   # last chunk may be smaller
            assert len(c.text) <= CHUNK_SIZE * 2

    def test_page_num_none_passes_through(self):
        chunks = chunk_text("Some text.", page_num=None)
        assert chunks[0].page_num is None

    def test_returns_pdf_chunk_type(self):
        chunks = chunk_text("Hello world.")
        assert all(isinstance(c, PdfChunk) for c in chunks)


# ---------------------------------------------------------------------------
# extract_pages  (mocked PdfReader)
# ---------------------------------------------------------------------------

def _make_mock_reader(page_texts: list[str]) -> MagicMock:
    mock_reader = MagicMock()
    pages = []
    for text in page_texts:
        p = MagicMock()
        p.extract_text.return_value = text
        pages.append(p)
    mock_reader.pages = pages
    return mock_reader


class TestExtractPages:
    def test_returns_page_num_and_text(self, tmp_path):
        pdf = tmp_path / "dummy.pdf"
        pdf.write_bytes(b"")   # existence check happens inside PdfReader mock
        mock_reader = _make_mock_reader(["Page one text.", "Page two text."])
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            pages = extract_pages(pdf)
        assert len(pages) == 2
        assert pages[0] == (1, "Page one text.")
        assert pages[1] == (2, "Page two text.")

    def test_skips_empty_pages(self, tmp_path):
        pdf = tmp_path / "dummy.pdf"
        pdf.write_bytes(b"")
        mock_reader = _make_mock_reader(["", "  ", "Has text."])
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            pages = extract_pages(pdf)
        assert len(pages) == 1
        assert pages[0][1] == "Has text."

    def test_none_extract_text_treated_as_empty(self, tmp_path):
        pdf = tmp_path / "dummy.pdf"
        pdf.write_bytes(b"")
        mock_reader = _make_mock_reader([None, "Real text."])  # type: ignore[list-item]
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            pages = extract_pages(pdf)
        assert len(pages) == 1

    def test_missing_pypdf_raises_runtime_error(self, tmp_path):
        pdf = tmp_path / "dummy.pdf"
        pdf.write_bytes(b"")
        with patch("mymem.rag.pdf_parser.PdfReader", None):
            with pytest.raises(RuntimeError, match="pypdf"):
                extract_pages(pdf)


# ---------------------------------------------------------------------------
# parse_pdf  (end-to-end with mocked PdfReader)
# ---------------------------------------------------------------------------

class TestParsePdf:
    def test_produces_chunks_with_global_indices(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"")
        # Two pages, each with enough text for at least one chunk
        page_text = "Sentence. " * 50   # ~500 chars
        mock_reader = _make_mock_reader([page_text, page_text])
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            chunks = parse_pdf(pdf)
        assert len(chunks) >= 2
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(indices)))   # 0, 1, 2, …

    def test_empty_pdf_returns_empty_list(self, tmp_path):
        pdf = tmp_path / "empty.pdf"
        pdf.write_bytes(b"")
        mock_reader = _make_mock_reader(["", "  "])
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            chunks = parse_pdf(pdf)
        assert chunks == []

    def test_page_nums_present(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"")
        mock_reader = _make_mock_reader(["Page 1 text. " * 10, "Page 2 text. " * 10])
        with patch("mymem.rag.pdf_parser.PdfReader", return_value=mock_reader):
            chunks = parse_pdf(pdf)
        page_nums = {c.page_num for c in chunks}
        assert 1 in page_nums
        assert 2 in page_nums
