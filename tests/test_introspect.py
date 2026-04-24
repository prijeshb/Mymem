"""Tests for mymem.pipeline.introspect — mocked LLM, real SQLite."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from mymem.pipeline.introspect import (
    IntrospectResult, introspect, log_curiosity_event, top_interests,
)
from mymem.pipeline.router import ModelRouter
from mymem.wiki.log import WikiLog
from mymem.wiki.page import write_page
from mymem.wiki.types import LogEntry, LogOperation, TagDomain, WikiPage
from mymem.wiki.index import IndexManager
from mymem.wiki.types import IndexEntry
from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_SUMMARY = "Today you explored several interesting topics."
FAKE_SUGGESTION = "- [[Page A]] — highly relevant to your query\n- [[Page B]] — related concepts"


def make_router(response: str = FAKE_SUMMARY) -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return response
    return ModelRouter(llm_fn=fake_llm)


def setup_wiki(wiki_dir: Path, index_path: Path) -> None:
    body = "# Page A\n\n" + "Content sentence. " * 10
    write_page(WikiPage(
        title="Page A", body=body, path=wiki_dir / "page-a.md",
        tags=["ml", "tech"], domain=TagDomain.TECH,
    ))
    IndexManager(index_path).save([
        IndexEntry(title="Page A", path=Path("page-a.md"),
                   summary="ML content", category="tech", domain=TagDomain.TECH),
    ])


# ---------------------------------------------------------------------------
# Curiosity DB
# ---------------------------------------------------------------------------

class TestCuriosityDB:
    def test_log_event_creates_db(self, tmp_path: Path):
        db = tmp_path / "curiosity.db"
        log_curiosity_event(db, "ingest", TagDomain.TECH, ["ml", "python"])
        assert db.exists()

    def test_top_interests_returns_list(self, tmp_path: Path):
        db = tmp_path / "curiosity.db"
        log_curiosity_event(db, "ingest", TagDomain.TECH, ["ml"])
        log_curiosity_event(db, "query", TagDomain.SPIRITUAL, ["meditation"])
        interests = top_interests(db)
        assert len(interests) >= 2

    def test_top_interests_sorted_by_weight(self, tmp_path: Path):
        db = tmp_path / "curiosity.db"
        # Log tech/ml 3 times, spiritual/stoicism once
        for _ in range(3):
            log_curiosity_event(db, "ingest", TagDomain.TECH, ["ml"])
        log_curiosity_event(db, "ingest", TagDomain.SPIRITUAL, ["stoicism"])
        interests = top_interests(db)
        top = interests[0]
        assert top["tag"] == "ml"
        assert top["weight"] > 1.0

    def test_empty_db_returns_empty_list(self, tmp_path: Path):
        assert top_interests(tmp_path / "nonexistent.db") == []

    def test_multiple_tags_per_event(self, tmp_path: Path):
        db = tmp_path / "curiosity.db"
        log_curiosity_event(db, "ingest", TagDomain.TECH, ["ml", "python", "systems"])
        interests = top_interests(db)
        tags = {i["tag"] for i in interests}
        assert "ml" in tags
        assert "python" in tags
        assert "systems" in tags


# ---------------------------------------------------------------------------
# Daily summary mode
# ---------------------------------------------------------------------------

class TestDailySummary:
    @pytest.mark.asyncio
    async def test_returns_result(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            save=False,
        )

        assert isinstance(result, IntrospectResult)
        assert isinstance(result.summary, str)

    @pytest.mark.asyncio
    async def test_no_activity_summary(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            save=False,
        )

        assert "No activity" in result.summary

    @pytest.mark.asyncio
    async def test_save_creates_daily_page(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            save=True,
        )

        assert result.saved_to is not None
        assert Path(result.saved_to).exists()

    @pytest.mark.asyncio
    async def test_daily_page_in_daily_folder(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            save=True,
        )

        assert result.saved_to is not None
        assert "daily" in result.saved_to

    @pytest.mark.asyncio
    async def test_introspect_logged(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        log_path = wiki_dir / "log.md"

        await introspect(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=log_path,
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            save=False,
        )

        log = WikiLog(log_path)
        ops = log.by_operation(LogOperation.INTROSPECT)
        assert len(ops) == 1


# ---------------------------------------------------------------------------
# Research suggestion mode
# ---------------------------------------------------------------------------

class TestResearchSuggestion:
    @pytest.mark.asyncio
    async def test_topic_mode_returns_suggestions(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(response=FAKE_SUGGESTION),
            topic="machine learning",
            save=False,
        )

        assert "Page A" in result.summary or len(result.summary) > 0

    @pytest.mark.asyncio
    async def test_topic_mode_no_save(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        result = await introspect(
            wiki_dir=wiki_dir,
            index_path=wiki_dir / "index.md",
            log_path=wiki_dir / "log.md",
            curiosity_db=tmp_path / "curiosity.db",
            router=make_router(),
            topic="anything",
            save=True,  # save is ignored in topic mode
        )

        # topic mode does not save a daily file
        assert result.saved_to is None
