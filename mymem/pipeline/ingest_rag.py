"""
Ingest-side RAG indexing helpers (split out of ingest.py).

Thin best-effort wrappers that push ingested content into the sqlite-vec store
(`data/rag.db`). Each is fire-and-forget from ingest's perspective — failures are
logged and swallowed so RAG never breaks an ingest.
"""
from __future__ import annotations

from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)


async def _rag_index_pdf(source: str, *, db_path: Path | None) -> int:
    """Index a local PDF into the RAG vector store. Returns chunk count (0 on failure)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_pdf

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_pdf(
            Path(source),
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info("RAG index: already indexed", source=source, reason=rag_result.skip_reason)
        elif rag_result.ok:
            log.info("RAG index: complete", source=source, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source, error=rag_result.error)
        return rag_result.chunk_count if rag_result.ok else 0
    except Exception as exc:
        log.warning("RAG indexing raised unexpectedly", source=source, error=str(exc))
        return 0


async def _rag_index_wiki(page_path: Path, *, db_path: Path | None) -> None:
    """Index a wiki page into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_wiki_page

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        result = await ingest_wiki_page(
            page_path,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
            force=True,
        )
        if result.ok:
            log.info("Wiki RAG indexed", path=str(page_path), chunks=result.chunk_count)
        elif not result.skipped:
            log.warning("Wiki RAG index failed", path=str(page_path), error=result.error)
    except Exception as exc:
        log.warning("Wiki RAG indexing raised unexpectedly", path=str(page_path), error=str(exc))


async def _rag_index_text(source_name: str, text: str, *, db_path: Path | None) -> None:
    """Index raw text into the RAG vector store (best-effort; never raises)."""
    try:
        from mymem.config import get_settings
        from mymem.rag.ingest import ingest_text_chunks

        settings = get_settings()
        rag_db = db_path.parent / "rag.db" if db_path else Path("data/rag.db")
        rag_result = await ingest_text_chunks(
            text,
            source_id=source_name,
            db_path=rag_db,
            base_url=settings.ollama.base_url,
        )
        if rag_result.skipped:
            log.info(
                "RAG index: already indexed", source=source_name, reason=rag_result.skip_reason
            )
        elif rag_result.ok:
            log.info("RAG index: complete", source=source_name, chunks=rag_result.chunk_count)
        else:
            log.warning("RAG index: failed", source=source_name, error=rag_result.error)
    except Exception as exc:
        log.warning(
            "RAG text indexing raised unexpectedly — continuing",
            source=source_name, error=str(exc),
        )
