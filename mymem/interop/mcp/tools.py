"""MCP tool handlers (ADR-017, Phase 1 — all read-only).

Each function is a pure delegate over an existing internal: index search, page I/O,
OKF mapping, query synthesis, and the knowledge-gap ranker. Returns frozen payloads
(`payloads.py`). No function here mutates the wiki.
"""
from __future__ import annotations

import re
from pathlib import Path

from mymem.graph.gaps import knowledge_gaps as _rank_gaps
from mymem.interop.mcp.context import WikiContext
from mymem.interop.mcp.payloads import AskResult, ConceptPayload, ConceptStub, GapItem
from mymem.knowledge.okf._links import flatten_wikilinks, wikilinks_to_markdown
from mymem.knowledge.okf._map import to_okf_frontmatter
from mymem.wiki.index import IndexManager
from mymem.wiki.page import list_pages, read_page
from mymem.wiki.types import WikiPage, slugify

_HEADING_RE = re.compile(r"^#{1,6}\s")


def _first_paragraph(body: str, *, limit: int = 200) -> str:
    """First non-heading, non-empty line — used for `description` (mirrors exporter)."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("*-> ").strip()
        if stripped and not _HEADING_RE.match(line.strip()):
            return flatten_wikilinks(stripped)[:limit]
    return ""


def search_wiki(
    ctx: WikiContext, query: str, *, domain: str | None = None, limit: int = 10
) -> list[ConceptStub]:
    """Ranked concept stubs (no bodies) via the deterministic index search."""
    if not ctx.index_path.exists():
        return []
    mgr = IndexManager(ctx.index_path)
    candidates = mgr.search(query, top_k=max(limit * 2, limit))
    if domain:
        candidates = [e for e in candidates if e.domain.value == domain]
    return [
        ConceptStub(
            title=e.title,
            slug=Path(str(e.path)).stem,
            domain=e.domain.value,
            description=e.summary,
        )
        for e in candidates[:limit]
    ]


def _resolve_page(ctx: WikiContext, ref: str) -> WikiPage | None:
    """Resolve a page by slug, ULID id, or slugified title."""
    candidate = ctx.wiki_dir / f"{ref}.md"
    if candidate.exists():
        return read_page(candidate)
    target = slugify(ref)
    for page in list_pages(ctx.wiki_dir, include_archived=True):
        if page.id == ref or page.path.stem == ref or slugify(page.title) == target:
            return page
    return None


def get_page(ctx: WikiContext, ref: str) -> ConceptPayload | None:
    """Full page as an OKF concept payload, or None if not found (identity-stable)."""
    page = _resolve_page(ctx, ref)
    if page is None:
        return None
    title_to_slug = {p.title: p.path.stem for p in list_pages(ctx.wiki_dir, include_archived=True)}
    description = _first_paragraph(page.body)
    frontmatter = to_okf_frontmatter(page, description=description)
    body, _unresolved = wikilinks_to_markdown(page.body, title_to_slug.get)
    return ConceptPayload(
        uri=f"okf://concept/{page.path.stem}.md",
        frontmatter=frontmatter,
        body=body,
    )


def list_concepts(
    ctx: WikiContext, *, domain: str | None = None, tag: str | None = None
) -> list[ConceptStub]:
    """List wiki concepts as stubs, optionally filtered by domain and/or tag."""
    out: list[ConceptStub] = []
    for page in list_pages(ctx.wiki_dir):
        if domain and page.domain.value != domain:
            continue
        if tag and tag not in page.tags:
            continue
        out.append(
            ConceptStub(
                title=page.title,
                slug=page.path.stem,
                domain=page.domain.value,
                description=_first_paragraph(page.body),
            )
        )
    return out


def knowledge_gaps(ctx: WikiContext, *, limit: int = 20) -> list[GapItem]:
    """Referenced-but-unwritten concepts, ranked by inbound page refs (ADR-008 D12)."""
    return [
        GapItem(concept=g.concept, inbound_refs=g.inbound_refs)
        for g in _rank_gaps(ctx.graph_db, limit=limit)
    ]


async def ask(ctx: WikiContext, question: str, *, domain: str | None = None) -> AskResult:
    """Synthesize an answer with citations by reusing the query pipeline."""
    if ctx.router is None:
        raise ValueError("ask requires a configured router on the WikiContext")
    from mymem.pipeline.query import query_wiki
    from mymem.wiki.types import TagDomain

    domain_filter = TagDomain(domain) if domain else None
    result = await query_wiki(
        question,
        wiki_dir=ctx.wiki_dir,
        index_path=ctx.index_path,
        log_path=ctx.log_path,
        router=ctx.router,
        save=False,
        domain_filter=domain_filter,
        rag_db_path=ctx.rag_db if ctx.rag_db.exists() else None,
    )
    return AskResult(question=question, answer=result.answer, citations=list(result.citations))
