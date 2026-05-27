"""
Ingest quality analytics — tracks per-ingest metrics to measure feature impact.

Currently tracks YouTube ingests to compare enriched (yt-dlp metadata) vs
plain (transcript-only) outcomes: concepts extracted, page body length, wikilinks.

Usage:
    from mymem.observability.ingest_analytics import record_ingest, youtube_comparison

    record_ingest(db_path, source_type="youtube", metadata_enriched=True, ...)
    stats = youtube_comparison(db_path)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mymem.observability.logger import get_logger

log = get_logger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ingest_analytics (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    source_type         TEXT    NOT NULL,
    metadata_enriched   INTEGER NOT NULL DEFAULT 0,
    source_chars        INTEGER NOT NULL DEFAULT 0,
    concepts_extracted  INTEGER NOT NULL DEFAULT 0,
    pages_written       INTEGER NOT NULL DEFAULT 0,
    pages_updated       INTEGER NOT NULL DEFAULT 0,
    avg_page_chars      REAL    NOT NULL DEFAULT 0,
    avg_wikilinks       REAL    NOT NULL DEFAULT 0,
    chunk_count         INTEGER NOT NULL DEFAULT 1,
    idea_duplicate_rate REAL    NOT NULL DEFAULT 0
)
"""

_CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_ia_source_type
    ON ingest_analytics(source_type, metadata_enriched)
"""

_MIGRATIONS = [
    "ALTER TABLE ingest_analytics ADD COLUMN chunk_count INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE ingest_analytics ADD COLUMN idea_duplicate_rate REAL NOT NULL DEFAULT 0",
]


@dataclass
class IngestRecord:
    source_type:         str
    metadata_enriched:   bool
    source_chars:        int
    concepts_extracted:  int
    pages_written:       int
    pages_updated:       int
    avg_page_chars:      float
    avg_wikilinks:       float
    chunk_count:         int = 1
    idea_duplicate_rate: float = 0.0


@dataclass
class EnrichmentStats:
    """Comparison between enriched and plain ingest runs."""
    enriched_count:       int
    plain_count:          int
    enriched_avg_concepts: float
    plain_avg_concepts:   float
    enriched_avg_page_chars: float
    plain_avg_page_chars: float
    enriched_avg_wikilinks: float
    plain_avg_wikilinks:  float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_table(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_IDX)
        for migration in _MIGRATIONS:
            try:
                conn.execute(migration)
            except sqlite3.OperationalError:
                pass  # column already exists


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_ingest(
    db_path: Path,
    *,
    source_type: str,
    metadata_enriched: bool,
    source_chars: int,
    concepts_extracted: int,
    pages_written: int,
    pages_updated: int,
    avg_page_chars: float,
    avg_wikilinks: float,
    chunk_count: int = 1,
    idea_duplicate_rate: float = 0.0,
) -> None:
    """Persist one ingest quality record. Silent on failure."""
    try:
        _ensure_table(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO ingest_analytics
                   (created_at, source_type, metadata_enriched, source_chars,
                    concepts_extracted, pages_written, pages_updated,
                    avg_page_chars, avg_wikilinks, chunk_count, idea_duplicate_rate)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    source_type,
                    int(metadata_enriched),
                    source_chars,
                    concepts_extracted,
                    pages_written,
                    pages_updated,
                    avg_page_chars,
                    avg_wikilinks,
                    chunk_count,
                    idea_duplicate_rate,
                ),
            )
        log.debug(
            "Ingest analytics recorded",
            source_type=source_type,
            metadata_enriched=metadata_enriched,
            concepts_extracted=concepts_extracted,
            avg_page_chars=round(avg_page_chars),
            avg_wikilinks=round(avg_wikilinks, 1),
        )
    except Exception as exc:
        log.warning("Failed to record ingest analytics", error=str(exc))


def youtube_comparison(db_path: Path) -> EnrichmentStats:
    """
    Compare enriched vs plain YouTube ingests.

    Returns aggregated averages for each group so you can see whether
    yt-dlp metadata is producing richer wiki pages.
    """
    try:
        _ensure_table(db_path)
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """SELECT
                       metadata_enriched,
                       COUNT(*)                    AS cnt,
                       AVG(concepts_extracted)     AS avg_concepts,
                       AVG(avg_page_chars)         AS avg_chars,
                       AVG(avg_wikilinks)          AS avg_wikilinks
                   FROM ingest_analytics
                   WHERE source_type = 'youtube'
                   GROUP BY metadata_enriched""",
            ).fetchall()
    except Exception as exc:
        log.warning("Failed to query ingest analytics", error=str(exc))
        rows = []

    enriched = next((r for r in rows if r[0] == 1), None)
    plain    = next((r for r in rows if r[0] == 0), None)

    def _safe(row, idx: int, default: float = 0.0) -> float:
        return float(row[idx]) if row and row[idx] is not None else default

    return EnrichmentStats(
        enriched_count=int(enriched[1]) if enriched else 0,
        plain_count=int(plain[1]) if plain else 0,
        enriched_avg_concepts=_safe(enriched, 2),
        plain_avg_concepts=_safe(plain, 2),
        enriched_avg_page_chars=_safe(enriched, 3),
        plain_avg_page_chars=_safe(plain, 3),
        enriched_avg_wikilinks=_safe(enriched, 4),
        plain_avg_wikilinks=_safe(plain, 4),
    )


def recent_ingests(db_path: Path, limit: int = 20) -> list[dict]:
    """Return the N most recent ingest records as plain dicts."""
    try:
        _ensure_table(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM ingest_analytics
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("Failed to fetch recent ingests", error=str(exc))
        return []
