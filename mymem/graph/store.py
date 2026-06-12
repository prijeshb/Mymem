"""
Graph entity store — entities / aliases / mentions in data/graph.db.

Repository pattern (module-level functions over SQLite, mirroring rag/store.py):
callers depend on these functions, never on the schema. Pure Python — no LLM,
no embedder. Pruning rule: an entity survives while it has mentions OR its own
wiki page; delete_page() removes both kinds of anchorage and prunes orphans.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

ENTITY_TYPES = ("person", "project", "system", "organization", "concept")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical   TEXT NOT NULL,
    type        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    page_slug   TEXT,
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_canonical
    ON entities(canonical COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS aliases (
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    alias       TEXT NOT NULL,
    UNIQUE(entity_id, alias)
);
CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    page_slug   TEXT NOT NULL,
    span        TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mentions_page   ON mentions(page_slug);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
"""


@dataclass(frozen=True)
class Entity:
    id: int
    canonical: str
    type: str
    description: str
    page_slug: str | None
    created: str
    updated: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Mention:
    entity_id: int
    page_slug: str
    span: str
    source_id: str
    created: str


@dataclass(frozen=True)
class GraphStats:
    total_entities: int
    total_mentions: int
    singleton_count: int   # entities mentioned on <= 1 distinct page
    singleton_rate: float


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalize(name: str) -> str:
    return " ".join(name.split())


def _validate_type(entity_type: str) -> None:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity type: {entity_type!r}. Must be one of {ENTITY_TYPES}."
        )


