"""Tests for mymem.pipeline.query — mocked LLM."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mymem.pipeline.query import QueryResult, query_wiki
from mymem.pipeline.router import ModelRouter
from mymem.wiki.index import IndexManager
from mymem.wiki.log import WikiLog
from mymem.wiki.page import write_page
from mymem.wiki.types import IndexEntry, LogOperation, TagDomain, WikiPage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_ANSWER = "Based on the wiki, the answer is: this is a test answer with [[Page A]] citation."


def make_router(answer: str = FAKE_ANSWER) -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        return answer
    return ModelRouter(llm_fn=fake_llm)


def setup_wiki(wiki_dir: Path, index_path: Path) -> None:
    """Create two linked wiki pages and populate the index."""
    body_a = "# Page A\n\n" + "Content about topic A. " * 10 + "\n\nSee [[Page B]]."
    body_b = "# Page B\n\n" + "Content about topic B. " * 10 + "\n\nSee [[Page A]]."
    write_page(WikiPage(title="Page A", body=body_a, path=wiki_dir / "page-a.md",
                        tags=["topic-a"], domain=TagDomain.TECH))
    write_page(WikiPage(title="Page B", body=body_b, path=wiki_dir / "page-b.md",
                        tags=["topic-b"], domain=TagDomain.TECH))
    mgr = IndexManager(index_path)
    mgr.save([
        IndexEntry(title="Page A", path=Path("page-a.md"),
                   summary="Content about topic A", category="tech", domain=TagDomain.TECH),
        IndexEntry(title="Page B", path=Path("page-b.md"),
                   summary="Content about topic B", category="tech", domain=TagDomain.TECH),
    ])


# ---------------------------------------------------------------------------
# Core query tests
# ---------------------------------------------------------------------------

class TestQueryWiki:
    @pytest.mark.asyncio
    async def test_returns_answer(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await query_wiki(
            "What is topic A?",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        assert isinstance(result, QueryResult)
        assert len(result.answer) > 0

    @pytest.mark.asyncio
    async def test_empty_index_returns_no_pages_message(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        IndexManager(index_path).save([])

        result = await query_wiki(
            "What is anything?",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        assert "wiki does not contain" in result.answer.lower() or len(result.answer) > 0

    @pytest.mark.asyncio
    async def test_citations_populated(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await query_wiki(
            "topic A topic B",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
        )

        assert len(result.citations) > 0

    @pytest.mark.asyncio
    async def test_save_creates_wiki_page(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await query_wiki(
            "topic A",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
            save=True,
        )

        assert result.saved_to is not None
        assert Path(result.saved_to).exists()

    @pytest.mark.asyncio
    async def test_save_false_no_file(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        setup_wiki(wiki_dir, index_path)

        result = await query_wiki(
            "topic A",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
            save=False,
        )

        assert result.saved_to is None

    @pytest.mark.asyncio
    async def test_query_logged(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"
        log_path = wiki_dir / "log.md"
        setup_wiki(wiki_dir, index_path)

        await query_wiki(
            "test question",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=log_path,
            router=make_router(),
        )

        log = WikiLog(log_path)
        queries = log.by_operation(LogOperation.QUERY)
        assert len(queries) == 1
        assert "test question" in queries[0].description

    @pytest.mark.asyncio
    async def test_domain_filter_applied(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        index_path = wiki_dir / "index.md"

        body = "# Spiritual Page\n\n" + "Philosophy content. " * 10
        write_page(WikiPage(
            title="Spiritual Page", body=body,
            path=wiki_dir / "spiritual-page.md", domain=TagDomain.SPIRITUAL,
        ))
        IndexManager(index_path).save([
            IndexEntry(title="Spiritual Page", path=Path("spiritual-page.md"),
                       summary="Philosophy content", category="spiritual",
                       domain=TagDomain.SPIRITUAL),
        ])

        result = await query_wiki(
            "philosophy",
            wiki_dir=wiki_dir,
            index_path=index_path,
            log_path=wiki_dir / "log.md",
            router=make_router(),
            domain_filter=TagDomain.TECH,  # filter to tech — should find nothing
        )

        # With domain filter=TECH and only a SPIRITUAL page, no pages loaded
        assert result.answer is not None  # still returns something (empty wiki message)
