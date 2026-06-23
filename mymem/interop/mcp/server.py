"""FastMCP server wiring (ADR-017) — the only module that imports `fastmcp`.

Thin registration: each tool/resource wrapper calls a handler in `tools`/`resources`
and returns plain dicts/strings. `fastmcp` is imported lazily inside `build_mcp_server`
so the rest of the package (and the test suite) does not require it to be installed.
"""
from __future__ import annotations

from typing import Any

from mymem.interop.mcp import resources, tools
from mymem.interop.mcp.context import WikiContext


def build_mcp_server(
    ctx: WikiContext, *, name: str = "mymem", auth_token: str | None = None
) -> Any:
    """Build a FastMCP server exposing the read-only wiki tools + OKF resources.

    When `auth_token` is set (the remote HTTP transport), a per-request bearer-auth
    middleware rejects unauthenticated calls (ADR-017 F3). stdio/local builds omit it.
    Raises ImportError (with install hint) if `fastmcp` is not available.
    """
    try:
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise ImportError(
            "fastmcp is required for `mymem mcp serve`. Install it with: "
            'pip install "mymem[mcp]"  (or: pip install fastmcp)'
        ) from exc

    mcp = FastMCP(name)

    if auth_token:
        from mymem.interop.mcp.middleware import BearerAuthMiddleware

        mcp.add_middleware(BearerAuthMiddleware(auth_token))

    @mcp.tool
    def search_wiki(
        query: str, domain: str | None = None, limit: int = 10
    ) -> list[dict[str, object]]:
        """Search the wiki; returns ranked OKF concept stubs (no bodies)."""
        return [s.as_dict() for s in tools.search_wiki(ctx, query, domain=domain, limit=limit)]

    @mcp.tool
    def get_page(ref: str) -> dict[str, object] | None:
        """Fetch a full page as an OKF concept (frontmatter + body). `ref` = slug or id."""
        payload = tools.get_page(ctx, ref)
        return payload.as_dict() if payload else None

    @mcp.tool
    def list_concepts(
        domain: str | None = None, tag: str | None = None
    ) -> list[dict[str, object]]:
        """List wiki concepts as OKF stubs, optionally filtered by domain/tag."""
        return [s.as_dict() for s in tools.list_concepts(ctx, domain=domain, tag=tag)]

    @mcp.tool
    def knowledge_gaps(limit: int = 20) -> list[dict[str, object]]:
        """Concepts the wiki references but has no page for, ranked by inbound links."""
        return [g.as_dict() for g in tools.knowledge_gaps(ctx, limit=limit)]

    @mcp.tool
    async def ask(question: str, domain: str | None = None) -> dict[str, object]:
        """Answer a question from the wiki, with citations."""
        result = await tools.ask(ctx, question, domain=domain)
        return result.as_dict()

    @mcp.resource("okf://index")
    def okf_index() -> str:
        return resources.okf_index(ctx)

    @mcp.resource("okf://concept/{slug}")
    def okf_concept(slug: str) -> str:
        out = resources.okf_concept(ctx, slug)
        return out if out is not None else f"# Concept not found: {slug}\n"

    return mcp
