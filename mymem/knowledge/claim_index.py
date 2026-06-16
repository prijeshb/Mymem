"""
Claim vector index — sqlite-vec semantic index over claims (ADR-011 / ADR-015 D19).

Lives as a `claim_vec` virtual table inside claims.db, alongside the `claims` table. Unlike
same-page retrieval (ADR-015 D8, original), this is a *global* index: a proposition can find
similar active claims on ANY page, so MERGE / SUPERSEDE work across pages, not just within one.

Pure sqlite-vec — vectors are passed in (the embedder lives in the compounding layer). The
table uses the cosine metric, so similarity = 1 - distance. `search` joins back to `claims`
and returns only ACTIVE claims (valid_to IS NULL); superseded ones are filtered out in Python
(mirroring rag/store.py's post-filter pattern, since vec0 KNN can't push down the join filter).
"""
from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

EMBED_DIM = 768  # nomic-embed-text output dimension


def _serialize(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


@dataclass(frozen=True)
class ClaimHit:
    claim_id: int
    page_id: str
    text: str
    confidence: float
    similarity: float  # cosine similarity in [-1, 1]; 1.0 = identical


def _connect(db_path: Path) -> sqlite3.Connection:
    import sqlite_vec

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def init_index(db_path: Path, *, dim: int = EMBED_DIM) -> None:
    """Create the `claim_vec` cosine index if absent. Idempotent. `dim` is overridable for
    tests; production uses the embedder's native dimension."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS claim_vec USING vec0("
                f"claim_id INTEGER PRIMARY KEY, embedding FLOAT[{dim}] distance_metric=cosine)"
            )
    finally:
        conn.close()


def index_claim(db_path: Path, claim_id: int, embedding: list[float]) -> None:
    """Upsert a claim's vector (delete-then-insert so re-indexing replaces)."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM claim_vec WHERE claim_id = ?", (claim_id,))
            conn.execute(
                "INSERT INTO claim_vec(claim_id, embedding) VALUES (?, ?)",
                (claim_id, _serialize(embedding)),
            )
    finally:
        conn.close()


def delete_claim(db_path: Path, claim_id: int) -> None:
    """Remove a claim's vector (e.g. when it is superseded or its source is deleted)."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM claim_vec WHERE claim_id = ?", (claim_id,))
    finally:
        conn.close()


def count(db_path: Path) -> int:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM claim_vec").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def search(
    db_path: Path,
    query_embedding: list[float],
    *,
    top_k: int = 5,
    min_similarity: float = 0.6,
    exclude_page_id: str | None = None,
) -> list[ClaimHit]:
    """Return the active claims most similar to `query_embedding`, best first.

    Over-fetches from the KNN index then filters active / excluded-page / below-threshold in
    Python (vec0 can't push those into the MATCH query), so the result is always ≤ top_k true
    actives above `min_similarity`.
    """
    fetch_k = max(top_k * 4, 20)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT v.claim_id, v.distance, c.page_id, c.text, c.confidence, c.valid_to "
            "FROM claim_vec v JOIN claims c ON c.id = v.claim_id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (_serialize(query_embedding), fetch_k),
        ).fetchall()
    finally:
        conn.close()

    hits: list[ClaimHit] = []
    for r in rows:
        if r["valid_to"] is not None:
            continue  # superseded — not a live candidate
        if exclude_page_id is not None and r["page_id"] == exclude_page_id:
            continue
        similarity = 1.0 - float(r["distance"])  # cosine metric
        if similarity < min_similarity:
            continue
        hits.append(
            ClaimHit(
                claim_id=r["claim_id"],
                page_id=r["page_id"],
                text=r["text"],
                confidence=r["confidence"],
                similarity=similarity,
            )
        )
    return hits[:top_k]


def claims_missing_vector(db_path: Path) -> list[tuple[int, str]]:
    """Active claims that have no vector yet — the work list for a backfill. Returns
    (claim_id, text) pairs."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, text FROM claims "
            "WHERE valid_to IS NULL AND id NOT IN (SELECT claim_id FROM claim_vec) "
            "ORDER BY id"
        ).fetchall()
        return [(r["id"], r["text"]) for r in rows]
    finally:
        conn.close()
