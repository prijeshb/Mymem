"""
Wiki page I/O — read, write, and list markdown pages with YAML frontmatter.

The LLM pipeline owns the wiki/ directory entirely. These functions are the
only entry points for touching wiki pages on disk.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

import yaml

from mymem.wiki.tags import domain_from_str, normalize_tags
from mymem.wiki.types import TagDomain, WikiPage


# ---------------------------------------------------------------------------
# Frontmatter parsing helpers
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def _split_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    """Split a markdown file into (frontmatter_dict, body)."""
    m = _FM_RE.match(raw)
    if not m:
        return {}, raw
    try:
        fm: dict[str, object] = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    body = raw[m.end():]
    return fm, body


def _parse_date(value: object) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return date.today()


def _render_frontmatter(page: WikiPage) -> str:
    """Render a WikiPage back to a markdown string with YAML frontmatter."""
    fm: dict[str, object] = {
        "title":   page.title,
        "domain":  page.domain.value,
        "tags":    list(page.tags),
        "sources": list(page.sources),
        "created": page.created.isoformat(),
        "updated": page.updated.isoformat(),
    }
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=True)
    return f"---\n{fm_str}---\n\n{page.body}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_page(path: Path) -> WikiPage:
    """
    Read a wiki page from disk.

    Raises FileNotFoundError if the path does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Wiki page not found: {path}")

    raw = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(raw)

    raw_tags: list[str] = []
    if isinstance(fm.get("tags"), list):
        raw_tags = [str(t) for t in fm["tags"]]  # type: ignore[union-attr]

    raw_sources: list[str] = []
    if isinstance(fm.get("sources"), list):
        raw_sources = [str(s) for s in fm["sources"]]  # type: ignore[union-attr]

    return WikiPage(
        title=str(fm.get("title", path.stem)),
        body=body.rstrip("\n"),
        path=path,
        tags=normalize_tags(raw_tags),  # type: ignore[arg-type]
        sources=raw_sources,
        domain=domain_from_str(str(fm.get("domain", "misc"))),
        created=_parse_date(fm.get("created")),
        updated=_parse_date(fm.get("updated")),
    )


def write_page(page: WikiPage) -> None:
    """
    Write a WikiPage to disk.

    Creates parent directories if they don't exist.
    Always stamps `updated` to today at write time.
    """
    page = dataclasses.replace(page, updated=date.today())
    page.path.parent.mkdir(parents=True, exist_ok=True)
    content = _render_frontmatter(page)
    page.path.write_text(content, encoding="utf-8")


def list_pages(wiki_dir: Path) -> list[WikiPage]:
    """
    Return all wiki pages in wiki_dir (non-recursive, top-level only).

    Skips index.md, log.md, and any files starting with '.'.
    Returns an empty list if the directory is empty or doesn't exist.
    """
    if not wiki_dir.exists():
        return []

    pages: list[WikiPage] = []
    skip = {"index.md", "log.md"}

    for md_file in sorted(wiki_dir.glob("*.md")):
        if md_file.name.startswith(".") or md_file.name in skip:
            continue
        try:
            pages.append(read_page(md_file))
        except Exception:
            # Corrupt or unreadable page — skip silently, let lint catch it
            continue

    return pages


def slug_to_path(wiki_dir: Path, title: str) -> Path:
    """Derive the expected file path for a page with the given title."""
    import re
    slug = title.lower().replace(" ", "-")
    # Strip any characters that could cause path traversal
    slug = re.sub(r"[^a-z0-9\-]", "", slug)
    slug = slug[:120] or "untitled"
    return wiki_dir / f"{slug}.md"
