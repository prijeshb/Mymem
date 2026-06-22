"""
Tests for the graph extraction hook in the ingest pipeline.

After ingest, a fire-and-forget background task extracts entities from the
source, resolves them against graph.db, and records mentions on the written
pages. Never blocks or fails the ingest itself.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mymem.graph.store import entities_for_page, find_entity, mentions_for_page
from mymem.pipeline.ingest import ingest_source
from mymem.pipeline.router import ModelRouter

SOURCE_TEXT = "An in-depth article about Alpha Systems and how it changed retrieval."

IDEAS = [
    {
        "title": "Concept Alpha",
        "summary": "Primary concept.",
        "tags": ["alpha"],
        "domain": "tech",
    }
]

PAGE_BODY = "# Concept Alpha\n\nBody about Alpha Systems.\n\n## See Also\n\n- [[Other]]"

ENTITIES = [
    {
        "name": "Alpha Systems",
        "type": "system",
        "description": "Retrieval platform",
        "span": "about Alpha Systems",
    }
]


def make_router() -> ModelRouter:
    async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
        if "extract named entities" in system:
            return json.dumps(ENTITIES)
        if "decide whether candidate names" in system:
            return "[]"
        if "json" in system.lower():
            return json.dumps(IDEAS)
        return PAGE_BODY

    return ModelRouter(llm_fn=fake_llm)


async def _drain_background() -> None:
    pending = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, Path]:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    src = tmp_path / "raw" / "article.md"
    src.parent.mkdir()
    src.write_text(SOURCE_TEXT, encoding="utf-8")
    return {
        "wiki_dir": wiki_dir,
        "index_path": wiki_dir / "index.md",
        "log_path": wiki_dir / "log.md",
        "db_path": data_dir / "mymem.db",
        "source": src,
    }


class TestGraphIngestHook:
    @pytest.mark.asyncio
    async def test_entities_recorded_after_ingest(self, env: dict[str, Path]) -> None:
        result = await ingest_source(
            str(env["source"]),
            wiki_dir=env["wiki_dir"],
            index_path=env["index_path"],
            log_path=env["log_path"],
            router=make_router(),
            db_path=env["db_path"],
        )
        await _drain_background()
        assert result.pages_written  # sanity: ingest produced a page

        graph_db = env["db_path"].parent / "graph.db"
        assert graph_db.exists()
        e = find_entity(graph_db, "Alpha Systems")
        assert e is not None and e.type == "system"
        # Mentions are anchored on the page's stable id (ADR-013/014), not its slug.
        from mymem.wiki.page import read_page
        page_id = read_page(env["wiki_dir"] / "concept-alpha.md").id
        ents = entities_for_page(graph_db, page_id)
        assert any(x.canonical == "Alpha Systems" for x in ents)
        ms = mentions_for_page(graph_db, page_id)
        assert ms and ms[0].span == "about Alpha Systems"

    @pytest.mark.asyncio
    async def test_no_db_path_skips_graph_quietly(self, env: dict[str, Path]) -> None:
        result = await ingest_source(
            str(env["source"]),
            wiki_dir=env["wiki_dir"],
            index_path=env["index_path"],
            log_path=env["log_path"],
            router=make_router(),
            db_path=None,
        )
        await _drain_background()
        assert result.pages_written
        assert not (env["db_path"].parent / "graph.db").exists()

    @pytest.mark.asyncio
    async def test_graph_failure_never_breaks_ingest(self, env: dict[str, Path]) -> None:
        async def hostile_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            if "extract named entities" in system:
                raise RuntimeError("entity model exploded")
            if "json" in system.lower():
                return json.dumps(IDEAS)
            return PAGE_BODY

        result = await ingest_source(
            str(env["source"]),
            wiki_dir=env["wiki_dir"],
            index_path=env["index_path"],
            log_path=env["log_path"],
            router=ModelRouter(llm_fn=hostile_llm),
            db_path=env["db_path"],
        )
        await _drain_background()
        assert result.pages_written  # ingest unaffected by graph failure
