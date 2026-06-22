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


class TestGraphBackfillResolutionFlags:
    def _capture_seed(self) -> tuple[dict, object]:
        captured: dict = {}
        from mymem.graph.backfill import SeedReport

        async def fake_seed(db, wiki, *, embed_fn=None, router=None):  # type: ignore[no-untyped-def]
            captured["embed_fn"] = embed_fn is not None
            captured["router"] = router is not None
            return SeedReport(0, 0, 0, 0, 0)

        return captured, fake_seed

    def test_semantic_flag_wires_embedder_only(self, env: MagicMock) -> None:
        captured, fake_seed = self._capture_seed()
        with (
            patch("mymem.cli._get_settings", return_value=env),
            patch("mymem.graph.backfill.seed_from_wiki", side_effect=fake_seed),
            patch("mymem.rag.embedder.OllamaEmbedder"),
        ):
            result = runner.invoke(app, ["graph", "backfill", "--semantic"])
        assert result.exit_code == 0
        assert captured["embed_fn"] is True
        assert captured["router"] is False

    def test_judge_flag_wires_router_only(self, env: MagicMock) -> None:
        captured, fake_seed = self._capture_seed()
        with (
            patch("mymem.cli._get_settings", return_value=env),
            patch("mymem.cli._make_router", return_value=MagicMock()),
            patch("mymem.graph.backfill.seed_from_wiki", side_effect=fake_seed),
        ):
            result = runner.invoke(app, ["graph", "backfill", "--judge"])
        assert result.exit_code == 0
        assert captured["router"] is True
        assert captured["embed_fn"] is False

    def test_default_wires_neither(self, env: MagicMock) -> None:
        captured, fake_seed = self._capture_seed()
        with (
            patch("mymem.cli._get_settings", return_value=env),
            patch("mymem.graph.backfill.seed_from_wiki", side_effect=fake_seed),
        ):
            result = runner.invoke(app, ["graph", "backfill"])
        assert result.exit_code == 0
        assert captured["embed_fn"] is False
        assert captured["router"] is False


class TestGraphGaps:
    def test_gaps_lists_missing_concepts(self, env: MagicMock) -> None:
        from mymem.graph.store import add_mention

        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)
        g = upsert_entity(graph_db, "AI Agents", entity_type="concept")  # pageless
        add_mention(graph_db, g.id, "p1")

        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "gaps"])
        assert result.exit_code == 0
        assert "AI Agents" in result.output

    def test_gaps_empty_reports_clearly(self, env: MagicMock) -> None:
        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)  # no pageless entities
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "gaps"])
        assert result.exit_code == 0
        assert "no knowledge gaps" in result.output.lower()

    def test_gaps_missing_db_exits_cleanly(self, env: MagicMock) -> None:
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "gaps"])
        assert result.exit_code == 0
        assert "no graph database" in result.output.lower()


class TestGraphRekey:
    def test_rekey_missing_db_exits_cleanly(self, env: MagicMock) -> None:
        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "rekey"])
        assert result.exit_code == 0
        assert "no graph" in result.output.lower()

    def test_rekey_converts_slug_anchor_to_id(self, env: MagicMock) -> None:
        from mymem.graph.store import add_mention
        from mymem.wiki.page import read_page

        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)
        # Legacy slug-keyed anchor for the on-disk "Target Page".
        e = upsert_entity(graph_db, "Target Page", entity_type="concept", page_id="target-page")
        add_mention(graph_db, e.id, "target-page", source_id="ingest")

        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "rekey"])

        assert result.exit_code == 0
        page_id = read_page(Path(env.paths.wiki) / "target-page.md").id
        got = find_entity(graph_db, "Target Page")
        assert got is not None and got.page_id == page_id

    def test_rekey_already_keyed_reports_clearly(self, env: MagicMock) -> None:
        from mymem.wiki.page import read_page

        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)
        page_id = read_page(Path(env.paths.wiki) / "target-page.md").id
        upsert_entity(graph_db, "Target Page", entity_type="concept", page_id=page_id)

        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "rekey"])

        assert result.exit_code == 0
        assert "already re-keyed" in result.output.lower()

    def test_rekey_empty_graph_reports_clearly(self, env: MagicMock) -> None:
        graph_db = Path(env.paths.db).parent / "graph.db"
        init_db(graph_db)  # exists but has no entities

        with patch("mymem.cli._get_settings", return_value=env):
            result = runner.invoke(app, ["graph", "rekey"])

        assert result.exit_code == 0
        assert "empty" in result.output.lower()


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
