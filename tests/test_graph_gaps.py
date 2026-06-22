"""
Tests for mymem/graph/gaps.py — knowledge-gap ranking (pageless referenced concepts).

Pure SQLite — no LLM, no embedder. Target: 100% coverage.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.graph.gaps import Gap, gap_count, knowledge_gaps
from mymem.graph.store import add_mention, init_db, upsert_entity


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "graph.db"
    init_db(p)
    return p


class TestKnowledgeGaps:
    def test_ranks_pageless_concepts_by_distinct_pages(self, db: Path) -> None:
        g1 = upsert_entity(db, "AI Agents", entity_type="concept")        # pageless
        g2 = upsert_entity(db, "Microservices", entity_type="concept")    # pageless
        # AI Agents referenced from 3 distinct pages, Microservices from 1
        for pid in ("p1", "p2", "p3"):
            add_mention(db, g1.id, pid, source_id="tier1-broken-link")
        add_mention(db, g2.id, "p1", source_id="tier1-broken-link")

        gaps = knowledge_gaps(db)
        assert [(g.concept, g.inbound_refs) for g in gaps] == [
            ("AI Agents", 3),
            ("Microservices", 1),
        ]
        assert gap_count(db) == 2

    def test_distinct_pages_not_raw_mentions(self, db: Path) -> None:
        g = upsert_entity(db, "AI Agents", entity_type="concept")
        # same page mentioned twice → still 1 distinct inbound ref
        add_mention(db, g.id, "p1")
        add_mention(db, g.id, "p1")
        gaps = knowledge_gaps(db)
        assert gaps[0].inbound_refs == 1

    def test_page_bearing_entity_is_not_a_gap(self, db: Path) -> None:
        real = upsert_entity(db, "Written", entity_type="concept", page_id="01PAGEID")
        add_mention(db, real.id, "p1")
        assert knowledge_gaps(db) == []
        assert gap_count(db) == 0

    def test_pageless_without_mentions_is_not_a_gap(self, db: Path) -> None:
        upsert_entity(db, "Orphan", entity_type="concept")  # pageless, unreferenced
        assert knowledge_gaps(db) == []
        assert gap_count(db) == 0

    def test_tie_broken_by_concept_name(self, db: Path) -> None:
        gb = upsert_entity(db, "Beta", entity_type="concept")
        ga = upsert_entity(db, "Alpha", entity_type="concept")
        add_mention(db, gb.id, "p1")
        add_mention(db, ga.id, "p1")
        gaps = knowledge_gaps(db)
        assert [g.concept for g in gaps] == ["Alpha", "Beta"]  # equal refs → name asc

    def test_limit_caps_results(self, db: Path) -> None:
        for name in ("A", "B", "C"):
            e = upsert_entity(db, name, entity_type="concept")
            add_mention(db, e.id, "p1")
        assert len(knowledge_gaps(db, limit=2)) == 2
        assert gap_count(db) == 3  # count ignores limit

    def test_sample_page_ids_capped(self, db: Path) -> None:
        g = upsert_entity(db, "AI Agents", entity_type="concept")
        for pid in ("p1", "p2", "p3", "p4", "p5"):
            add_mention(db, g.id, pid)
        gap = knowledge_gaps(db, sample=2)[0]
        assert len(gap.sample_page_ids) == 2
        assert isinstance(gap, Gap)

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.db"
        assert knowledge_gaps(missing) == []
        assert gap_count(missing) == 0
