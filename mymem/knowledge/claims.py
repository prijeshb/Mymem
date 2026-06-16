"""
Claims store — bi-temporal atomic propositions in data/claims.db (ADR-011 / ADR-015 Phase 2).

Repository pattern (module-level functions over SQLite, mirroring graph/store.py and
rag/store.py): callers depend on these functions, never on the schema. Pure Python —
no LLM, no embedder.

Each claim is one atomic proposition extracted from a source, keyed on the page's
**stable ULID** (ADR-013) — never the mutable slug, so a rename or surface-form merge
never orphans provenance. Claims are bi-temporal: `valid_from`/`valid_to` is "valid
time" (when the fact held), `created` is "transaction time" (when we recorded it).
SUPERSEDE sets `valid_to` + `superseded_by` on the old claim and adds a new one —
contradicted knowledge is retired, never hard-deleted, so the audit trail survives.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id       TEXT NOT NULL,             -- stable page ULID (ADR-013), NOT the slug
    text          TEXT NOT NULL,             -- the atomic proposition
    source_id     TEXT NOT NULL,             -- raw/ filename or URL
    source_span   TEXT NOT NULL DEFAULT '',  -- verbatim substring grounding the claim
    confidence    REAL NOT NULL DEFAULT 1.0,
    valid_from    TEXT NOT NULL,             -- ISO date; bi-temporal "valid time"
    valid_to      TEXT,                      -- NULL = currently valid
    superseded_by INTEGER,                   -- FK claims.id; set on SUPERSEDE
    created       TEXT NOT NULL              -- ISO datetime; "transaction time"
);
CREATE INDEX IF NOT EXISTS idx_claims_page   ON claims(page_id);
CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id);
CREATE INDEX IF NOT EXISTS idx_claims_active ON claims(valid_to) WHERE valid_to IS NULL;
"""


@dataclass(frozen=True)
class Claim:
    id: int
    page_id: str
    text: str
    source_id: str
    source_span: str
    confidence: float
    valid_from: str
    valid_to: str | None
    superseded_by: int | None
    created: str


@dataclass(frozen=True)
class NewClaim:
    """A claim to persist, before it has an id (input to replace_source_claims)."""
    page_id: str
    text: str
    source_span: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class ClaimsStats:
    total: int
    active: int          # valid_to IS NULL
    superseded: int      # valid_to IS NOT NULL


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _require(value: str, name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{name} must not be blank")
    return cleaned


def _check_confidence(confidence: float) -> float:
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence}")
    return confidence


