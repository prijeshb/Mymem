# System Design: Agent-to-Agent Knowledge Interop (MCP + OKF, A2A later)

> Slug: `agent-knowledge-interop` · ADR-017 · PRD: docs/PRD/agent-knowledge-interop.md

## Overview

Add a thin **MCP access layer** over MyMem's existing knowledge functions. The wiki is exposed as an
MCP server whose **Tools** and **Resources** return **OKF-formatted** payloads. The server is pure
adapter code: every handler delegates to an existing internal function (retrieval, synthesis, OKF
mapping, gaps). Transport is **stdio by default** (local clients) with an **opt-in, token-gated remote
HTTP/SSE** transport. Phase 2 (contribute) and Phase 3 (A2A federation) extend the same registry
without altering Phase 1.

## Architecture diagram

```
External agent / Claude Code / peer MyMem
        │  (MCP: stdio  |  Streamable HTTP/SSE + bearer token)
        ▼
┌──────────────────────────────────────────────────────────┐
│  mymem/interop/mcp/  (FastMCP server — NEW, thin adapter)  │
│                                                            │
│  Tools (actions)              Resources (read context)     │
│   search_wiki  ───┐            okf://index                  │
│   get_page     ───┤            okf://concept/{slug}         │
│   ask          ───┤                                        │
│   list_concepts───┤   auth scopes: READ (P1) / WRITE (P2)  │
│   knowledge_gaps──┘                                        │
│        │  (every handler delegates downward — no new logic)│
└────────┼───────────────────────────────────────────────────┘
         ▼
┌──────────────── existing MyMem internals (reused) ─────────┐
│ pipeline/query.py   query_wiki() → answer + citations      │
│ wiki/page.py        list_pages(), read_page()              │
│ knowledge/okf/_map  to_okf_frontmatter(); _links rewrite   │
│ knowledge/claim_index, retrieval  cross-page retrieval     │
│ graph/gaps.py       rank_gaps()                            │
│ pipeline/router/    model selection + fallback (for ask)   │
│ [Phase 2] pipeline/compounding.reconcile_source_claims()   │
│ [Phase 2] security/ scanner on inbound content             │
└────────────────────────────────────────────────────────────┘
         ▲
         │  payload schema = OKF v0.1 concept (frontmatter + body)
         └── validated by knowledge/okf/conformance.check_*
```

## Components

### New module: `mymem/interop/mcp/`
Keep files < 300 lines (project rule); one concern per file.

| File | Responsibility |
|------|----------------|
| `server.py` | FastMCP app factory `build_mcp_server(settings, *, llm_fn=None)`; registers tools+resources; transport selection. Thin. |
| `tools.py` | The 5 read tools — each a typed function delegating to internals, returning OKF/stub dataclasses. |
| `resources.py` | `okf://index`, `okf://concept/{slug}` handlers (reuse exporter render fns). |
| `payloads.py` | Frozen dataclasses: `ConceptStub`, `ConceptPayload` (wraps OKF frontmatter+body), `AskResult` (answer+citations). Serialization to MCP tool-result JSON. |
| `auth.py` | Bearer-token check for remote transport; reads `MYMEM_MCP_TOKEN` from env; fail-closed. |
| `__init__.py` | Public exports. |

> Note: `mymem/interop/` is a new top-level package deliberately separate from `web/` — MCP is a
> distinct protocol surface, not an HTTP route. (Phase 3 `interop/a2a/` lands beside it.)

### Tool contract (Phase 1 — all read-only)

| Tool | Signature | Delegates to | Returns |
|------|-----------|--------------|---------|
| `search_wiki` | `(query: str, domain: str\|None, limit: int=10)` | `query.py` hybrid retrieval / `claim_index` | `list[ConceptStub]` (title, slug, domain, description, score) — **no bodies** |
| `get_page` | `(ref: str)  # slug or ULID id` | `wiki/identity.resolve_to_id` + `read_page` + `okf/_map` | `ConceptPayload` (OKF frontmatter + body, wikilinks→md links) |
| `ask` | `(question: str, domain: str\|None)` | `query_wiki(..., llm_fn)` | `AskResult` (answer + citation slugs) |
| `list_concepts` | `(domain: str\|None, tag: str\|None)` | `list_pages` + index | `list[ConceptStub]` |
| `knowledge_gaps` | `(limit: int=20)` | `graph/gaps.rank_gaps` | ranked referenced-but-unwritten concepts |

### Resources

| URI | Content | Reuse |
|-----|---------|-------|
| `okf://index` | OKF `index.md` directory listing | `okf/exporter._render_index` (extract to shared fn) |
| `okf://concept/{slug}` | single OKF concept file | `okf/exporter._render_okf_file` + `_map` |

