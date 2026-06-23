# E2E Report — MCP Access Layer (ADR-017, Phase 1)

> Date: 2026-06-22 · Branch: V1-0015 · Harness: `scripts/e2e_mcp.py`
> Raw transcript: `docs/testing/mcp-interop-e2e-output.txt`

## On "screenshot / video"

This feature is a **headless stdio/MCP protocol server** — there is no GUI to screenshot. The faithful
visual artifact is a **terminal transcript of a real MCP client driving the real server**. The harness
uses FastMCP's in-memory `Client` (a true protocol round-trip — `list_tools`, `call_tool`,
`read_resource` — without a socket) against the **live 144-page wiki**. `ask` uses a fake router so the
full tool path runs without a live LLM (per the no-LLM-in-tests rule). To get an actual *picture*, the
real-world equivalent is Claude Code's MCP panel listing the `mymem` server after
`mymem mcp serve` — that requires your interactive client and is the recommended manual confirm (below).

## Environment

| Item | Value |
|------|-------|
| `fastmcp` | 3.4.2 (installed; matches the `[mcp]` pin `>=3.4,<4`) |
| Wiki | `wiki/` — 144 pages |
| Graph db | `data/graph.db` (exists) |
| `server.run` signature | `(transport: Transport \| None = None, show_banner=None, **transport_kwargs)` — confirms `run(transport="http", host=, port=)` is correctly shaped |

## What was tested (and the result)

| # | Surface | Call | Result |
|---|---------|------|--------|
| 1 | Handshake | `list_tools` / `list_resources` / `list_resource_templates` | ✅ 5 tools `[search_wiki, get_page, list_concepts, knowledge_gaps, ask]`; resource `okf://index`; template `okf://concept/{slug}` |
| 2 | `search_wiki` | `("attention", limit=3)` | ✅ ranked `ConceptStub`s with real titles + descriptions, no bodies |
| 3 | `get_page` | `("2-phase-commit-vs-saga-pattern")` | ✅ OKF concept: `type`, `title`, **`id` preserved** (`01KV6J5…`), `sources`, body `[[wikilinks]]` → `[Microservices](/microservices.md)` |
| 4 | `list_concepts` | `(domain="tech")` | ✅ 104 tech concepts; stubs well-formed |
| 5 | `knowledge_gaps` | `(limit=5)` | ✅ real ranked gaps: `JWT(8), Transformer(8), LLM(7), Durable State Machines(6), Entropy(6)` |
| 6 | `ask` | `("what is attention?")` | ✅ returns answer + citations through the MCP path (fake router) |
| 7 | `okf://index` | `read_resource` | ✅ OKF directory listing, no frontmatter (spec form) |
| 8 | `okf://concept/{slug}` | `read_resource(...)` | ✅ full OKF concept file (frontmatter + body) |
| — | CLI | `mcp serve --help` | ✅ renders |
| — | CLI | `mcp serve --transport http` (no token) | ✅ **fail-closed**, exit 1, refusal message |
| — | Types/lint | `mypy`/`ruff` on `mymem/interop` | ✅ clean (except pre-existing `yaml` baseline) |
| — | Unit | full suite | ✅ 971 passed / 2 skipped (smoke test passes once `fastmcp` is installed) |

**Verdict: GO for Phase 1 (read-only).** Every tool and resource works end-to-end over the real
protocol against the real wiki.

### HTTP (Streamable-HTTP) transport smoke

Harness: `scripts/smoke_mcp_http.py` (real network client) against a backgrounded
`mymem mcp serve --transport http --port 7861` (FastMCP 3.4.2, default mount path `/mcp`).

| Step | Result |
|------|--------|
| Server start (token set) | ✅ port 7861 open in ~0.5s |
| Client `ping` + `list_tools` | ✅ all 5 tools listed over HTTP |
| `knowledge_gaps(3)` | ✅ `JWT(8), Transformer(8), LLM(7)` |
| `search_wiki("attention", 2)` | ✅ `['DeepSeek V4 Compressed Attention', 'CSA vs HCA Mechanisms']` |
| `read_resource("okf://index")` | ✅ `# Wiki` |
| Client WITHOUT token | ✅ **DENIED** (`McpError`) — per-request auth enforced (F3 fixed) |
| Client WITH token | ✅ all tools + resource served |

## Transcript excerpts

