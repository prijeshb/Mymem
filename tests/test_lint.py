"""Tests for mymem.pipeline.lint — pure wiki health checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from mymem.pipeline.lint import (
    IssueKind, LintIssue, format_lint_report, lint_wiki,
)
from mymem.wiki.page import write_page
from mymem.wiki.types import WikiPage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_page(tmp_path: Path, title: str, body: str) -> None:
    slug = title.lower().replace(" ", "-")
    write_page(WikiPage(title=title, body=body, path=tmp_path / f"{slug}.md"))


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------

class TestOrphans:
    def test_detects_orphan(self, tmp_path: Path):
        make_page(tmp_path, "Hub", "# Hub\n\nSee [[Linked Page]] for more.")
        make_page(tmp_path, "Linked Page", "# Linked\n\nSome real content here to avoid stub.")
        make_page(tmp_path, "Orphan", "# Orphan\n\nNobody links here at all ever.")
        issues = lint_wiki(tmp_path)
        orphan_issues = [i for i in issues if i.kind == IssueKind.ORPHAN]
        titles = {i.page_title for i in orphan_issues}
        assert "Orphan" in titles

    def test_hub_not_orphan(self, tmp_path: Path):
        make_page(tmp_path, "Hub", "# Hub\n\nSee [[Spoke]] for more info.")
        make_page(tmp_path, "Spoke", "# Spoke\n\nLinks back to [[Hub]] page.")
        issues = lint_wiki(tmp_path)
        orphan_issues = [i for i in issues if i.kind == IssueKind.ORPHAN]
        titles = {i.page_title for i in orphan_issues}
        assert "Hub" not in titles
        assert "Spoke" not in titles

    def test_single_page_is_orphan(self, tmp_path: Path):
        make_page(tmp_path, "Solo", "# Solo\n\nNo links to or from anyone.")
        issues = lint_wiki(tmp_path)
        orphan_titles = {i.page_title for i in issues if i.kind == IssueKind.ORPHAN}
        assert "Solo" in orphan_titles

    def test_empty_wiki_no_issues(self, tmp_path: Path):
        assert lint_wiki(tmp_path) == []


# ---------------------------------------------------------------------------
# Broken link detection
# ---------------------------------------------------------------------------

class TestBrokenLinks:
    def test_detects_missing_target(self, tmp_path: Path):
        make_page(tmp_path, "Main", "# Main\n\nSee [[Ghost Page]] for details.")
        issues = lint_wiki(tmp_path)
        broken = [i for i in issues if i.kind == IssueKind.BROKEN_LINK]
        assert any("Ghost Page" in i.detail for i in broken)

    def test_valid_link_no_broken_issue(self, tmp_path: Path):
        make_page(tmp_path, "A", "# A\n\nLinks to [[B]] page here.")
        make_page(tmp_path, "B", "# B\n\nLinks to [[A]] page here.")
        issues = lint_wiki(tmp_path)
        broken = [i for i in issues if i.kind == IssueKind.BROKEN_LINK]
        assert len(broken) == 0

    def test_page_with_no_links_no_broken(self, tmp_path: Path):
        make_page(tmp_path, "Isolated", "# Isolated\n\nNo wikilinks at all here.")
        issues = lint_wiki(tmp_path)
        broken = [i for i in issues if i.kind == IssueKind.BROKEN_LINK]
        assert len(broken) == 0


# ---------------------------------------------------------------------------
# Stub detection
# ---------------------------------------------------------------------------

class TestStubs:
    def test_detects_empty_body(self, tmp_path: Path):
        make_page(tmp_path, "Empty", "# Empty Page\n")
        issues = lint_wiki(tmp_path)
        stubs = [i for i in issues if i.kind == IssueKind.STUB]
        assert any(i.page_title == "Empty" for i in stubs)

    def test_full_page_not_stub(self, tmp_path: Path):
        body = "# Full Page\n\n" + "This is a full sentence. " * 20
        make_page(tmp_path, "Full", body)
        issues = lint_wiki(tmp_path)
        stubs = [i for i in issues if i.kind == IssueKind.STUB]
        assert not any(i.page_title == "Full" for i in stubs)

    def test_heading_only_is_stub(self, tmp_path: Path):
        make_page(tmp_path, "Heading Only", "# Heading Only\n\nTodo.")
        issues = lint_wiki(tmp_path)
        stubs = [i for i in issues if i.kind == IssueKind.STUB]
        assert any(i.page_title == "Heading Only" for i in stubs)


# ---------------------------------------------------------------------------
# Clean wiki
# ---------------------------------------------------------------------------

class TestCleanWiki:
    def test_clean_linked_pages_no_issues(self, tmp_path: Path):
        body_a = "# Page A\n\n" + "Content sentence. " * 15 + "\n\nSee [[Page B]]."
        body_b = "# Page B\n\n" + "Content sentence. " * 15 + "\n\nSee [[Page A]]."
        make_page(tmp_path, "Page A", body_a)
        make_page(tmp_path, "Page B", body_b)
        assert lint_wiki(tmp_path) == []


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

class TestFormatReport:
    def test_clean_report(self):
        report = format_lint_report([])
        assert "clean" in report.lower()

    def test_report_lists_issues(self, tmp_path: Path):
        make_page(tmp_path, "Solo", "# Solo\n\nTodo.")
        issues = lint_wiki(tmp_path)
        report = format_lint_report(issues)
        assert isinstance(report, str)
        assert len(report) > 10

    def test_report_groups_by_kind(self, tmp_path: Path):
        make_page(tmp_path, "Solo", "# Solo\n\nTodo.")
        make_page(tmp_path, "Ghost Ref", "# Ghost\n\nSee [[Nonexistent]].")
        issues = lint_wiki(tmp_path)
        report = format_lint_report(issues)
        assert "ORPHAN" in report or "STUB" in report
