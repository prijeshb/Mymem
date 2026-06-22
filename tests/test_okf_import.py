"""
Tests for OKF import + export→import round-trip (ADR-016, PRD G4 lossless gate).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from mymem.cli import app
from mymem.knowledge.okf.exporter import export_okf
from mymem.knowledge.okf.importer import import_okf
from mymem.wiki.page import list_pages, read_page, write_page
from mymem.wiki.types import TagDomain, WikiPage

runner = CliRunner()


def _wiki(tmp_path: Path, name: str = "wiki") -> Path:
    wiki = tmp_path / name
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


class TestImportOkf:
    def test_imports_concepts_as_pages(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), bundle)
        dst = tmp_path / "dst"
        report = import_okf(bundle, dst)
        assert report.concepts == 2
        assert report.written == 2
        titles = {p.title for p in list_pages(dst)}
        assert titles == {"Self Attention", "Cross Attention"}

    def test_restores_wikilinks(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), bundle)
        dst = tmp_path / "dst"
        import_okf(bundle, dst)
        page = read_page(dst / "self-attention.md")
        assert "[[Cross Attention]]" in page.body

    def test_skips_reserved_and_non_concept_files(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "index.md").write_text("# Wiki\n", encoding="utf-8")        # reserved
        (bundle / "log.md").write_text("# Log\n", encoding="utf-8")           # reserved
        (bundle / "notes.md").write_text("no frontmatter", encoding="utf-8")  # not a concept
        (bundle / "good.md").write_text("---\ntype: tech\ntitle: Good\n---\n\nx", encoding="utf-8")
        report = import_okf(bundle, tmp_path / "dst")
        assert report.concepts == 1     # only good.md
        assert report.written == 1
        assert report.skipped == 1      # notes.md (reserved files not counted)

    def test_malformed_frontmatter_skipped(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "bad.md").write_text("---\nfoo: [unclosed\n---\n\nbody", encoding="utf-8")
        report = import_okf(bundle, tmp_path / "dst")
        assert report.written == 0 and report.skipped == 1

    def test_skips_existing_unless_overwrite(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), bundle)
        dst = _wiki(tmp_path, "dst")  # already has both pages
        skip_report = import_okf(bundle, dst)
        assert skip_report.written == 0 and skip_report.skipped == 2
        ow_report = import_okf(bundle, dst, overwrite=True)
        assert ow_report.written == 2


class TestRoundTrip:
    def test_export_import_preserves_identity_and_links(self, tmp_path: Path) -> None:
        src = _wiki(tmp_path, "src")
        bundle = tmp_path / "bundle"
        export_okf(src, bundle)
        dst = tmp_path / "dst"
        import_okf(bundle, dst)

        src_pages = {p.title: p for p in list_pages(src)}
        dst_pages = {p.title: p for p in list_pages(dst)}
        assert set(src_pages) == set(dst_pages)
        for title, sp in src_pages.items():
            dp = dst_pages[title]
            assert dp.id == sp.id                              # identity stable (G4)
            assert dp.domain == sp.domain
            assert list(dp.tags) == list(sp.tags)
            assert list(dp.sources) == list(sp.sources)
            assert dp.created == sp.created
            assert set(dp.wikilinks()) == set(sp.wikilinks())  # links restored


class TestImportCli:
    def test_import_okf_command(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle"
        export_okf(_wiki(tmp_path), bundle)
        dst = tmp_path / "dst"
        settings = MagicMock()
        settings.paths.wiki = str(dst)
        settings.paths.db = str(tmp_path / "data" / "mymem.db")
        with patch("mymem.cli._get_settings", return_value=settings):
            result = runner.invoke(app, ["import", "okf", str(bundle)])
        assert result.exit_code == 0, result.output
        assert "OKF Import" in result.output
        assert (dst / "self-attention.md").exists()

    def test_import_missing_dir_errors(self, tmp_path: Path) -> None:
        settings = MagicMock()
        settings.paths.wiki = str(tmp_path / "dst")
        settings.paths.db = str(tmp_path / "data" / "mymem.db")
        with patch("mymem.cli._get_settings", return_value=settings):
            result = runner.invoke(app, ["import", "okf", str(tmp_path / "nope")])
        assert result.exit_code == 1
