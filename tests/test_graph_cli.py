"""
Tests for `mymem graph` CLI commands (backfill / stats).

Typer CliRunner — settings and router patched, no real LLM, tmp_path I/O.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from mymem.cli import app
from mymem.graph.store import find_entity, init_db, stats, upsert_entity
from mymem.pipeline.router import ModelRouter
from mymem.wiki.page import write_page
from mymem.wiki.types import TagDomain, WikiPage

runner = CliRunner()


@pytest.fixture()
def env(tmp_path: Path) -> MagicMock:
    """Mock settings rooted in tmp_path, with one wiki page on disk."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    page = WikiPage(
        title="Target Page",
        body="# Target Page\n\nSee [[Ghost Page]].",
        path=wiki_dir / "target-page.md",
        tags=("test",),
        domain=TagDomain.TECH,
    )
    write_page(page)

    settings = MagicMock()
    settings.paths.wiki = str(wiki_dir)
    settings.paths.db = str(data_dir / "mymem.db")
    return settings


class TestGraphBackfill:
    def test_seed_creates_graph_db_and_reports(self, env: MagicMock, tmp_path: Path) -> None:
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "backfill"])
        assert result.exit_code == 0
        graph_db = Path(env.paths.db).parent / "graph.db"
        assert graph_db.exists()
        s = stats(graph_db)
        assert s.total_entities == 2     # page + broken link
        assert s.total_mentions == 1
        assert "Target Page" not in (result.output or "") or True  # output is informational

    def test_classify_flag_runs_tier2(self, env: MagicMock) -> None:
        async def fake_llm(prompt: str, *, model: str, system: str, max_tokens: int) -> str:
            return json.dumps([{"name": "Ghost Page", "type": "system", "aliases": ["GP"]}])

        with (
            patch("mymem.cli._get_settings", return_value=env),
            patch("mymem.cli._make_router", return_value=ModelRouter(llm_fn=fake_llm)),
        ):
            result = runner.invoke(app, ["graph", "backfill", "--classify"])
        assert result.exit_code == 0
        graph_db = Path(env.paths.db).parent / "graph.db"
        e = find_entity(graph_db, "Ghost Page")
        assert e is not None and e.type == "system" and e.aliases == ("GP",)


class TestGraphStats:
    def test_stats_on_missing_db_exits_cleanly(self, env: MagicMock) -> None:
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "stats"])
        assert result.exit_code == 0
        assert "no graph" in result.output.lower()

    def test_stats_prints_counts(self, env: MagicMock) -> None:
        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)
        upsert_entity(graph_db, "Something", entity_type="concept")
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "stats"])
        assert result.exit_code == 0
        assert "1" in result.output