```
1. PROTOCOL HANDSHAKE
tools     : ['search_wiki', 'get_page', 'list_concepts', 'knowledge_gaps', 'ask']
resources : [AnyUrl('okf://index')]
templates : ['okf://concept/{slug}']

5. knowledge_gaps(limit=5)
[ {"concept":"JWT","inbound_refs":8}, {"concept":"Transformer","inbound_refs":8},
  {"concept":"LLM","inbound_refs":7}, {"concept":"Durable State Machines","inbound_refs":6},
  {"concept":"Entropy","inbound_refs":6} ]

B. http fail-closed (no token)
Refusing to start remote MCP: set MYMEM_MCP_TOKEN in your .env first (fail-closed). … exit code: 1
```
(Full output in `mcp-interop-e2e-output.txt`.)

## Findings — where it needs fixes

### F1 — `description` leaked raw `[[wikilinks]]` — ✅ FIXED
`get_page`/`list_concepts` descriptions and the OKF `frontmatter.description` previously kept raw
`[[Microservices]]` because the description was taken from `_first_paragraph(body)` **before** link
rewriting. This was **shared with `mymem/knowledge/okf/exporter.py`**.
- **Fix applied:** new shared helper `flatten_wikilinks(text)` in `mymem/knowledge/okf/_links.py`
  (`[[X]]`→`X`), used by **both** the exporter `_first_paragraph` and the MCP tools `_first_paragraph`,
  so OKF export and MCP payloads stay consistent.
- **Verified:** re-run E2E shows `"description": "When moving from a monolithic architecture to
  Microservices or sharded databases…"` (plain text); grep for `[[` in description fields → **0**.
  Tests: `test_flatten_wikilinks_to_plain_text` (okf core) + a `[[`-absent assertion in
  `test_get_page_returns_okf_payload`. Full suite 973 passed / 1 skipped.

### F2 — `ask` citations are noisy / inconsistently formatted (LOW; query-pipeline behavior)
For `"what is attention?"`, citations mixed plain page titles (`OpenID Connect…`, unrelated) with
RAG section labels (`[[Transformer Architecture Fundamentals § … > Self-Attention]]`). This is the
existing `query_wiki` retrieval/citation contract (broad keyword match + RAG labels), surfaced verbatim
by the fake router; a real LLM narrows the answer but citations still come through as-is.
- **Severity:** low — answer quality depends on `query_wiki` + a real model, out of MCP-layer scope.
- **Recommended (optional) follow-up:** normalize citation strings the MCP `ask` returns (strip `[[ ]]`,
  drop section anchors) so external agents get clean page references.

### F3 — Per-request bearer enforcement — ✅ FIXED
HTTP transport was fail-closed at *startup* but did not validate the token *per request* (an
unauthenticated client was served). **Fixed:** `BearerAuthMiddleware`
(`mymem/interop/mcp/middleware.py`) is attached whenever a token is configured (the HTTP transport);
its `on_request` hook reads the `Authorization` header via `get_http_headers(include_all=True)` and
calls `auth.authorize_request` → rejects any request without the valid bearer token. Local
stdio/in-memory requests have no HTTP headers and pass through (not network-reachable).
- **Verified (network-level):** HTTP smoke — client **without** token → **denied** (`McpError`); client
  **with** token → all tools + resource served. (`scripts/smoke_mcp_http.py`.)
- **Tests:** `test_extract_bearer`, `test_authorize_request_local_allows_without_token`,
  `test_authorize_request_http_requires_valid_token`. Full suite 976 passed / 1 skipped.
- **Still deferred (separate backlog):** SSRF URL-allowlist + write-endpoint rate limiting — required
  before exposing beyond a trusted network, independent of auth.

No CRITICAL or HIGH issues. F1/F2 are cosmetic; F3 is the documented Phase-1 boundary.

## Manual confirm (real client, for a literal "screenshot")

```bash
pip install -e ".[mcp]"
mymem mcp serve                 # stdio
```
Then add it to an MCP client. Example Claude Code MCP config:
```json
{ "mcpServers": { "mymem": { "command": "mymem", "args": ["mcp", "serve"] } } }
```
The client's MCP panel will then list the 5 `mymem` tools — that view is the GUI screenshot equivalent.

## Reproduce

```bash
pip install -e ".[mcp]"
venv/Scripts/python.exe scripts/e2e_mcp.py     # writes the transcript shown above
```
