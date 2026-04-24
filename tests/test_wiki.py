"""Tests for mymem.wiki — types, page I/O, index, log."""

from __future__ import annotations

import time
from datetime import date, datetime
from pathlib import Path

import pytest

from mymem.wiki.types import (
    IndexEntry, LogEntry, LogOperation, TagDomain, WikiPage,
)
from mymem.wiki.page import list_pages, read_page, slug_to_path, write_page
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog


# ---------------------------------------------------------------------------
# WikiPage
# ---------------------------------------------------------------------------

class TestWikiPage:
    def test_required_fields(self):
        page = WikiPage(title="Test", body="# Test", path=Path("wiki/test.md"))
        assert page.title == "Test"
        assert page.body == "# Test"

    def test_defaults(self):
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"))
        assert page.tags == ()
        assert page.sources == ()
        assert page.domain == TagDomain.MISC
        assert isinstance(page.created, date)

    def test_is_frozen(self):
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"))
        with pytest.raises((AttributeError, TypeError)):
            page.title = "X"  # type: ignore[misc]

    def test_wikilinks_extracted(self):
        page = WikiPage(
            title="T", body="See [[Alpha]] and [[Beta]].", path=Path("wiki/t.md")
        )
        links = page.wikilinks()
        assert "Alpha" in links
        assert "Beta" in links

    def test_wikilinks_empty(self):
        page = WikiPage(title="T", body="No links here.", path=Path("wiki/t.md"))
        assert page.wikilinks() == []

    def test_slug(self):
        page = WikiPage(title="Hello World", body="b", path=Path("wiki/hello-world.md"))
        assert page.slug == "hello-world"

    def test_with_updated(self):
        page = WikiPage(title="T", body="old", path=Path("wiki/t.md"))
        updated = page.with_updated(body="new")
        assert updated.body == "new"
        assert updated.title == "T"
        assert page.body == "old"  # original unchanged

    def test_tags_coerced_to_tuple(self):
        page = WikiPage(title="T", body="b", path=Path("t.md"), tags=["a", "b"])
        assert isinstance(page.tags, tuple)


# ---------------------------------------------------------------------------
# Page I/O
# ---------------------------------------------------------------------------

