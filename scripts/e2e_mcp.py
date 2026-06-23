"""End-to-end exercise of the MCP access layer (ADR-017, Phase 1).

Drives the REAL FastMCP server through FastMCP's in-memory Client (real protocol
round-trip, no sockets) against the live wiki/. `ask` uses a fake router so the full
tool path runs without a live LLM. Prints a transcript used as E2E evidence.

Run:  venv/Scripts/python.exe scripts/e2e_mcp.py
"""
from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

from fastmcp import Client

from mymem.config import get_settings
from mymem.interop.mcp.context import context_from_settings
from mymem.interop.mcp.server import build_mcp_server
from mymem.wiki.page import list_pages


class _FakeRouter:
    """No live LLM — canned synthesis so `ask` exercises the full MCP path."""

    session_cost = 0.0

    async def call(self, prompt: str, *, task: str, system: str) -> str:
        return "Synthesized from wiki context (fake router; no live LLM in E2E)."


def _hr(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def _unwrap(result: Any) -> Any:
    """Pull structured data out of a FastMCP CallToolResult across versions."""
    for attr in ("data", "structured_content", "content"):
        val = getattr(result, attr, None)
        if val is not None:
            return val
    return result


def _show(label: str, value: Any, *, limit: int = 600) -> None:
    try:
        text = json.dumps(value, indent=2, default=str)
    except TypeError:
        text = str(value)
    if len(text) > limit:
        text = text[:limit] + f"\n… [truncated, {len(text)} chars total]"
    print(f"\n[{label}]\n{text}")


async def main() -> None:
    settings = get_settings()
    ctx = context_from_settings(settings, router=_FakeRouter())
    pages = list_pages(ctx.wiki_dir)
    sample_slug = pages[0].path.stem if pages else "missing"

    _hr("0. ENVIRONMENT")
    print(f"wiki_dir      : {ctx.wiki_dir}  ({len(pages)} pages)")
    print(f"graph_db      : {ctx.graph_db}  (exists={ctx.graph_db.exists()})")
    print(f"sample slug   : {sample_slug}")
    print(f"server.run sig: {inspect.signature(build_mcp_server(ctx).run)}")

    server = build_mcp_server(ctx)
    async with Client(server) as client:
        _hr("1. PROTOCOL HANDSHAKE — list tools & resources")
        tools = await client.list_tools()
        print("tools     :", [t.name for t in tools])
        resources = await client.list_resources()
        templates = await client.list_resource_templates()
        print("resources :", [r.uri for r in resources] if resources else "[]")
        print("templates :", [t.uriTemplate for t in templates] if templates else "[]")

        _hr("2. TOOL: search_wiki('attention', limit=3)")
        res = await client.call_tool("search_wiki", {"query": "attention", "limit": 3})
        _show("search_wiki", _unwrap(res))

        _hr(f"3. TOOL: get_page('{sample_slug}')  — OKF concept payload")
        res = await client.call_tool("get_page", {"ref": sample_slug})
        _show("get_page", _unwrap(res), limit=900)

        _hr("4. TOOL: list_concepts(limit via domain='tech') — first few")
        res = await client.call_tool("list_concepts", {"domain": "tech"})
        data = _unwrap(res)
        count = len(data) if isinstance(data, list) else "n/a"
        head = data[:3] if isinstance(data, list) else data
        print(f"count(tech) = {count}")
        _show("list_concepts[:3]", head)

        _hr("5. TOOL: knowledge_gaps(limit=5)")
        res = await client.call_tool("knowledge_gaps", {"limit": 5})
        _show("knowledge_gaps", _unwrap(res))

        _hr("6. TOOL: ask('what is attention?')  — fake router")
        res = await client.call_tool("ask", {"question": "what is attention?"})
        _show("ask", _unwrap(res))

        _hr("7. RESOURCE: okf://index  (first 400 chars)")
        idx = await client.read_resource("okf://index")
        idx_text = idx[0].text if idx else ""
        print(idx_text[:400])

        _hr(f"8. RESOURCE: okf://concept/{sample_slug}  (first 500 chars)")
        con = await client.read_resource(f"okf://concept/{sample_slug}")
        con_text = con[0].text if con else ""
        print(con_text[:500])

    _hr("E2E COMPLETE — all tool/resource calls returned without error")


if __name__ == "__main__":
    asyncio.run(main())
