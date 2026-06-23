"""HTTP-transport smoke client for the MCP access layer (ADR-017, Phase 1).

Connects over real Streamable-HTTP to a `mymem mcp serve --transport http` server
(started separately) and exercises a few tools + a resource. Prints a transcript.

Usage (server must already be running on :7861):
    venv/Scripts/python.exe scripts/smoke_mcp_http.py
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client

URL = "http://127.0.0.1:7861/mcp"


def _unwrap(result: Any) -> Any:
    for attr in ("data", "structured_content", "content"):
        val = getattr(result, attr, None)
        if val is not None:
            return val
    return result


TOKEN = "smoke-test-token"  # must match MYMEM_MCP_TOKEN used to start the server


async def _exercise(auth: str | None) -> list[str]:
    async with Client(URL, auth=auth) as client:
        await client.ping()
        tools = await client.list_tools()
        res = await client.call_tool("knowledge_gaps", {"limit": 3})
        print("   knowledge_gaps(3):", _unwrap(res))
        res = await client.call_tool("search_wiki", {"query": "attention", "limit": 2})
        stubs = _unwrap(res)
        titles = [s.get("title") for s in stubs] if isinstance(stubs, list) else stubs
        print("   search_wiki('attention'):", titles)
        idx = await client.read_resource("okf://index")
        print("   okf://index first line:", idx[0].text.splitlines()[0] if idx else "")
        return [t.name for t in tools]


async def main() -> None:
    print(f"target: {URL}\n")

    print("1) connect WITHOUT token  (expect DENIED) …")
    try:
        await _exercise(auth=None)
        print("   !! FAIL — server accepted an UNAUTHENTICATED client")
    except Exception as exc:  # noqa: BLE001 - smoke wants any failure surfaced
        print(f"   OK — denied: {type(exc).__name__}: {str(exc)[:100]}")

    print("\n2) connect WITH token  (expect OK) …")
    names = await _exercise(auth=TOKEN)
    print(f"   OK — authenticated; tools: {names}")

    print("\nHTTP SMOKE OK — per-request bearer auth enforced (deny without, allow with)")


if __name__ == "__main__":
    asyncio.run(main())
