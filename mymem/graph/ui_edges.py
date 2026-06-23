"""Shared-concept edges for the UI knowledge graph (ADR-007/008 follow-up).

The wikilink graph (`GET /api/graph`) only links two pages when one `[[wikilinks]]` the
other by exact title — which leaves most pages isolated, because they reference concepts
that don't have their own page yet (the knowledge gaps). This module bridges those islands
using the entity layer: two pages that *mention the same concept* get an edge, weighted by
how many concepts they share.

Generic hub concepts (mentioned by many pages) are skipped — connecting everything to
everything is a hairball, not a signal. Pure SQL/Python, no LLM (mirrors graph/gaps.py).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SharedEdge:
    """A page-to-page edge inferred from a shared concept mention."""

    source: str            # page title
    target: str            # page title
    weight: int            # number of concepts the two pages share
    via: tuple[str, ...]   # up to a few shared concept names (for tooltips)


def shared_entity_edges(
    db_path: Path,
    page_id_to_title: Mapping[str, str],
    *,
    min_pages: int = 2,
    max_pages: int = 6,
    max_edges: int = 500,
) -> list[SharedEdge]:
    """Page-pair edges where both pages mention the same entity.

    Considers only entities mentioned by ``[min_pages, max_pages]`` distinct pages —
    above ``max_pages`` a concept is a generic hub that would hairball the view. Page
    ids are resolved via ``page_id_to_title`` (keyed by ULID id *and* legacy slug, so
    both pre- and post-rekey graphs work). Edges are de-duplicated per page-pair,
    weighted by the number of shared concepts, sorted strongest-first, and capped at
    ``max_edges``. Returns ``[]`` if the graph db doesn't exist yet.
    """
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        entities = conn.execute(
            """
            SELECT e.id AS eid, e.canonical AS name, COUNT(DISTINCT m.page_id) AS pc
            FROM entities e JOIN mentions m ON m.entity_id = e.id
            GROUP BY e.id
            HAVING pc BETWEEN ? AND ?
            ORDER BY pc ASC, e.canonical ASC
            """,
            (min_pages, max_pages),
        ).fetchall()

        weights: dict[tuple[str, str], int] = {}
        via: dict[tuple[str, str], list[str]] = {}
        for ent in entities:
            page_ids = [
                str(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT page_id FROM mentions WHERE entity_id = ?", (ent["eid"],)
                ).fetchall()
            ]
            titles = sorted({page_id_to_title[pid] for pid in page_ids if pid in page_id_to_title})
            for i in range(len(titles)):
                for j in range(i + 1, len(titles)):
                    key = (titles[i], titles[j])
                    weights[key] = weights.get(key, 0) + 1
                    bucket = via.setdefault(key, [])
                    if len(bucket) < 3:
                        bucket.append(str(ent["name"]))
    finally:
        conn.close()

    edges = [
        SharedEdge(source=a, target=b, weight=w, via=tuple(via[(a, b)]))
        for (a, b), w in weights.items()
    ]
    edges.sort(key=lambda e: (-e.weight, e.source, e.target))
    return edges[:max_edges]
