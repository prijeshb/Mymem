"""
Tests for OKF export — exporter, conformance, and the `mymem export okf` CLI (ADR-016).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mymem.cli import app
from mymem.knowledge.okf.conformance import check_bundle
from mymem.knowledge.okf.exporter import export_okf
from mymem.wiki.page import write_page
from mymem.wiki.types import TagDomain, WikiPage

runner = CliRunner()


def _wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    write_page(WikiPage(
        title="Self Attention",
        body="# Self Attention\n\nA mechanism. See [[Cross Attention]].",
        path=wiki / "self-attention.md",
        tags=("ml",), domain=TagDomain.TECH, sources=["paper.md"],
    ))
    write_page(WikiPage(
        title="Cross Attention",
        body="# Cross Attention\n\nRelated. See [[Self Attention]] and [[Ghost Concept]].",
        path=wiki / "cross-attention.md",
        tags=("ml",), domain=TagDomain.TECH,
    ))
    return wiki


class TestExportOkf:
    def test_produces_conformant_bundle(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        report = export_okf(_wiki(tmp_path), out)
        assert report.pages == 2
        assert report.conformant is True
        assert check_bundle(out).conformant is True

    def test_concept_files_have_type_and_converted_links(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), out)
        sa = (out / "self-attention.md").read_text(encoding="utf-8")
        assert "type: tech" in sa
        assert "[Cross Attention](/cross-attention.md)" in sa  # resolved wikilink

    def test_broken_links_emitted_and_counted(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        report = export_okf(_wiki(tmp_path), out)
        ca = (out / "cross-attention.md").read_text(encoding="utf-8")
        assert "[Ghost Concept](/ghost-concept.md)" in ca  # tolerant broken link
        assert report.links_broken == 1
        assert report.links_resolved == 2

    def test_index_has_no_frontmatter_and_log_exists(self, tmp_path: Path) -> None:
        out = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), out)
        index = (out / "index.md").read_text(encoding="utf-8")
        assert not index.startswith("---")          # OKF index has no frontmatter
        assert "[Self Attention](/self-attention.md)" in index
        log = (out / "log.md").read_text(encoding="utf-8")
        assert "**Exported**" in log

    def test_empty_wiki_exports_empty_conformant_bundle(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        report = export_okf(wiki, tmp_path / "bundle")
        assert report.pages == 0
        assert report.conformant is True


class TestConformance:
    def test_flags_missing_type(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("---\ntitle: X\n---\n\nbody", encoding="utf-8")
        report = check_bundle(tmp_path)
        assert report.conformant is False
        assert "bad.md" in report.violations

    def test_no_frontmatter_is_a_violation(self, tmp_path: Path) -> None:
        (tmp_path / "plain.md").write_text("just text", encoding="utf-8")
        assert check_bundle(tmp_path).conformant is False

    def test_malformed_frontmatter_is_a_violation(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("---\nfoo: [unclosed\n---\n\nbody", encoding="utf-8")
        assert check_bundle(tmp_path).conformant is False

    def test_reserved_files_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "index.md").write_text("# Wiki\n\n- [a](/a.md)", encoding="utf-8")
        (tmp_path / "log.md").write_text("# Log\n\n## 2026-01-01\n**X**: y", encoding="utf-8")
        (tmp_path / "good.md").write_text("---\ntype: tech\n---\n\nbody", encoding="utf-8")
        report = check_bundle(tmp_path)
        assert report.conformant is True
        assert report.total == 1  # only good.md counted


class TestExportCli:
    def test_export_okf_command(self, tmp_path: Path) -> None:
        wiki = _wiki(tmp_path)
        settings = MagicMock()
        settings.paths.wiki = str(wiki)
        settings.paths.db = str(tmp_path / "data" / "mymem.db")
        out = tmp_path / "bundle"
        with patch("mymem.cli._get_settings", return_value=settings):
            result = runner.invoke(app, ["export", "okf", str(out)])
        assert result.exit_code == 0, result.output
        assert "OKF Export" in result.output
        assert (out / "self-attention.md").exists()
        assert check_bundle(out).conformant is True
