"""Tests for shared-concept UI graph edges (mymem/graph/ui_edges.py)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mymem.graph.ui_edges import shared_entity_edges


def _build_graph(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE entities (id INTEGER PRIMARY KEY, canonical TEXT, page_id TEXT);
        CREATE TABLE mentions (entity_id INTEGER, page_id TEXT);

        -- DRAM: shared by p1,p2,p3  (in range)
        INSERT INTO entities (id, canonical, page_id) VALUES (1, 'DRAM', NULL);
        INSERT INTO mentions (entity_id, page_id) VALUES (1,'p1'),(1,'p2'),(1,'p3');

        -- HBM: shared by p1,p2  (in range) -> adds weight to the p1-p2 pair
        INSERT INTO entities (id, canonical, page_id) VALUES (2, 'HBM', NULL);
        INSERT INTO mentions (entity_id, page_id) VALUES (2,'p1'),(2,'p2');

        -- Generic: shared by 9 pages (> max_pages) -> skipped as a hairball hub
        INSERT INTO entities (id, canonical, page_id) VALUES (3, 'Generic', NULL);
        INSERT INTO mentions (entity_id, page_id) VALUES
            (3,'p1'),(3,'p2'),(3,'p3'),(3,'p4'),(3,'p5'),(3,'p6'),(3,'p7'),(3,'p8'),(3,'p9');
        """
    )
    conn.commit()
    conn.close()


_TITLES = {"p1": "Page One", "p2": "Page Two", "p3": "Page Three"}


def test_missing_db_returns_empty(tmp_path: Path) -> None:
    assert shared_entity_edges(tmp_path / "nope.db", _TITLES) == []


def test_shared_edges_weighted_and_hub_skipped(tmp_path: Path) -> None:
    db = tmp_path / "graph.db"
    _build_graph(db)

    edges = shared_entity_edges(db, _TITLES, min_pages=2, max_pages=6)

    # p1-p2 share DRAM + HBM (weight 2); p1-p3 and p2-p3 share only DRAM (weight 1).
    by_pair = {(e.source, e.target): e for e in edges}
    assert by_pair[("Page One", "Page Two")].weight == 2
    assert set(by_pair[("Page One", "Page Two")].via) == {"DRAM", "HBM"}
    assert by_pair[("Page One", "Page Three")].weight == 1
    assert by_pair[("Page Three", "Page Two")].weight == 1 or ("Page Two", "Page Three") in by_pair

    # Strongest pair sorts first; the 9-page "Generic" hub created no edges.
    assert edges[0].weight == 2
    assert all(e.weight <= 2 for e in edges)
    assert len(edges) == 3  # only the 3 distinct pairs among p1/p2/p3


def test_max_pages_excludes_hub(tmp_path: Path) -> None:
    db = tmp_path / "graph.db"
    _build_graph(db)
    # Raise max_pages to include the 9-page hub -> many more edges appear.
    with_hub = shared_entity_edges(db, {f"p{i}": f"Page {i}" for i in range(1, 10)},
                                   min_pages=2, max_pages=9)
    assert len(with_hub) > 3  # the hub now connects p1..p9


def test_unmapped_page_ids_are_dropped(tmp_path: Path) -> None:
    db = tmp_path / "graph.db"
    _build_graph(db)
    # Only p1/p2 mapped -> DRAM(p1,p2,p3) and HBM(p1,p2) collapse to a single p1-p2 edge.
    edges = shared_entity_edges(db, {"p1": "One", "p2": "Two"}, min_pages=2, max_pages=6)
    assert len(edges) == 1
    edge = edges[0]
    assert (edge.source, edge.target, edge.weight) == ("One", "Two", 2)
    assert set(edge.via) == {"DRAM", "HBM"}  # order is by concept frequency, not significant


def test_max_edges_cap(tmp_path: Path) -> None:
    db = tmp_path / "graph.db"
    _build_graph(db)
    edges = shared_entity_edges(db, _TITLES, min_pages=2, max_pages=6, max_edges=1)
    assert len(edges) == 1
    assert edges[0].weight == 2  # the cap keeps the strongest edge