def init_db(db_path: Path) -> None:
    """Create graph tables if they don't exist. Idempotent."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.executescript(_SCHEMA)
    finally:
        conn.close()


def _row_to_entity(conn: sqlite3.Connection, row: sqlite3.Row) -> Entity:
    alias_rows = conn.execute(
        "SELECT alias FROM aliases WHERE entity_id = ? ORDER BY alias", (row["id"],)
    ).fetchall()
    return Entity(
        id=row["id"],
        canonical=row["canonical"],
        type=row["type"],
        description=row["description"],
        page_slug=row["page_slug"],
        created=row["created"],
        updated=row["updated"],
        aliases=tuple(r["alias"] for r in alias_rows),
    )


def upsert_entity(
    db_path: Path,
    canonical: str,
    *,
    entity_type: str,
    description: str = "",
    page_slug: str | None = None,
) -> Entity:
    """Insert an entity, or update the existing one with the same canonical name
    (case-insensitive). Empty description/page_slug never overwrite existing values."""
    _validate_type(entity_type)
    canonical = _normalize(canonical)
    if not canonical:
        raise ValueError("canonical name must not be blank")

    conn = _connect(db_path)
    try:
        with conn:
            existing = conn.execute(
                "SELECT * FROM entities WHERE canonical = ? COLLATE NOCASE", (canonical,)
            ).fetchone()
            if existing is None:
                cur = conn.execute(
                    "INSERT INTO entities"
                    " (canonical, type, description, page_slug, created, updated)"
                    " VALUES (?,?,?,?,?,?)",
                    (canonical, entity_type, description, page_slug, _now(), _now()),
                )
                entity_id = int(cur.lastrowid or 0)
            else:
                entity_id = existing["id"]
                conn.execute(
                    "UPDATE entities SET description = ?, page_slug = ?, updated = ? WHERE id = ?",
                    (
                        description or existing["description"],
                        page_slug if page_slug is not None else existing["page_slug"],
                        _now(),
                        entity_id,
                    ),
                )
            row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
            return _row_to_entity(conn, row)
    finally:
        conn.close()


def get_entity(db_path: Path, entity_id: int) -> Entity | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return _row_to_entity(conn, row) if row else None
    finally:
        conn.close()


def find_entity(db_path: Path, name: str) -> Entity | None:
    """Exact lookup by canonical name or alias, case-insensitive."""
    name = _normalize(name)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM entities WHERE canonical = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT e.* FROM entities e JOIN aliases a ON a.entity_id = e.id"
                " WHERE a.alias = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        return _row_to_entity(conn, row) if row else None
    finally:
        conn.close()


def list_entities(db_path: Path, *, entity_type: str = "", limit: int = 500) -> list[Entity]:
    if entity_type:
        _validate_type(entity_type)
    conn = _connect(db_path)
    try:
        if entity_type:
            rows = conn.execute(
                "SELECT * FROM entities WHERE type = ? ORDER BY canonical LIMIT ?",
                (entity_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM entities ORDER BY canonical LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_entity(conn, r) for r in rows]
    finally:
        conn.close()


def _require_entity(conn: sqlite3.Connection, entity_id: int) -> None:
    if conn.execute("SELECT 1 FROM entities WHERE id = ?", (entity_id,)).fetchone() is None:
        raise ValueError(f"entity {entity_id} does not exist")


def add_alias(db_path: Path, entity_id: int, alias: str) -> None:
    """Attach an alias to an entity. Idempotent."""
    alias = _normalize(alias)
    if not alias:
        raise ValueError("alias must not be blank")
    conn = _connect(db_path)
    try:
        with conn:
            _require_entity(conn, entity_id)
            conn.execute(
                "INSERT OR IGNORE INTO aliases (entity_id, alias) VALUES (?,?)",
                (entity_id, alias),
            )
    finally:
        conn.close()


def update_entity_type(db_path: Path, entity_id: int, entity_type: str) -> None:
    """Set the type of an existing entity (used by Tier-2 classify backfill)."""
    _validate_type(entity_type)
    conn = _connect(db_path)
    try:
        with conn:
            _require_entity(conn, entity_id)
            conn.execute(
                "UPDATE entities SET type = ?, updated = ? WHERE id = ?",
                (entity_type, _now(), entity_id),
            )
    finally:
        conn.close()


def delete_mentions_by_source(db_path: Path, source_ids: tuple[str, ...]) -> int:
    """Delete all mentions whose source_id is in *source_ids*.

    Lets structural (tier-1) mentions be wiped and rebuilt on re-seed while
    ingest-derived mentions survive. Returns the number removed.
    """
    if not source_ids:
        return 0
    conn = _connect(db_path)
    try:
        with conn:
            # f-string injects only "?" placeholder marks — values stay parameterized
            marks = ",".join("?" * len(source_ids))
            cur = conn.execute(
                f"DELETE FROM mentions WHERE source_id IN ({marks})",  # noqa: S608
                source_ids,
            )
            return cur.rowcount
    finally:
        conn.close()


def add_mention(
    db_path: Path,
    entity_id: int,
    page_slug: str,
    *,
    span: str = "",
    source_id: str = "",
) -> None:
    conn = _connect(db_path)
    try:
        with conn:
            _require_entity(conn, entity_id)
            conn.execute(
                "INSERT INTO mentions (entity_id, page_slug, span, source_id, created)"
                " VALUES (?,?,?,?,?)",
                (entity_id, page_slug, span, source_id, _now()),
            )
    finally:
        conn.close()


def mentions_for_page(db_path: Path, page_slug: str) -> list[Mention]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT entity_id, page_slug, span, source_id, created"
            " FROM mentions WHERE page_slug = ? ORDER BY id",
            (page_slug,),
        ).fetchall()
        return [Mention(**dict(r)) for r in rows]
    finally:
        conn.close()


def entities_for_page(db_path: Path, page_slug: str) -> list[Entity]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT e.* FROM entities e JOIN mentions m ON m.entity_id = e.id"
            " WHERE m.page_slug = ? ORDER BY e.canonical",
            (page_slug,),
        ).fetchall()
        return [_row_to_entity(conn, r) for r in rows]
    finally:
        conn.close()


def pages_for_entity(db_path: Path, entity_id: int) -> list[str]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT page_slug FROM mentions WHERE entity_id = ? ORDER BY page_slug",
            (entity_id,),
        ).fetchall()
        return [r["page_slug"] for r in rows]
    finally:
        conn.close()


def delete_page(db_path: Path, page_slug: str) -> int:
    """Remove all graph anchorage of a deleted/archived wiki page.

    1. Delete its mentions.
    2. Clear page_slug on the entity whose page this was.
    3. Prune entities left with no mentions and no page (and their aliases).

    Returns the number of mentions removed. Idempotent.
    """
    conn = _connect(db_path)
    try:
        with conn:
            cur = conn.execute("DELETE FROM mentions WHERE page_slug = ?", (page_slug,))
            removed = cur.rowcount
            conn.execute(
                "UPDATE entities SET page_slug = NULL, updated = ? WHERE page_slug = ?",
                (_now(), page_slug),
            )
            orphan_ids = [
                r["id"]
                for r in conn.execute(
                    "SELECT e.id FROM entities e"
                    " WHERE e.page_slug IS NULL"
                    " AND NOT EXISTS (SELECT 1 FROM mentions m WHERE m.entity_id = e.id)"
                ).fetchall()
            ]
            if orphan_ids:
                # f-string injects only "?" placeholder marks — values stay parameterized
                marks = ",".join("?" * len(orphan_ids))
                conn.execute(f"DELETE FROM aliases WHERE entity_id IN ({marks})", orphan_ids)  # noqa: S608
                conn.execute(f"DELETE FROM entities WHERE id IN ({marks})", orphan_ids)  # noqa: S608
                log.info("Pruned orphan entities", count=len(orphan_ids), page=page_slug)
        return removed
    finally:
        conn.close()


def stats(db_path: Path) -> GraphStats:
    """Explosion-alarm metrics: singleton = entity mentioned on <= 1 distinct page."""
    conn = _connect(db_path)
    try:
        total_entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        total_mentions = conn.execute("SELECT COUNT(*) FROM mentions").fetchone()[0]
        singleton_count = conn.execute(
            "SELECT COUNT(*) FROM entities e WHERE"
            " (SELECT COUNT(DISTINCT page_slug) FROM mentions m WHERE m.entity_id = e.id) <= 1"
        ).fetchone()[0]
        rate = singleton_count / total_entities if total_entities else 0.0
        return GraphStats(
            total_entities=total_entities,
            total_mentions=total_mentions,
            singleton_count=singleton_count,
            singleton_rate=rate,
        )
    finally:
        conn.close()
