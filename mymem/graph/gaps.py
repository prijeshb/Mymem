"""
Knowledge gaps — concepts the wiki links to but has no page for (ADR-007 follow-up).

A "gap" is a *pageless* entity in the graph (`page_id IS NULL`) that one or more
pages reference via `[[wikilinks]]`. These are the broken links that are genuine
content gaps (not resolution misses): pages worth writing next. Ranked by how many
distinct pages reference the concept — the most-linked missing concept is the
highest-value page to create. When that page is written, the next `graph backfill`
upgrades the entity (sets its page_id) and the links resolve as linked.

Repository pattern, pure SQL/Python — no LLM, no embedder (mirrors graph/store.py).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Gap:
    """A referenced-but-unwritten concept."""
    concept: str                     # canonical name of the missing concept
    entity_id: int
    inbound_refs: int                # distinct pages that wikilink to it
    sample_page_ids: tuple[str, ...]  # a few referencing page ids (for context)


def knowledge_gaps(db_path: Path, *, limit: int = 50, sample: int = 3) -> list[Gap]:
    """Pageless entities ranked by distinct inbound page references (desc).

    A gap must be referenced by at least one page (the JOIN guarantees it); a
    pageless entity with no mentions is a prunable orphan, not a gap. Ties are
    broken by concept name for deterministic ordering. Returns [] if the graph
    db doesn't exist yet.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT e.id AS entity_id, e.canonical AS concept,
                   COUNT(DISTINCT m.page_id) AS inbound_refs
            FROM entities e
            JOIN mentions m ON m.entity_id = e.id
            WHERE e.page_id IS NULL
            GROUP BY e.id
            ORDER BY inbound_refs DESC, e.canonical ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        gaps: list[Gap] = []
        for r in rows:
            samples = conn.execute(
                "SELECT DISTINCT page_id FROM mentions WHERE entity_id = ?"
                " ORDER BY page_id LIMIT ?",
                (r["entity_id"], sample),
            ).fetchall()
            gaps.append(
                Gap(
                    concept=r["concept"],
                    entity_id=r["entity_id"],
                    inbound_refs=r["inbound_refs"],
                    sample_page_ids=tuple(s["page_id"] for s in samples),
                )
            )
        return gaps
    finally:
        conn.close()


def gap_count(db_path: Path) -> int:
    """Total distinct pageless concepts referenced by at least one page."""
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM ("
                " SELECT e.id FROM entities e JOIN mentions m ON m.entity_id = e.id"
                " WHERE e.page_id IS NULL GROUP BY e.id)"
            ).fetchone()[0]
        )
    finally:
        conn.close()
