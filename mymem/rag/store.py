"""
RAG chunk store — sqlite-vec backed vector storage.

Schema (added to mymem.db):
  rag_chunks      — chunk metadata (source, page, text, timestamps)
  rag_embeddings  — vec0 virtual table (chunk_id FK, embedding FLOAT[768])
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

EMBED_DIM = 768  # nomic-embed-text output dimension


def _serialize(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


@dataclass(frozen=True)
class RagChunk:
    id: int
    source_path: str
    source_slug: str
    chunk_index: int
    page_num: int | None
    text: str
    char_count: int
    created_at: str


@dataclass(frozen=True)
class SearchResult:
    chunk: RagChunk
    distance: float


def _connect(db_path: Path) -> sqlite3.Connection:
    import sqlite_vec  # type: ignore[import-untyped]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_db(db_path: Path) -> None:
    """Create rag_chunks and rag_embeddings tables if they don't exist."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_path TEXT    NOT NULL,
                    source_slug TEXT    NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page_num    INTEGER,
                    text        TEXT    NOT NULL,
                    char_count  INTEGER NOT NULL,
                    created_at  TEXT    NOT NULL
                )
            """)
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_embeddings USING vec0(
                    chunk_id  INTEGER PRIMARY KEY,
                    embedding FLOAT[{EMBED_DIM}]
                )
            """)
    finally:
        conn.close()


def source_exists(db_path: Path, source_path: str) -> bool:
    """Return True if this source has already been indexed."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM rag_chunks WHERE source_path = ? LIMIT 1",
            (source_path,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_chunks(
    db_path: Path,
    chunks: list[dict[str, object]],
    embeddings: list[list[float]],
) -> None:
    """
    Insert chunks + their embeddings atomically.

    Each chunk dict must have: source_path, source_slug, chunk_index, text.
    Optional: page_num.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}")

    conn = _connect(db_path)
    now = datetime.now(UTC).isoformat()
    try:
        with conn:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                cur = conn.execute(
                    """
                    INSERT INTO rag_chunks
                        (source_path, source_slug, chunk_index, page_num,
                         text, char_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["source_path"],
                        chunk["source_slug"],
                        chunk["chunk_index"],
                        chunk.get("page_num"),
                        chunk["text"],
                        len(str(chunk["text"])),
                        now,
                    ),
                )
                chunk_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO rag_embeddings(chunk_id, embedding) VALUES (?, ?)",
                    (chunk_id, _serialize(embedding)),
                )
    finally:
        conn.close()


def search_similar(
    db_path: Path,
    query_embedding: list[float],
    top_k: int = 10,
) -> list[SearchResult]:
    """Return the top-k chunks closest to query_embedding by cosine distance."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.chunk_id, e.distance,
                   c.source_path, c.source_slug, c.chunk_index,
                   c.page_num, c.text, c.char_count, c.created_at
            FROM rag_embeddings e
            JOIN rag_chunks c ON c.id = e.chunk_id
            WHERE e.embedding MATCH ?
              AND k = ?
            ORDER BY e.distance
            """,
            (_serialize(query_embedding), top_k),
        ).fetchall()

        return [
            SearchResult(
                chunk=RagChunk(
                    id=r["chunk_id"],
                    source_path=r["source_path"],
                    source_slug=r["source_slug"],
                    chunk_index=r["chunk_index"],
                    page_num=r["page_num"],
                    text=r["text"],
                    char_count=r["char_count"],
                    created_at=r["created_at"],
                ),
                distance=r["distance"],
            )
            for r in rows
        ]
    finally:
        conn.close()


def list_sources(db_path: Path) -> list[dict[str, object]]:
    """Return one summary row per indexed source."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT source_path, source_slug,
                   COUNT(*)     AS chunk_count,
                   MIN(created_at) AS created_at
            FROM rag_chunks
            GROUP BY source_path, source_slug
            ORDER BY created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_source(db_path: Path, source_path: str) -> int:
    """Delete all chunks + embeddings for a source. Returns deleted chunk count."""
    conn = _connect(db_path)
    try:
        chunk_ids: list[int] = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM rag_chunks WHERE source_path = ?", (source_path,)
            ).fetchall()
        ]
        if not chunk_ids:
            return 0

        placeholders = ",".join("?" * len(chunk_ids))
        with conn:
            conn.execute(
                f"DELETE FROM rag_embeddings WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            conn.execute(
                "DELETE FROM rag_chunks WHERE source_path = ?", (source_path,)
            )
        log.info("RAG: source deleted", source=source_path, chunks=len(chunk_ids))
        return len(chunk_ids)
    finally:
        conn.close()
