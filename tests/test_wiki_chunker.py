"""Tests for mymem.rag.wiki_chunker — header splitting, parent-child linking,
metadata attachment, and empty-heading fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mymem.rag.wiki_chunker import (
    WikiChunk,
    _build_heading_path,
    _extract_frontmatter,
    chunk_wiki_page,
)


# ---------------------------------------------------------------------------
# _extract_frontmatter
# ---------------------------------------------------------------------------

class TestExtractFrontmatter:
    def test_standard_frontmatter(self):
        content = "---\ntitle: My Page\ndomain: tech\ntags: [ml, python]\n---\n\nBody here."
        meta, body = _extract_frontmatter(content)
        assert meta["title"] == "My Page"
        assert meta["domain"] == "tech"
        assert "ml" in meta["tags"]
        assert "python" in meta["tags"]
        assert body.strip() == "Body here."

    def test_no_frontmatter(self):
        content = "# Just a heading\n\nSome content."
        meta, body = _extract_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_unclosed_frontmatter(self):
        content = "---\ntitle: Broken\n\nNo closing delimiter."
        meta, body = _extract_frontmatter(content)
        assert meta == {}
        assert body == content

    def test_empty_tags(self):
        content = "---\ntitle: T\ntags: []\n---\n\nBody."
        meta, body = _extract_frontmatter(content)
        assert meta["tags"] == ""

    def test_tags_stripped_of_brackets_and_quotes(self):
        content = "---\ntitle: T\ntags: [\"tag-a\", \"tag-b\"]\n---\n\nBody."
        meta, body = _extract_frontmatter(content)
        assert "[" not in meta["tags"]
        assert "]" not in meta["tags"]
        assert '"' not in meta["tags"]


# ---------------------------------------------------------------------------
# _build_heading_path
# ---------------------------------------------------------------------------

class TestBuildHeadingPath:
    def test_single_level(self):
        assert _build_heading_path({"h1": "Overview"}) == "Overview"

    def test_two_levels(self):
        assert _build_heading_path({"h1": "Overview", "h2": "Details"}) == "Overview > Details"

    def test_three_levels(self):
        result = _build_heading_path({"h1": "A", "h2": "B", "h3": "C"})
        assert result == "A > B > C"

    def test_empty_metadata(self):
        assert _build_heading_path({}) == ""

    def test_skips_empty_values(self):
        result = _build_heading_path({"h1": "A", "h2": "", "h3": "C"})
        assert ">" in result
        assert result.count(">") == 1  # only one separator for two non-empty parts


# ---------------------------------------------------------------------------
# chunk_wiki_page — core tests
# ---------------------------------------------------------------------------

_SAMPLE_PAGE = """\
---
title: Attention Mechanism
domain: tech
tags: [ml, transformers]
---

# Attention Mechanism

Attention allows the model to focus on relevant parts of the input sequence.

## Scaled Dot-Product

Compute query-key dot products, scale, and softmax to get weights.

## Multi-Head Attention

Run attention in parallel across multiple representation subspaces.
"""

_MINIMAL_PAGE = """\
---
title: Minimal
domain: misc
tags: []
---

Short paragraph with no headings at all.
"""


class TestChunkWikiPage:
    def test_returns_wiki_chunks(self, tmp_path: Path):
        page = tmp_path / "attention-mechanism.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert len(chunks) >= 1
        assert all(isinstance(c, WikiChunk) for c in chunks)

    def test_chunk_type_is_child(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(c.chunk_type == "child" for c in chunks)

    def test_page_title_in_metadata(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(c.page_title == "Attention Mechanism" for c in chunks)

    def test_domain_in_metadata(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(c.domain == "tech" for c in chunks)

    def test_tags_in_metadata(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all("ml" in c.tags for c in chunks)

    def test_embed_text_has_title_prefix(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(c.embed_text.startswith("Attention Mechanism") for c in chunks)

    def test_embed_text_contains_heading_path(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        # At least one chunk should have a heading path from ## headings
        heading_chunks = [c for c in chunks if c.heading_path]
        assert len(heading_chunks) >= 1

    def test_parent_text_is_full_section(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        # parent_text should be non-empty and >= child text length
        for c in chunks:
            assert len(c.parent_text) >= len(c.text) or len(c.parent_text) == len(c.text)

    def test_chunk_indices_sequential(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_source_path_is_absolute(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(Path(c.source_path).is_absolute() for c in chunks)

    def test_empty_heading_fallback(self, tmp_path: Path):
        """Pages with no ## headings still produce chunks."""
        page = tmp_path / "minimal.md"
        page.write_text(_MINIMAL_PAGE, encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert len(chunks) >= 1
        # heading_path may be empty but embed_text still starts with title
        assert all(c.embed_text.startswith("Minimal") for c in chunks)

    def test_missing_file_returns_empty(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist.md"
        chunks = chunk_wiki_page(missing)
        assert chunks == []

    def test_empty_body_returns_empty(self, tmp_path: Path):
        page = tmp_path / "empty.md"
        page.write_text("---\ntitle: Empty\ndomain: misc\ntags: []\n---\n\n", encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert chunks == []

    def test_no_frontmatter_uses_filename_as_title(self, tmp_path: Path):
        page = tmp_path / "my-concept.md"
        page.write_text("## Overview\n\nSome content here.", encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert len(chunks) >= 1
        assert all(c.page_title == "my-concept" for c in chunks)

    def test_large_section_truncates_parent(self, tmp_path: Path):
        long_section = "## Big Section\n\n" + ("word " * 2000)
        page = tmp_path / "long.md"
        page.write_text(f"---\ntitle: Long\ndomain: misc\ntags: []\n---\n\n{long_section}", encoding="utf-8")
        chunks = chunk_wiki_page(page)
        assert all(len(c.parent_text) <= 4096 for c in chunks)

    def test_import_error_returns_empty(self, tmp_path: Path):
        page = tmp_path / "page.md"
        page.write_text(_SAMPLE_PAGE, encoding="utf-8")
        import builtins
        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "langchain_text_splitters":
                raise ImportError("no langchain")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            chunks = chunk_wiki_page(page)
        assert chunks == []
