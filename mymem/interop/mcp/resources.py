"""MCP read-context resources (ADR-017).

`okf://index` and `okf://concept/{slug}` mirror the OKF bundle layout (ADR-016) as
live resources, so an MCP client can pull the same directory + concept files MyMem
would export — without writing a bundle to disk.
"""
from __future__ import annotations

import yaml

from mymem.interop.mcp.context import WikiContext
from mymem.interop.mcp.tools import get_page
from mymem.wiki.page import list_pages


def okf_index(ctx: WikiContext) -> str:
    """OKF `index.md` — directory listing, no frontmatter (spec)."""
    lines = ["# Wiki", ""]
    for page in sorted(list_pages(ctx.wiki_dir), key=lambda p: p.title.lower()):
        lines.append(f"- [{page.title}](/{page.path.stem}.md)")
    return "\n".join(lines) + "\n"


def okf_concept(ctx: WikiContext, slug: str) -> str | None:
    """A single OKF concept file (frontmatter + body), or None if not found."""
    payload = get_page(ctx, slug)
    if payload is None:
        return None
    fm_str = yaml.dump(
        payload.frontmatter, default_flow_style=False, allow_unicode=True, sort_keys=True
    )
    return f"---\n{fm_str}---\n\n{payload.body}\n"
