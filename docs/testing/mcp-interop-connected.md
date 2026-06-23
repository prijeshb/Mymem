# MCP Access Layer — Connection Verified (ADR-017)

> Date: 2026-06-22 · Branch: V1-0015 · `mymem mcp serve` connected to live MCP clients.

This records that the MyMem MCP server is **connected and serving tools in real clients**
(Claude Code CLI + Claude Desktop), after the MCP-host launch fixes (project-root anchoring,
stdout-clean stdio, graceful logging, per-request auth).

---

## 1. Claude Code CLI — text evidence (captured)

### Server registered & healthy — `claude mcp list`
```
$ claude mcp list
Checking MCP server health…
...
mymem: venv\Scripts\mymem.exe mcp serve - ✔ Connected
```

### A real tool call through the CLI — `claude -p`
```
$ claude -p "Use the mymem MCP server's knowledge_gaps tool (limit 3) and list what it returns" \
    --allowedTools "mcp__mymem__knowledge_gaps"

The `knowledge_gaps` tool (limit 3) returned these concepts the wiki references but
has no page for, ranked by inbound links:

| Concept     | Inbound refs |
|-------------|--------------|
| JWT         | 8            |
| Transformer | 8            |
| LLM         | 7            |
```
The values match the wiki's real entity graph (`data/graph.db`) — the agent reached the live
wiki over MCP and returned correct data.

### Independent stdio-spawn client (proves the protocol channel)
`scripts/` + a spawned-stdio FastMCP client (same parse path Claude Desktop uses) listed all five
tools and returned real data without a JSON-parse error:
```
STDIO OK — tools: ['search_wiki', 'get_page', 'list_concepts', 'knowledge_gaps', 'ask']
knowledge_gaps: [{'concept': 'JWT', 'inbound_refs': 8}, {'concept': 'Transformer', 'inbound_refs': 8}]
STDIO CHANNEL CLEAN — client parsed stdout as JSON without error
```

---

## 2. Claude Desktop — screenshot (add yours here)

> Image capture of the desktop app can't be produced by the agent — drop your PNG into
> `docs/testing/img/` and it renders below.

**To add it:** take a screenshot in Claude Desktop showing the `mymem` server connected with its
tools (Settings → Developer → MCP, or the tools list when you invoke one), save it as
`docs/testing/img/claude-desktop-mymem-connected.png`.

![Claude Desktop — mymem MCP connected](./img/claude-desktop-mymem-connected.png)

Optionally also capture the CLI for a matching pair:
`docs/testing/img/claude-cli-mymem-connected.png`

![Claude Code CLI — mymem MCP connected](./img/claude-cli-mymem-connected.png)

---

## 3. Config used (Claude Desktop)

```json
"mymem": {
  "type": "stdio",
  "command": "C:/Users/prije/Desktop/AI apps/MyMem/venv/Scripts/mymem.exe",
  "args": ["mcp", "serve"],
  "cwd": "C:/Users/prije/Desktop/AI apps/MyMem",
  "env": { "MYMEM_PROJECT_DIR": "C:/Users/prije/Desktop/AI apps/MyMem" }
}
```
(`MYMEM_PROJECT_DIR` is what makes it work regardless of the CWD the host launches from; `cwd` is
kept as a harmless hint for hosts that honor it.)

## 4. Tools available to the client
`search_wiki` · `get_page` · `list_concepts` · `knowledge_gaps` · `ask`
Resources: `okf://index` · `okf://concept/{slug}` — payloads are OKF v0.1 concepts (ADR-016).
> `ask` needs your LLM provider (Ollama) running; the other four are pure retrieval.