class TestPageIO:
    def test_write_and_read_roundtrip(self, tmp_path: Path):
        p = tmp_path / "page.md"
        page = WikiPage(
            title="Roundtrip Page",
            body="# Roundtrip\n\nSome content here.",
            path=p,
            tags=["test", "io"],
            sources=["source.md"],
            domain=TagDomain.TECH,
        )
        write_page(page)
        loaded = read_page(p)
        assert loaded.title == "Roundtrip Page"
        assert loaded.domain == TagDomain.TECH
        assert "test" in loaded.tags
        assert "source.md" in loaded.sources
        assert "Some content" in loaded.body

    def test_written_file_has_yaml_frontmatter(self, tmp_path: Path):
        p = tmp_path / "fm.md"
        write_page(WikiPage(title="FM Test", body="# FM", path=p, tags=["x"]))
        raw = p.read_text()
        assert raw.startswith("---")
        assert "title: FM Test" in raw
        assert "tags:" in raw

    def test_read_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            read_page(tmp_path / "nope.md")

    def test_list_pages_returns_all(self, tmp_path: Path):
        for i in range(3):
            write_page(WikiPage(title=f"Page {i}", body=f"# {i}", path=tmp_path / f"page-{i}.md"))
        pages = list_pages(tmp_path)
        assert len(pages) == 3

    def test_list_pages_empty_dir(self, tmp_path: Path):
        assert list_pages(tmp_path) == []

    def test_list_pages_skips_index_and_log(self, tmp_path: Path):
        write_page(WikiPage(title="Real", body="# Real\n\nContent.", path=tmp_path / "real.md"))
        (tmp_path / "index.md").write_text("# Index")
        (tmp_path / "log.md").write_text("## Log")
        pages = list_pages(tmp_path)
        assert len(pages) == 1
        assert pages[0].title == "Real"

    def test_write_creates_parent_dirs(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "page.md"
        write_page(WikiPage(title="Deep", body="# Deep", path=deep))
        assert deep.exists()

    def test_created_date_preserved_on_rewrite(self, tmp_path: Path):
        p = tmp_path / "p.md"
        original = WikiPage(
            title="P", body="# P\n\nOriginal.", path=p,
            created=date(2026, 1, 1),
        )
        write_page(original)
        loaded = read_page(p)
        assert loaded.created == date(2026, 1, 1)

        updated = loaded.with_updated(body="# P\n\nUpdated.")
        write_page(updated)
        reloaded = read_page(p)
        assert reloaded.created == date(2026, 1, 1)

    def test_slug_to_path(self, tmp_path: Path):
        path = slug_to_path(tmp_path, "Hello World")
        assert path == tmp_path / "hello-world.md"


# ---------------------------------------------------------------------------
# IndexManager
# ---------------------------------------------------------------------------

class TestIndexManager:
    def _entry(self, title: str, category: str = "concepts") -> IndexEntry:
        return IndexEntry(
            title=title,
            path=Path(f"wiki/{title.lower().replace(' ', '-')}.md"),
            summary=f"Summary of {title}",
            category=category,
            source_count=1,
        )

    def test_save_and_load(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        entries = [self._entry("Alpha"), self._entry("Beta")]
        mgr.save(entries)
        loaded = mgr.load()
        assert len(loaded) == 2
        titles = {e.title for e in loaded}
        assert "Alpha" in titles and "Beta" in titles

    def test_empty_save_creates_file(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([])
        assert (tmp_path / "index.md").exists()

    def test_upsert_adds_new(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([])
        mgr.upsert(self._entry("New"))
        assert len(mgr.load()) == 1

    def test_upsert_updates_existing(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([self._entry("Alpha")])
        updated = IndexEntry(
            title="Alpha",
            path=Path("wiki/alpha.md"),
            summary="Updated summary",
            category="concepts",
            source_count=5,
        )
        mgr.upsert(updated)
        loaded = mgr.load()
        assert len(loaded) == 1
        assert loaded[0].summary == "Updated summary"
        assert loaded[0].source_count == 5

    def test_remove(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([self._entry("Keep"), self._entry("Drop")])
        mgr.remove("Drop")
        loaded = mgr.load()
        assert len(loaded) == 1
        assert loaded[0].title == "Keep"

    def test_remove_nonexistent_is_noop(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([self._entry("A")])
        mgr.remove("Nonexistent")
        assert len(mgr.load()) == 1

    def test_grouped_by_category(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([
            self._entry("P1", "papers"),
            self._entry("C1", "concepts"),
            self._entry("P2", "papers"),
        ])
        raw = (tmp_path / "index.md").read_text()
        assert "Papers" in raw or "papers" in raw.lower()
        assert "Concepts" in raw or "concepts" in raw.lower()

    def test_search_returns_relevant(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([
            IndexEntry(title="Python Basics", path=Path("p.md"),
                       summary="Intro to Python programming", category="tech"),
            IndexEntry(title="Stoic Ethics", path=Path("s.md"),
                       summary="Stoicism philosophy overview", category="spiritual"),
        ])
        results = mgr.search("python programming")
        assert results[0].title == "Python Basics"

    def test_find_by_title(self, tmp_path: Path):
        mgr = IndexManager(tmp_path / "index.md")
        mgr.save([self._entry("Alpha")])
        assert mgr.find("Alpha") is not None
        assert mgr.find("Missing") is None


# ---------------------------------------------------------------------------
# WikiLog
# ---------------------------------------------------------------------------

class TestWikiLog:
    def test_append_and_load(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        entry = LogEntry(
            operation=LogOperation.INGEST,
            description="article.md",
            affected_pages=("page-a.md", "page-b.md"),
        )
        log.append(entry)
        entries = log.load()
        assert len(entries) == 1
        assert entries[0].operation == LogOperation.INGEST
        assert "article.md" in entries[0].description

    def test_append_is_cumulative(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        for i in range(5):
            log.append(LogEntry(operation=LogOperation.QUERY, description=f"q{i}"))
        assert len(log.load()) == 5

    def test_log_never_overwrites(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        log.append(LogEntry(operation=LogOperation.INGEST, description="first"))
        log.append(LogEntry(operation=LogOperation.LINT, description="second"))
        entries = log.load()
        assert entries[0].description == "first"
        assert entries[1].description == "second"

    def test_header_starts_with_bracket(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        log.append(LogEntry(operation=LogOperation.INGEST, description="test.md"))
        raw = (tmp_path / "log.md").read_text()
        headers = [l for l in raw.splitlines() if l.startswith("## [")]
        assert len(headers) == 1

    def test_recent_returns_last_n(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        for i in range(10):
            log.append(LogEntry(operation=LogOperation.QUERY, description=f"q{i}"))
        recent = log.recent(3)
        assert len(recent) == 3
        assert recent[-1].description == "q9"

    def test_load_empty_log(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        assert log.load() == []

    def test_by_operation_filters(self, tmp_path: Path):
        log = WikiLog(tmp_path / "log.md")
        log.append(LogEntry(operation=LogOperation.INGEST, description="i"))
        log.append(LogEntry(operation=LogOperation.QUERY, description="q"))
        log.append(LogEntry(operation=LogOperation.LINT, description="l"))
        ingests = log.by_operation(LogOperation.INGEST)
        assert len(ingests) == 1
        assert ingests[0].description == "i"