def _row_to_claim(row: sqlite3.Row) -> Claim:
    return Claim(
        id=row["id"],
        page_id=row["page_id"],
        text=row["text"],
        source_id=row["source_id"],
        source_span=row["source_span"],
        confidence=row["confidence"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        superseded_by=row["superseded_by"],
        created=row["created"],
    )


def _get(conn: sqlite3.Connection, claim_id: int) -> Claim | None:
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    return _row_to_claim(row) if row else None


def _insert(conn: sqlite3.Connection, new: NewClaim, source_id: str, valid_from: str) -> Claim:
    cur = conn.execute(
        "INSERT INTO claims"
        " (page_id, text, source_id, source_span, confidence, valid_from, created)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            _require(new.page_id, "page_id"),
            _require(new.text, "text"),
            source_id,
            new.source_span,
            _check_confidence(new.confidence),
            valid_from,
            _now(),
        ),
    )
    inserted = _get(conn, int(cur.lastrowid or 0))
    assert inserted is not None  # just inserted
    return inserted


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def init_db(db_path: Path) -> None:
    """Create the claims table + indexes if absent. Idempotent."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.executescript(_SCHEMA)
    finally:
        conn.close()


def add_claim(
    db_path: Path,
    *,
    page_id: str,
    text: str,
    source_id: str,
    source_span: str = "",
    confidence: float = 1.0,
    valid_from: str | None = None,
) -> Claim:
    """Insert one active claim. `valid_from` defaults to today (ISO date)."""
    source_id = _require(source_id, "source_id")
    conn = _connect(db_path)
    try:
        with conn:
            new = NewClaim(
                page_id=page_id, text=text, source_span=source_span, confidence=confidence
            )
            return _insert(conn, new, source_id, valid_from or _today())
    finally:
        conn.close()


def get_claim(db_path: Path, claim_id: int) -> Claim | None:
    conn = _connect(db_path)
    try:
        return _get(conn, claim_id)
    finally:
        conn.close()


def claims_for_page(db_path: Path, page_id: str, *, active_only: bool = False) -> list[Claim]:
    """All claims on a page, newest first. `active_only` filters out superseded claims."""
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM claims WHERE page_id = ?"
        if active_only:
            sql += " AND valid_to IS NULL"
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, (page_id,)).fetchall()
        return [_row_to_claim(r) for r in rows]
    finally:
        conn.close()


def claims_for_source(db_path: Path, source_id: str) -> list[Claim]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM claims WHERE source_id = ? ORDER BY id", (source_id,)
        ).fetchall()
        return [_row_to_claim(r) for r in rows]
    finally:
        conn.close()


def supersede_claim(
    db_path: Path, old_claim_id: int, *, by: int, valid_to: str | None = None
) -> None:
    """Retire `old_claim_id`: set its valid_to (default today) + superseded_by = `by`.

    Bi-temporal — the old row is kept for audit, never deleted. Both claims must exist.
    """
    conn = _connect(db_path)
    try:
        with conn:
            if _get(conn, old_claim_id) is None:
                raise ValueError(f"claim {old_claim_id} does not exist")
            if _get(conn, by) is None:
                raise ValueError(f"superseding claim {by} does not exist")
            conn.execute(
                "UPDATE claims SET valid_to = ?, superseded_by = ? WHERE id = ?",
                (valid_to or _today(), by, old_claim_id),
            )
    finally:
        conn.close()


def corroborate(db_path: Path, claim_id: int, *, delta: float = 0.1) -> Claim:
    """Bump a claim's confidence (NOOP/MERGE corroboration), clamped to ≤ 1.0."""
    conn = _connect(db_path)
    try:
        with conn:
            current = _get(conn, claim_id)
            if current is None:
                raise ValueError(f"claim {claim_id} does not exist")
            new_conf = min(1.0, current.confidence + delta)
            conn.execute(
                "UPDATE claims SET confidence = ? WHERE id = ?", (new_conf, claim_id)
            )
            refreshed = _get(conn, claim_id)
            assert refreshed is not None
            return refreshed
    finally:
        conn.close()


def delete_source(db_path: Path, source_id: str) -> int:
    """Delete all claims for a source. Returns the count removed.

    Any surviving claim that pointed at a deleted claim via superseded_by has that
    pointer nulled, so no dangling FK is left behind.
    """
    conn = _connect(db_path)
    try:
        with conn:
            doomed = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM claims WHERE source_id = ?", (source_id,)
                ).fetchall()
            ]
            if not doomed:
                return 0
            marks = ",".join("?" * len(doomed))
            conn.execute(
                f"UPDATE claims SET superseded_by = NULL WHERE superseded_by IN ({marks})",  # noqa: S608
                doomed,
            )
            conn.execute(
                f"DELETE FROM claims WHERE id IN ({marks})", doomed  # noqa: S608
            )
            log.info("Claims: source deleted", source=source_id, claims=len(doomed))
            return len(doomed)
    finally:
        conn.close()


def replace_source_claims(
    db_path: Path, source_id: str, new_claims: list[NewClaim]
) -> list[Claim]:
    """Atomically rebuild a source's claims: delete its old claims, insert `new_claims`.

    Idempotent per source — re-ingesting the same source replaces (never accretes) its
    provenance. This is the naive-ADD persistence path; the ADD/MERGE/SUPERSEDE/NOOP
    decision pipeline (Phase 3) will drive these writes per-claim instead.
    """
    source_id = _require(source_id, "source_id")
    today = _today()
    conn = _connect(db_path)
    try:
        with conn:
            doomed = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM claims WHERE source_id = ?", (source_id,)
                ).fetchall()
            ]
            if doomed:
                marks = ",".join("?" * len(doomed))
                conn.execute(
                    f"UPDATE claims SET superseded_by = NULL WHERE superseded_by IN ({marks})",  # noqa: S608
                    doomed,
                )
                conn.execute(f"DELETE FROM claims WHERE id IN ({marks})", doomed)  # noqa: S608
            return [_insert(conn, nc, source_id, today) for nc in new_claims]
    finally:
        conn.close()


def stats(db_path: Path) -> ClaimsStats:
    """Counts for the dashboard / lint: total, active, superseded."""
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM claims WHERE valid_to IS NULL"
        ).fetchone()[0]
        return ClaimsStats(total=total, active=active, superseded=total - active)
    finally:
        conn.close()
