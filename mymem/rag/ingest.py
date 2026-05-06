"""
RAG ingest orchestrator — PDF path → chunks → embeddings → sqlite-vec store.

Flow:
  1. init_db — ensure schema exists
  2. Skip if source already indexed (unless force=True)
  3. parse_pdf  → list[PdfChunk]
  4. embed_texts → list[list[float]]
  5. insert_chunks into store
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mymem.observability.logger import get_logger
from mymem.rag.embedder import embed_texts
from mymem.rag.pdf_parser import parse_pdf
from mymem.rag.store import init_db, insert_chunks, source_exists
from mymem.wiki.types import slugify

log = get_logger(__name__)


@dataclass
class RagIngestResult:
    source_path: str
    chunk_count: int = 0
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.skipped


async def ingest_pdf(
    pdf_path: Path,
    *,
    db_path: Path,
    base_url: str = "http://localhost:11434",
    force: bool = False,
) -> RagIngestResult:
    """
    Index a PDF file into the RAG vector store.

    Args:
        pdf_path:  Path to the PDF file (must exist).
        db_path:   Path to mymem.db — sqlite-vec tables are added here.
        base_url:  Ollama server URL.
        force:     Re-index even if source is already present.
    """
    source_str  = str(pdf_path.resolve())
    source_slug = slugify(pdf_path.stem)

    init_db(db_path)

    if not force and source_exists(db_path, source_str):
        log.info("RAG: already indexed — skipping", source=source_str)
        return RagIngestResult(
            source_path=source_str,
            skipped=True,
            skip_reason="already indexed",
        )

    if not pdf_path.exists():
        return RagIngestResult(
            source_path=source_str,
            error=f"File not found: {pdf_path}",
        )

    # --- 1. Parse ---------------------------------------------------------
    try:
        pdf_chunks = parse_pdf(pdf_path)
    except Exception as exc:
        log.error("RAG: PDF parse failed", source=source_str, error=str(exc))
        return RagIngestResult(source_path=source_str, error=f"PDF parse failed: {exc}")

    if not pdf_chunks:
        return RagIngestResult(
            source_path=source_str,
            error="No text could be extracted from this PDF",
        )

    # --- 2. Embed ---------------------------------------------------------
    texts = [c.text for c in pdf_chunks]
    log.info("RAG: embedding", source=source_str, chunks=len(texts))
    try:
        embeddings = await embed_texts(texts, base_url=base_url)
    except Exception as exc:
        log.error("RAG: embedding failed", source=source_str, error=str(exc))
        return RagIngestResult(source_path=source_str, error=f"Embedding failed: {exc}")

    # --- 3. Store ---------------------------------------------------------
    chunk_dicts: list[dict[str, object]] = [
        {
            "source_path":  source_str,
            "source_slug":  source_slug,
            "chunk_index":  c.chunk_index,
            "page_num":     c.page_num,
            "text":         c.text,
        }
        for c in pdf_chunks
    ]
    try:
        insert_chunks(db_path, chunk_dicts, embeddings)
    except Exception as exc:
        log.error("RAG: store insert failed", source=source_str, error=str(exc))
        return RagIngestResult(source_path=source_str, error=f"Store insert failed: {exc}")

    log.info("RAG: indexed OK", source=source_str, chunks=len(pdf_chunks))
    return RagIngestResult(source_path=source_str, chunk_count=len(pdf_chunks))


async def ingest_text_chunks(
    text: str,
    *,
    source_id: str,
    db_path: Path,
    base_url: str = "http://localhost:11434",
    force: bool = False,
) -> RagIngestResult:
    """
    Index raw text into the RAG vector store (no pypdf involved).

    Used for paste-text ingestion where there is no physical PDF file.

    Args:
        text:      Raw source text to chunk and embed.
        source_id: Stable identifier (e.g. title or slug) stored as source_path.
        db_path:   Path to the sqlite-vec store.
        base_url:  Ollama server URL.
        force:     Re-index even if source_id already present.
    """
    from mymem.rag.pdf_parser import chunk_text

    init_db(db_path)

    if not force and source_exists(db_path, source_id):
        log.info("RAG: already indexed — skipping", source=source_id)
        return RagIngestResult(source_path=source_id, skipped=True, skip_reason="already indexed")

    if not text.strip():
        return RagIngestResult(source_path=source_id, error="Empty text — nothing to index")

    text_chunks = chunk_text(text, page_num=None, chunk_offset=0)
    if not text_chunks:
        return RagIngestResult(source_path=source_id, error="Text produced no chunks after splitting")

    texts = [c.text for c in text_chunks]
    log.info("RAG: embedding text chunks", source=source_id, chunks=len(texts))
    try:
        embeddings = await embed_texts(texts, base_url=base_url)
    except Exception as exc:
        log.error("RAG: embedding failed", source=source_id, error=str(exc))
        return RagIngestResult(source_path=source_id, error=f"Embedding failed: {exc}")

    source_slug = slugify(source_id.split("/")[-1].replace(".txt", ""))
    chunk_dicts: list[dict[str, object]] = [
        {
            "source_path":  source_id,
            "source_slug":  source_slug,
            "chunk_index":  c.chunk_index,
            "page_num":     c.page_num,
            "text":         c.text,
        }
        for c in text_chunks
    ]
    try:
        insert_chunks(db_path, chunk_dicts, embeddings)
    except Exception as exc:
        log.error("RAG: store insert failed", source=source_id, error=str(exc))
        return RagIngestResult(source_path=source_id, error=f"Store insert failed: {exc}")

    log.info("RAG: text indexed OK", source=source_id, chunks=len(text_chunks))
    return RagIngestResult(source_path=source_id, chunk_count=len(text_chunks))