### CLI (`mymem/cli.py`)
```
mymem mcp serve                         # stdio (local, default) — no token needed
mymem mcp serve --transport http --port 7861   # remote; requires MYMEM_MCP_TOKEN; fail-closed
mymem mcp tools                         # list registered tools (debug)
```

## Data flow — `ask` (representative)
1. MCP client calls tool `ask(question, domain)`.
2. `auth.py` (remote only) validates bearer token → 401 if absent/wrong.
3. `tools.ask` calls `query_wiki(question, domain=..., llm_fn=router_llm_fn)`.
4. `query_wiki` does hybrid wiki-keyword + RAG/claim retrieval → router selects `qa` model → synth.
5. Result wrapped as `AskResult(answer, citations=[slug,...])` → serialized to MCP tool result.
6. No wiki mutation. Curiosity/log side-effects: **off** for remote reads (don't pollute the owner's
   curiosity profile with a remote agent's questions) — gated by a `source="mcp"` flag.

## Security considerations

- **Default safe:** stdio transport touches no network. Remote transport is **opt-in** and
  **fail-closed**: `--transport http` with no `MYMEM_MCP_TOKEN` refuses to start (mirrors the
  dashboard `DASHBOARD_PIN` → 503 pattern).
- **Read-only Phase 1:** no tool writes; a compromised token can read, not corrupt.
- **Scopes ready for Phase 2:** `auth.py` returns a scope (`READ`/`WRITE`); write tools register only
  under `WRITE`. A read token cannot reach contribute tools.
- **Inbound content (Phase 2):** `propose_*` runs `mymem/security/` scan + path-traversal guard before
  `reconcile_source_claims`; provenance records the caller's peer/agent id (feeds KBT source-trust).
- **Secrets:** token from `.env` only; never `config.yaml`, never committed; never logged.
- **SSRF/rate-limit:** unchanged from today's deferral; remote transport documented as "trusted-network
  only" until the backlog hardening lands. Read tools don't fetch user URLs, so SSRF surface is flat.

## Performance considerations

- `search_wiki`/`ask` reuse existing retrieval — same latency profile as `/api/query`; no new index.
- Search returns **stubs** to bound caller-context size; bodies fetched on demand.
- Server is stateless per request; no caching layer in Phase 1 (retrieval is already fast enough).

## API / tool-schema contract

FastMCP generates JSON Schema from the typed Python signatures + pydantic/dataclass returns. Example
`get_page` result (OKF concept payload):
```json
{
  "uri": "okf://concept/multi-head-attention.md",
  "frontmatter": {
    "type": "tech", "title": "Multi-Head Attention",
    "description": "Parallel attention heads...", "timestamp": "2026-04-08",
    "tags": ["attention","transformers"],
    "id": "01J...", "domain": "tech", "sources": ["attention-is-all-you-need.md"]
  },
  "body": "# Multi-Head Attention\n\n... [Self-Attention](/self-attention.md) ..."
}
```
(Extension keys `id`/`domain`/`sources` preserved per ADR-016 — identity-stable round trip.)

## Testing strategy

- **Unit:** each tool/resource handler called directly with a `tmp_path` wiki fixture; assert OKF
  payload shape + conformance via `okf/conformance`. Mock `llm_fn` for `ask` (no live LLM — project
  rule). Auth: token present/absent/wrong → allow/fail-closed.
- **Integration:** `build_mcp_server()` in-process; enumerate tools/resources; invoke through FastMCP's
  in-memory client if available (no socket).
- **Regression:** full existing suite stays green; FastAPI routes untouched.
- **Coverage:** ≥ 80% on `interop/mcp/` (target 100% on `auth.py` + `payloads.py` — pure logic).

## Phase 2 / 3 forward hooks (designed now, not built)

- **Phase 2 (contribute):** add `propose_claim` / `propose_page` under the `WRITE` scope → flow through
  `pipeline/compounding.reconcile_source_claims` (ADD/MERGE/SUPERSEDE) with `provenance.peer_id`. Zero
  new pipeline — remote contributions are just another ingest source.
- **Phase 3 (A2A federation + sync):** new sibling `mymem/interop/a2a/` — Agent Card at
  `/.well-known/agent.json` advertising skills (search/ask/contribute); peer registry
  (`data/peers.db`); periodic OKF bundle pull (reuse `import okf`) + live A2A delegation ("ask my
  peer"); conflict resolution rides the bi-temporal claims ledger with `peer_id:page_id` namespacing.
