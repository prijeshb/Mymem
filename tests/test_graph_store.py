"""
Tests for mymem/graph/store.py — entity/alias/mention repository (graph.db).

Pure SQLite — no LLM, no embedder. Target: 100% coverage (same standard as rag/store.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mymem.graph.store import (
    ENTITY_TYPES,
    GraphStats,
    add_alias,
    add_mention,
    delete_page,
    entities_for_page,
    find_entity,
    get_entity,
    init_db,
    list_entities,
    mentions_for_page,
    pages_for_entity,
    stats,
    upsert_entity,
)


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    p = tmp_path / "graph.db"
    init_db(p)
    return p


# ---------------------------------------------------------------------------
# upsert_entity
# ---------------------------------------------------------------------------

class TestUpsertEntity:
    def test_insert_returns_entity(self, db: Path) -> None:
        e = upsert_entity(db, "Sarah Chen", entity_type="person", description="Platform lead")
        assert e.id > 0
        assert e.canonical == "Sarah Chen"
        assert e.type == "person"
        assert e.description == "Platform lead"
        assert e.page_id is None

    def test_invalid_type_raises(self, db: Path) -> None:
        with pytest.raises(ValueError, match="type"):
            upsert_entity(db, "X", entity_type="alien")

    def test_empty_canonical_raises(self, db: Path) -> None:
        with pytest.raises(ValueError, match="canonical"):
            upsert_entity(db, "   ", entity_type="concept")

    def test_upsert_same_canonical_updates_not_duplicates(self, db: Path) -> None:
        first = upsert_entity(db, "RAG", entity_type="concept", description="old")
        second = upsert_entity(db, "RAG", entity_type="concept", description="newer text")
        assert first.id == second.id
        assert second.description == "newer text"
        assert stats(db).total_entities == 1

    def test_upsert_is_case_insensitive(self, db: Path) -> None:
        a = upsert_entity(db, "GraphRAG", entity_type="concept")
        b = upsert_entity(db, "graphrag", entity_type="concept")
        assert a.id == b.id

    def test_upsert_keeps_existing_description_when_new_is_empty(self, db: Path) -> None:
        upsert_entity(db, "RAG", entity_type="concept", description="kept")
        e = upsert_entity(db, "RAG", entity_type="concept", description="")
        assert e.description == "kept"

    def test_upsert_sets_page_id_when_provided(self, db: Path) -> None:
        upsert_entity(db, "RAG", entity_type="concept")
        e = upsert_entity(db, "RAG", entity_type="concept", page_id="rag")
        assert e.page_id == "rag"

    def test_upsert_keeps_page_id_when_not_provided(self, db: Path) -> None:
        upsert_entity(db, "RAG", entity_type="concept", page_id="rag")
        e = upsert_entity(db, "RAG", entity_type="concept")
        assert e.page_id == "rag"

    def test_canonical_whitespace_normalized(self, db: Path) -> None:
        e = upsert_entity(db, "  Sarah   Chen  ", entity_type="person")
        assert e.canonical == "Sarah Chen"


# ---------------------------------------------------------------------------
# get / find / list
# ---------------------------------------------------------------------------

class TestLookup:
    def test_get_entity_found(self, db: Path) -> None:
        e = upsert_entity(db, "MyMem", entity_type="project")
        got = get_entity(db, e.id)
        assert got is not None and got.canonical == "MyMem"

    def test_get_entity_missing_returns_none(self, db: Path) -> None:
        assert get_entity(db, 999) is None

    def test_find_by_canonical_case_insensitive(self, db: Path) -> None:
        upsert_entity(db, "MyMem", entity_type="project")
        found = find_entity(db, "mymem")
        assert found is not None and found.canonical == "MyMem"

    def test_find_by_alias(self, db: Path) -> None:
        e = upsert_entity(db, "Large Language Models", entity_type="concept")
        add_alias(db, e.id, "LLM")
        found = find_entity(db, "llm")
        assert found is not None and found.id == e.id

    def test_find_missing_returns_none(self, db: Path) -> None:
        assert find_entity(db, "ghost") is None

    def test_entity_includes_aliases(self, db: Path) -> None:
        e = upsert_entity(db, "Large Language Models", entity_type="concept")
        add_alias(db, e.id, "LLM")
        add_alias(db, e.id, "LLMs")
        got = get_entity(db, e.id)
        assert got is not None and set(got.aliases) == {"LLM", "LLMs"}

    def test_list_entities_all(self, db: Path) -> None:
        upsert_entity(db, "A", entity_type="person")
        upsert_entity(db, "B", entity_type="concept")
        assert len(list_entities(db)) == 2

    def test_list_entities_filter_by_type(self, db: Path) -> None:
        upsert_entity(db, "A", entity_type="person")
        upsert_entity(db, "B", entity_type="concept")
        only = list_entities(db, entity_type="person")
        assert [e.canonical for e in only] == ["A"]

    def test_list_entities_invalid_type_raises(self, db: Path) -> None:
        with pytest.raises(ValueError, match="type"):
            list_entities(db, entity_type="alien")


# ---------------------------------------------------------------------------
# aliases
# ---------------------------------------------------------------------------

class TestAliases:
    def test_add_alias_idempotent(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept")
        add_alias(db, e.id, "Retrieval-Augmented Generation")
        add_alias(db, e.id, "Retrieval-Augmented Generation")
        got = get_entity(db, e.id)
        assert got is not None and got.aliases == ("Retrieval-Augmented Generation",)

    def test_add_alias_missing_entity_raises(self, db: Path) -> None:
        with pytest.raises(ValueError, match="entity"):
            add_alias(db, 999, "X")

    def test_blank_alias_raises(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept")
        with pytest.raises(ValueError, match="alias"):
            add_alias(db, e.id, "  ")


# ---------------------------------------------------------------------------
# mentions
# ---------------------------------------------------------------------------

class TestMentions:
    def test_add_and_read_mentions_for_page(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept")
        add_mention(db, e.id, "intro-to-rag", span="RAG combines retrieval", source_id="src.md")
        ms = mentions_for_page(db, "intro-to-rag")
        assert len(ms) == 1
        assert ms[0].entity_id == e.id
        assert ms[0].span == "RAG combines retrieval"

    def test_add_mention_missing_entity_raises(self, db: Path) -> None:
        with pytest.raises(ValueError, match="entity"):
            add_mention(db, 999, "some-page")

    def test_entities_for_page(self, db: Path) -> None:
        a = upsert_entity(db, "RAG", entity_type="concept")
        b = upsert_entity(db, "Sarah Chen", entity_type="person")
        add_mention(db, a.id, "p1")
        add_mention(db, b.id, "p1")
        ents = entities_for_page(db, "p1")
        assert {e.canonical for e in ents} == {"RAG", "Sarah Chen"}

    def test_pages_for_entity_distinct(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept")
        add_mention(db, e.id, "p1")
        add_mention(db, e.id, "p1")  # second mention, same page
        add_mention(db, e.id, "p2")
        assert sorted(pages_for_entity(db, e.id)) == ["p1", "p2"]


# ---------------------------------------------------------------------------
# delete_page — cleanup + refcount pruning
# ---------------------------------------------------------------------------

class TestDeletePage:
    def test_removes_mentions(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept", page_id="rag")
        add_mention(db, e.id, "p1")
        removed = delete_page(db, "p1")
        assert removed == 1
        assert mentions_for_page(db, "p1") == []

    def test_prunes_orphan_entity(self, db: Path) -> None:
        # Entity with no page of its own and mentions only on the deleted page
        e = upsert_entity(db, "Ephemeral", entity_type="concept")
        add_mention(db, e.id, "p1")
        delete_page(db, "p1")
        assert get_entity(db, e.id) is None

    def test_keeps_entity_mentioned_elsewhere(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept")
        add_mention(db, e.id, "p1")
        add_mention(db, e.id, "p2")
        delete_page(db, "p1")
        assert get_entity(db, e.id) is not None

    def test_clears_page_id_but_keeps_entity_with_mentions(self, db: Path) -> None:
        # The deleted page IS the entity's own page; other pages still mention it
        e = upsert_entity(db, "RAG", entity_type="concept", page_id="rag")
        add_mention(db, e.id, "other-page")
        delete_page(db, "rag")
        got = get_entity(db, e.id)
        assert got is not None and got.page_id is None

    def test_prunes_own_page_entity_with_no_other_mentions(self, db: Path) -> None:
        e = upsert_entity(db, "RAG", entity_type="concept", page_id="rag")
        delete_page(db, "rag")
        assert get_entity(db, e.id) is None

    def test_orphan_aliases_removed_with_entity(self, db: Path) -> None:
        e = upsert_entity(db, "Ephemeral", entity_type="concept")
        add_alias(db, e.id, "Eph")
        add_mention(db, e.id, "p1")
        delete_page(db, "p1")
        assert find_entity(db, "Eph") is None

    def test_idempotent_on_unknown_page(self, db: Path) -> None:
        assert delete_page(db, "never-existed") == 0


# ---------------------------------------------------------------------------
# stats — explosion alarms
# ---------------------------------------------------------------------------

class TestStats:
    def test_empty_db(self, db: Path) -> None:
        s = stats(db)
        assert s == GraphStats(total_entities=0, total_mentions=0,
                               singleton_count=0, singleton_rate=0.0)

    def test_singleton_rate(self, db: Path) -> None:
        a = upsert_entity(db, "A", entity_type="concept")   # mentioned on 2 pages — not singleton
        b = upsert_entity(db, "B", entity_type="concept")   # 1 page — singleton
        c = upsert_entity(db, "C", entity_type="concept")   # 0 pages — singleton
        add_mention(db, a.id, "p1")
        add_mention(db, a.id, "p2")
        add_mention(db, b.id, "p1")
        s = stats(db)
        assert s.total_entities == 3
        assert s.total_mentions == 3
        assert s.singleton_count == 2
        assert s.singleton_rate == pytest.approx(2 / 3)
        assert c.id > 0  # silence unused warning


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def test_entity_types_closed_set() -> None:
    assert ENTITY_TYPES == ("person", "project", "system", "organization", "concept")


def test_init_db_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "graph.db"
    init_db(p)
    init_db(p)  # second call must not raise
    assert p.exists()


def test_entity_is_immutable(db: Path) -> None:
    e = upsert_entity(db, "RAG", entity_type="concept")
    with pytest.raises(AttributeError):
        e.canonical = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# update_entity_type / delete_mentions_by_source (backfill support)
# ---------------------------------------------------------------------------

class TestUpdateEntityType:
    def test_updates_type(self, db: Path) -> None:
        from mymem.graph.store import update_entity_type
        e = upsert_entity(db, "Sarah Chen", entity_type="concept")
        update_entity_type(db, e.id, "person")
        got = get_entity(db, e.id)
        assert got is not None and got.type == "person"

    def test_invalid_type_raises(self, db: Path) -> None:
        from mymem.graph.store import update_entity_type
        e = upsert_entity(db, "X", entity_type="concept")
        with pytest.raises(ValueError, match="type"):
            update_entity_type(db, e.id, "alien")

    def test_missing_entity_raises(self, db: Path) -> None:
        from mymem.graph.store import update_entity_type
        with pytest.raises(ValueError, match="entity"):
            update_entity_type(db, 999, "person")


class TestDeleteMentionsBySource:
    def test_deletes_only_matching_sources(self, db: Path) -> None:
        from mymem.graph.store import delete_mentions_by_source
        e = upsert_entity(db, "RAG", entity_type="concept")
        add_mention(db, e.id, "p1", source_id="tier1-wikilink")
        add_mention(db, e.id, "p1", source_id="ingest")
        removed = delete_mentions_by_source(db, ("tier1-wikilink", "tier1-broken-link"))
        assert removed == 1
        remaining = mentions_for_page(db, "p1")
        assert len(remaining) == 1 and remaining[0].source_id == "ingest"

    def test_empty_sources_is_noop(self, db: Path) -> None:
        from mymem.graph.store import delete_mentions_by_source
        assert delete_mentions_by_source(db, ()) == 0


# ---------------------------------------------------------------------------
# init_db migration — legacy page_slug → page_id rename (ADR-014 D4)
# ---------------------------------------------------------------------------

_LEGACY_SCHEMA = """
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical   TEXT NOT NULL,
    type        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    page_slug   TEXT,
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_entities_canonical ON entities(canonical COLLATE NOCASE);
CREATE TABLE aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    alias     TEXT NOT NULL,
    UNIQUE(entity_id, alias)
);
CREATE TABLE mentions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    page_slug TEXT NOT NULL,
    span      TEXT NOT NULL DEFAULT '',
    source_id TEXT NOT NULL DEFAULT '',
    created   TEXT NOT NULL
);
CREATE INDEX idx_mentions_page   ON mentions(page_slug);
CREATE INDEX idx_mentions_entity ON mentions(entity_id);
"""


class TestLegacyMigration:
    def _make_legacy_db(self, path: Path) -> None:
        import sqlite3
        conn = sqlite3.connect(path)
        try:
            with conn:
                conn.executescript(_LEGACY_SCHEMA)
                conn.execute(
                    "INSERT INTO entities (canonical, type, description, page_slug,"
                    " created, updated) VALUES ('RAG','concept','', 'rag', 't', 't')"
                )
                conn.execute(
                    "INSERT INTO mentions (entity_id, page_slug, span, source_id, created)"
                    " VALUES (1, 'src-page', '', 'ingest', 't')"
                )
        finally:
            conn.close()

    def test_init_db_renames_page_slug_to_page_id_preserving_data(self, tmp_path: Path) -> None:
        p = tmp_path / "legacy.db"
        self._make_legacy_db(p)

        init_db(p)  # should migrate page_slug → page_id

        # Old column gone, new column present, values preserved.
        e = find_entity(p, "RAG")
        assert e is not None and e.page_id == "rag"
        ms = mentions_for_page(p, "src-page")
        assert len(ms) == 1 and ms[0].source_id == "ingest"

    def test_init_db_migration_is_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "legacy.db"
        self._make_legacy_db(p)
        init_db(p)
        init_db(p)  # second run must be a no-op, not error
        e = find_entity(p, "RAG")
        assert e is not None and e.page_id == "rag"

    def test_fresh_db_has_page_id_and_no_page_slug(self, tmp_path: Path) -> None:
        import sqlite3
        p = tmp_path / "fresh.db"
        init_db(p)
        conn = sqlite3.connect(p)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(mentions)").fetchall()}
        finally:
            conn.close()
        assert "page_id" in cols and "page_slug" not in cols
