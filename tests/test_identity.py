"""Tests for stable page identity — ADR-013 (id vs slug vs title).

Covers: mint_id(), WikiPage.id field + frontmatter I/O (auto-mint on write,
load on read, stability across rewrites), the title|slug → id resolution index,
and the idempotent backfill facade.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from mymem.wiki.identity import (
    BackfillReport,
    backfill_page_ids,
    build_page_id_index,
    resolve_to_id,
)
from mymem.wiki.page import read_page, write_page
from mymem.wiki.types import WikiPage, mint_id

# ---------------------------------------------------------------------------
# mint_id
# ---------------------------------------------------------------------------

class TestMintId:
    def test_returns_26_char_ulid(self):
        uid = mint_id()
        assert len(uid) == 26
        assert uid.isalnum()

    def test_ids_are_unique(self):
        ids = {mint_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_ids_are_time_sortable(self):
        # The first 10 Crockford chars encode the 48-bit millisecond timestamp,
        # which is non-decreasing across calls. (The full string isn't strictly
        # ordered within the same millisecond — the suffix is random.)
        first = mint_id()
        later = mint_id()
        assert first[:10] <= later[:10]


# ---------------------------------------------------------------------------
# WikiPage.id field
# ---------------------------------------------------------------------------

class TestWikiPageId:
    def test_id_defaults_empty(self):
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"))
        assert page.id == ""

    def test_with_updated_preserves_id(self):
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"), id="01ABC")
        updated = page.with_updated(body="new")
        assert updated.id == "01ABC"

    def test_with_updated_can_set_id(self):
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"))
        updated = page.with_updated(id="01XYZ")
        assert updated.id == "01XYZ"

    def test_with_updated_preserves_archived(self):
        # Regression: with_updated previously dropped `archived`.
        page = WikiPage(title="T", body="b", path=Path("wiki/t.md"), archived=True)
        assert page.with_updated(body="new").archived is True


# ---------------------------------------------------------------------------
# Frontmatter I/O
# ---------------------------------------------------------------------------

class TestPageIdIO:
    def test_write_auto_mints_id_when_absent(self, tmp_path: Path):
        p = tmp_path / "page.md"
        write_page(WikiPage(title="No Id", body="# x", path=p))
        loaded = read_page(p)
        assert loaded.id != ""
        assert len(loaded.id) == 26

    def test_explicit_id_is_preserved_on_write(self, tmp_path: Path):
        p = tmp_path / "page.md"
        write_page(WikiPage(title="Has Id", body="# x", path=p, id="01EXPLICIT0000000000000000"))
        assert read_page(p).id == "01EXPLICIT0000000000000000"

    def test_id_is_stable_across_rewrites(self, tmp_path: Path):
        p = tmp_path / "page.md"
        write_page(WikiPage(title="Stable", body="# v1", path=p))
        minted = read_page(p).id
        # Rewrite via with_updated — id must NOT be re-minted.
        write_page(read_page(p).with_updated(body="# v2"))
        assert read_page(p).id == minted

    def test_id_rendered_in_frontmatter(self, tmp_path: Path):
        p = tmp_path / "page.md"
        write_page(WikiPage(title="FM", body="# x", path=p, id="01ZZZ00000000000000000000Z"))
        assert "id: 01ZZZ00000000000000000000Z" in p.read_text()

    def test_legacy_page_without_id_reads_empty(self, tmp_path: Path):
        # A pre-ADR-013 page on disk has no `id:` line.
        p = tmp_path / "legacy.md"
        p.write_text("---\ntitle: Legacy\ndomain: tech\n---\n\n# Legacy\n", encoding="utf-8")
        assert read_page(p).id == ""


# ---------------------------------------------------------------------------
# Resolution index
# ---------------------------------------------------------------------------

class TestResolution:
    def _seed(self, wiki_dir: Path, title: str, uid: str) -> None:
        from mymem.wiki.page import slug_to_path
        write_page(WikiPage(title=title, body="# x", path=slug_to_path(wiki_dir, title), id=uid))

    def test_index_maps_title_and_slug_to_id(self, tmp_path: Path):
        self._seed(tmp_path, "Self Attention", "01AAA00000000000000000000A")
        index = build_page_id_index(tmp_path)
        assert resolve_to_id(index, "Self Attention") == "01AAA00000000000000000000A"
        assert resolve_to_id(index, "self-attention") == "01AAA00000000000000000000A"

    def test_resolution_is_normalization_insensitive(self, tmp_path: Path):
        self._seed(tmp_path, "Self Attention", "01AAA00000000000000000000A")
        index = build_page_id_index(tmp_path)
        # Different surface forms normalize to the same key.
        assert resolve_to_id(index, "  SELF   attention ") == "01AAA00000000000000000000A"

    def test_unknown_title_resolves_none(self, tmp_path: Path):
        self._seed(tmp_path, "Known", "01AAA00000000000000000000A")
        index = build_page_id_index(tmp_path)
        assert resolve_to_id(index, "Nonexistent") is None

    def test_index_skips_pages_without_id(self, tmp_path: Path):
        # Legacy page (no id) must not appear in the index.
        (tmp_path / "legacy.md").write_text(
            "---\ntitle: Legacy\n---\n\n# Legacy\n", encoding="utf-8"
        )
        index = build_page_id_index(tmp_path)
        assert resolve_to_id(index, "Legacy") is None


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

class TestBackfill:
    def _legacy(self, wiki_dir: Path, name: str, title: str) -> Path:
        p = wiki_dir / f"{name}.md"
        p.write_text(f"---\ntitle: {title}\ndomain: tech\n---\n\n# {title}\n", encoding="utf-8")
        return p

    def test_mints_ids_for_legacy_pages(self, tmp_path: Path):
        self._legacy(tmp_path, "a", "Alpha")
        self._legacy(tmp_path, "b", "Beta")
        report = backfill_page_ids(tmp_path)
        assert isinstance(report, BackfillReport)
        assert report.total_pages == 2
        assert report.minted == 2
        assert report.already_had == 0
        assert read_page(tmp_path / "a.md").id != ""
        assert read_page(tmp_path / "b.md").id != ""

    def test_is_idempotent(self, tmp_path: Path):
        self._legacy(tmp_path, "a", "Alpha")
        first = backfill_page_ids(tmp_path)
        minted_id = read_page(tmp_path / "a.md").id
        second = backfill_page_ids(tmp_path)
        assert first.minted == 1
        assert second.minted == 0
        assert second.already_had == 1
        # The id assigned on the first pass is not changed by the second.
        assert read_page(tmp_path / "a.md").id == minted_id

    def test_preserves_existing_ids(self, tmp_path: Path):
        from mymem.wiki.page import slug_to_path
        write_page(WikiPage(title="Kept", body="# x",
                            path=slug_to_path(tmp_path, "Kept"), id="01KEPT0000000000000000000K"))
        self._legacy(tmp_path, "new", "New")
        report = backfill_page_ids(tmp_path)
        assert report.minted == 1          # only the legacy page
        assert report.already_had == 1
        assert read_page(slug_to_path(tmp_path, "Kept")).id == "01KEPT0000000000000000000K"

    def test_empty_wiki(self, tmp_path: Path):
        report = backfill_page_ids(tmp_path)
        assert report == BackfillReport(total_pages=0, minted=0, already_had=0)

    def test_backfill_preserves_updated_date(self, tmp_path: Path):
        # Adding an id must not look like a content edit (would reset
        # introspect's "not revisited in >14 days" logic).
        p = tmp_path / "old.md"
        p.write_text(
            "---\ntitle: Old\ndomain: tech\nupdated: 2026-01-01\n---\n\n# Old\n",
            encoding="utf-8",
        )
        backfill_page_ids(tmp_path)
        reloaded = read_page(p)
        assert reloaded.id != ""
        assert reloaded.updated == date(2026, 1, 1)
