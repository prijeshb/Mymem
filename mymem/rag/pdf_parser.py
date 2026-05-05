"""
PDF text extraction and chunking.

Uses pypdf for text extraction, then applies a sliding-window chunker
that respects paragraph boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore[assignment,misc]

CHUNK_SIZE = 800   # target characters per chunk
OVERLAP    = 80    # ~10% overlap kept from previous chunk


@dataclass(frozen=True)
class PdfChunk:
    chunk_index: int
    page_num: int | None   # 1-based page number; None for multi-page chunks
    text: str


def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return [(page_num_1based, text), ...] for every page that has text."""
    if PdfReader is None:
        raise RuntimeError("pypdf is required for PDF ingestion — run: pip install pypdf")

    reader = PdfReader(str(pdf_path))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append((i, text))

    log.info("PDF pages extracted", path=str(pdf_path), pages=len(pages))
    return pages


def _split_paragraphs(text: str) -> list[str]:
    """Split text on blank lines; collapse excess whitespace within paragraphs."""
    text = re.sub(r"\n{3,}", "\n\n", text)
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def chunk_text(
    text: str,
    *,
    page_num: int | None = None,
    chunk_offset: int = 0,
) -> list[PdfChunk]:
    """
    Split *text* into overlapping chunks of ~CHUNK_SIZE characters.
    Breaks at paragraph boundaries when possible.
    """
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[PdfChunk] = []
    current: list[str] = []
    current_len = 0
    idx = chunk_offset

    for para in paragraphs:
        if current_len + len(para) > CHUNK_SIZE and current:
            body = "\n\n".join(current).strip()
            if body:
                chunks.append(PdfChunk(chunk_index=idx, page_num=page_num, text=body))
                idx += 1
            # Keep last paragraph as overlap seed for next chunk
            tail = current[-1]
            current = [tail] if len(tail) <= OVERLAP * 2 else []
            current_len = sum(len(p) for p in current)

        current.append(para)
        current_len += len(para)

    if current:
        body = "\n\n".join(current).strip()
        if body:
            chunks.append(PdfChunk(chunk_index=idx, page_num=page_num, text=body))

    return chunks


def parse_pdf(pdf_path: Path) -> list[PdfChunk]:
    """
    Full pipeline: extract all pages → chunk each page → return flat list.
    chunk_index is globally monotonic across the whole document.
    """
    pages = extract_pages(pdf_path)
    all_chunks: list[PdfChunk] = []
    offset = 0

    for page_num, page_text in pages:
        page_chunks = chunk_text(page_text, page_num=page_num, chunk_offset=offset)
        all_chunks.extend(page_chunks)
        offset += len(page_chunks)

    log.info("PDF chunked", path=str(pdf_path), chunks=len(all_chunks))
    return all_chunks
