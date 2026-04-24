"""
Wiki linter — pure Python health checks, no LLM required.

Detects:
  ORPHAN      — page with no inbound wikilinks from other pages
  BROKEN_LINK — page references a [[Target]] that has no wiki page
  STUB        — page body is too short to be useful (< 50 words)

100% test coverage is required for this module (pure logic, no I/O side effects
beyond reading files that already exist).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mymem.wiki.page import list_pages
from mymem.wiki.types import WikiPage


# ---------------------------------------------------------------------------
# Issue types
# ---------------------------------------------------------------------------

class IssueKind(str, Enum):
    ORPHAN      = "orphan"
    BROKEN_LINK = "broken_link"
    STUB        = "stub"


@dataclass(frozen=True)
class LintIssue:
    kind:       IssueKind
    page_title: str
    detail:     str


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

_STUB_WORD_THRESHOLD = 30  # fewer than this many words → stub


def _word_count(text: str) -> int:
    return len(text.split())


def _heading_only(body: str) -> bool:
    """True if body is just a heading with no real content."""
    non_heading = [
        line for line in body.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return _word_count(" ".join(non_heading)) < _STUB_WORD_THRESHOLD


def _build_title_index(pages: list[WikiPage]) -> dict[str, WikiPage]:
    """Map lowercase title → WikiPage for fast lookup."""
    return {p.title.lower(): p for p in pages}


def _check_broken_links(
    page: WikiPage, title_index: dict[str, WikiPage]
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for link in page.wikilinks():
        if link.lower() not in title_index:
            issues.append(
                LintIssue(
                    kind=IssueKind.BROKEN_LINK,
                    page_title=page.title,
                    detail=f"[[{link}]] has no matching wiki page",
                )
            )
    return issues


def _check_orphans(
    pages: list[WikiPage], title_index: dict[str, WikiPage]
) -> list[LintIssue]:
    """A page is an orphan if no other page links to it."""
    linked_to: set[str] = set()
    for page in pages:
        for link in page.wikilinks():
            linked_to.add(link.lower())

    issues: list[LintIssue] = []
    for page in pages:
        if page.title.lower() not in linked_to:
            issues.append(
                LintIssue(
                    kind=IssueKind.ORPHAN,
                    page_title=page.title,
                    detail="No other page links to this page",
                )
            )
    return issues


def _check_stubs(pages: list[WikiPage]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for page in pages:
        if _heading_only(page.body):
            issues.append(
                LintIssue(
                    kind=IssueKind.STUB,
                    page_title=page.title,
                    detail=(
                        f"Body has fewer than {_STUB_WORD_THRESHOLD} words — "
                        "consider expanding or merging with another page"
                    ),
                )
            )
    return issues


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lint_wiki(wiki_dir: Path) -> list[LintIssue]:
    """
    Run all lint checks on the wiki directory.

    Returns a list of issues (empty = clean). Does NOT modify any files.
    Skips index.md and log.md automatically (via list_pages).
    """
    pages = list_pages(wiki_dir)
    if not pages:
        return []

    title_index = _build_title_index(pages)
    issues: list[LintIssue] = []

    issues.extend(_check_orphans(pages, title_index))
    for page in pages:
        issues.extend(_check_broken_links(page, title_index))
    issues.extend(_check_stubs(pages))

    return issues


def format_lint_report(issues: list[LintIssue]) -> str:
    """Human-readable lint report for CLI output."""
    if not issues:
        return "Wiki is clean — no issues found."

    by_kind: dict[IssueKind, list[LintIssue]] = {}
    for issue in issues:
        by_kind.setdefault(issue.kind, []).append(issue)

    lines: list[str] = [f"Found {len(issues)} issue(s):\n"]
    for kind in IssueKind:
        group = by_kind.get(kind, [])
        if not group:
            continue
        lines.append(f"[{kind.value.upper()}] ({len(group)})")
        for issue in group:
            lines.append(f"  • {issue.page_title}: {issue.detail}")
        lines.append("")

    return "\n".join(lines).rstrip()
