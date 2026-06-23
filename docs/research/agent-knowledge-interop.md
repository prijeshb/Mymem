# Research: Agents Talking to Each Other's Wiki / Docs (MCP + OKF, A2A later)

> Raw topic: "I want agents talking to each others wiki or docs through okf or mcp or both
> combined or other ways." Clean slug: `agent-knowledge-interop`.
> Date: 2026-06-22 · Status: research complete, plan proposed.

## 1. The question, sharpened

MyMem already passes knowledge as **files** (ADR-016: `mymem export/import okf` — a static OKF v0.1
bundle). That is snapshot interchange. The open question is one layer up:

> How do **running agents** reach each other's *live* knowledge — query it, and later contribute
> to it and sync — over a standard channel?

Clarified scope (from the requester):

| Axis | Decision |
|------|----------|
| **Topology** | Both: MyMem↔MyMem federation **and** external agents (Claude, Cursor, custom) consuming MyMem |
| **Access mode** | Phase 1 **read/query only**; Phase 2 contribute; Phase 3 full bidirectional sync |
| **Channel** | **MCP (live) + OKF (payload format) combined** now; **A2A / other** as a later channel |
| **Priority** | **P1** — next branch up |

## 2. Key finding — the industry converged on a two-layer stack

The 2026 consensus across MCP-vs-A2A analyses is a **two-layer reference architecture**, explicitly
likened to TCP (transport) vs HTTP (application):

```
┌─────────────────────────────────────────────┐
│  A2A  — agent ↔ agent coordination layer      │  (Google, Apr 2025; "ask my peer", delegation)
├─────────────────────────────────────────────┤
│  MCP  — agent ↔ tool/resource access layer    │  (Anthropic; "read/search this knowledge")
├─────────────────────────────────────────────┤
│  OKF  — the knowledge payload format           │  (Google Cloud, 2026; markdown+YAML concept files)
└─────────────────────────────────────────────┘
```

- **MCP** governs how an agent talks to **tools/data**: a server exposes **Resources** (read-only
  context, like GET) and **Tools** (actions, like POST). This is exactly "let an agent read/query a
  wiki."
- **A2A** governs how **agents talk to each other**: capability discovery via an **Agent Card**
  (`/.well-known/agent.json`), task delegation, multi-agent coordination. This is "MyMem instance A
  asks MyMem instance B."
- Both are now governed by the **Agentic AI Foundation (AAIF)** under the **Linux Foundation**
  (146 members incl. Anthropic, Google, OpenAI, Microsoft, AWS) — these are stable open standards,
  not vendor lock-in. Google ADK, Salesforce Agentforce, and ServiceNow Now Assist each implement
  **both**.

**Implication for MyMem:** the requester's "MCP + OKF combined now, A2A later" is not a hedge — it
*is* the reference stack, adopted in the same order the industry layers it. MCP first (access), A2A
later (coordination), with OKF as the wire format the MCP tools return.

## 3. Where MyMem already sits

MyMem has the **content layer** essentially done; it is missing the **access** and **coordination**
layers.

| Layer | Standard | MyMem today | Gap |
|-------|----------|-------------|-----|
| Payload format | OKF v0.1 | ✅ `mymem/knowledge/okf/` — export/import, 99% cov (ADR-016) | none — reuse as wire schema |
| Retrieval | — | ✅ hybrid keyword+vector (`pipeline/query.py`), cross-page claim index (`knowledge/claim_index.py`), gaps (`graph/gaps.py`) | wrap, don't rebuild |
| Synthesis | — | ✅ `query_wiki()` answer + citations | wrap as a tool |
| **Access channel** | **MCP** | ❌ none (FastAPI `/api/*` is bespoke JSON, not MCP) | **build (Phase 1)** |
| Contribution | — | ✅ compounding ingest `reconcile.py` ADD/MERGE/SUPERSEDE (ADR-011/015) | expose as a gated tool (Phase 2) |
| **Coordination** | **A2A** | ❌ none | **build (Phase 3)** |
| Identity/provenance | — | ✅ ULID page id (ADR-013/014), bi-temporal claims, per-claim provenance | extend provenance with peer/source id |

The punchline: MyMem is ~1 module away from being MCP-reachable, because every tool the server needs
to expose is **already an internal function** — `query_wiki`, `list_pages`, `to_okf_frontmatter`,
`wikilinks_to_markdown`, `rank_gaps`, and (Phase 2) `reconcile_source_claims`.

## 4. Prior art & reusable building blocks

### Protocol SDKs (recommended dependency)
- **FastMCP** (`fastmcp` on PyPI) — the de-facto Pythonic MCP framework; builds on Anthropic's
  reference `python-sdk`. Declares a tool from a typed Python function (schema/validation/docs
  auto-generated from type hints + pydantic — fits MyMem's strict-mypy, frozen-dataclass style).
  Supports **stdio** (local clients like Claude Desktop/Code) **and remote transports**
  (SSE / Streamable HTTP) with built-in **auth middleware**.
  - Latest **v3.4.2** (2026-06-06), **Apache-2.0**, requires **Python ≥3.10** → compatible with
    MyMem's 3.11+. No conflict with existing deps; pure-Python.
- **A2A** (Phase 3) — Google's `a2a` Python SDK / spec; Agent Card + task lifecycle. Defer until the
  federation phase; evaluate against ACP at that point (convergence is ongoing).

### Reference servers to mirror (markdown-knowledge-over-MCP)
These are the closest analogs — a markdown knowledge base exposed over MCP. (Star counts / exact
names to be confirmed at build time — `gh` CLI was unavailable in the research shell.)
- **basic-memory** (basicmachines-co) — local-first **markdown** knowledge graph exposed over MCP;
  agents read **and write** notes. The nearest design twin to MyMem; study its read-vs-write tool
  split and its provenance handling for Phase 2.
- **mcp-obsidian / obsidian-mcp** — expose an Obsidian vault (search + read notes) over MCP. MyMem
  already has an Obsidian projection (ADR-004), so the tool surface is familiar.
- **mcp-server-qdrant / memory servers** — vector-retrieval-over-MCP pattern; informs the
  `search_wiki` tool shape (query → ranked snippets).
- Anthropic reference **`servers`** repo (`modelcontextprotocol/servers`) — canonical resource/tool
  patterns and the `memory` server.

### Pattern lift
- Expose **Resources** for stable reads (`okf://index`, `okf://concept/{slug}`) and **Tools** for
  parameterized actions (`search_wiki`, `ask`, `get_page`, `knowledge_gaps`).
- **Return OKF, not bespoke JSON** — the MCP tool result body is an OKF concept (frontmatter+body),
  so "MCP + OKF combined" is literal: OKF is the schema, MCP is the channel. Any OKF-aware consumer
  understands the payload regardless of how it was fetched.

## 5. Known failure modes / gotchas

1. **Network exposure re-opens deferred security gaps.** MyMem is local-first by design; its audit
   (PASS, 2026-06-11) explicitly defers SSRF allowlisting and write-endpoint rate limiting as
   "acceptable for local deployment." A *remote* MCP transport changes that threat model. Mitigation:
   **stdio/localhost by default**, remote transport opt-in + bearer-token gated, read tools only in
   Phase 1, write tools (Phase 2) behind a separate scope + the existing security scanner on inbound
   content.
2. **Prompt-injection via served content.** A remote agent ingesting MyMem pages inherits any
   injected instructions in the markdown. Treat served content as data; the consumer's problem, but
   document it. For inbound (Phase 2), run `mymem/security/` scan before reconcile.
3. **Tool-result size blowups.** Returning full pages for a broad `search_wiki` floods the caller's
   context. Return ranked **stubs** (title + description + slug + score) from search; full body only
   via `get_page`/resource. Mirrors the OKF index-vs-concept split.
4. **Identity drift across instances (Phase 3).** ULIDs are unique per instance; two MyMems will not
   share ids. Federation must namespace provenance by peer (`peer_id:page_id`) so supersede/merge
   across instances is well-defined and never silently clobbers a local claim.
5. **A2A/ACP still converging.** Don't hard-commit the coordination layer now; Phase 1/2 give value
   with zero A2A risk. Re-evaluate A2A vs ACP at the Phase 3 gate.
6. **Auth secret management.** Remote bearer token from `.env` only (never `config.yaml`, never
   committed) — consistent with existing MyMem secret rules.

## 6. Recommendation

Build the **MCP access layer returning OKF payloads** as the P1 branch (Phase 1, read/query only),
reusing existing retrieval/synthesis/OKF functions behind a thin `mymem/interop/mcp/` server and a
`mymem mcp serve` CLI. Architect the tool registry and auth scopes so **contribute** (Phase 2, via
the existing compounding pipeline) and **A2A federation** (Phase 3) are additive, not rewrites. Adopt
**FastMCP** as the only new dependency. Keep remote transport opt-in and local-by-default to preserve
the current security posture until the network-exposure backlog items are done.

## Sources
- [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) — official MCP Python SDK (Resources + Tools, stdio/SSE/Streamable HTTP)
- [FastMCP (gofastmcp.com)](https://gofastmcp.com/getting-started/welcome) · [fastmcp on PyPI](https://pypi.org/project/fastmcp/) — v3.4.2, Apache-2.0, Python ≥3.10; remote transports + auth middleware
- [How to Build a Python MCP Server to Consult a Knowledge Base (Auth0)](https://auth0.com/blog/build-python-mcp-server-for-blog-search/) — search-tool-over-knowledge-base pattern
- [awesome-mcp-servers — knowledge management & memory](https://github.com/TensorBlock/awesome-mcp-servers/blob/main/docs/knowledge-management--memory.md) — prior-art catalog
- [MCP vs A2A: AI Agent Protocol Comparison 2026 (Intuz)](https://www.intuz.com/blog/mcp-vs-a2a) — two-layer model
- [Agent Interoperability Protocols 2026: MCP, A2A, ACP and the Path to Convergence (Zylos)](https://zylos.ai/research/2026-03-26-agent-interoperability-protocols-mcp-a2a-acp-convergence/) — AAIF / Linux Foundation governance, convergence
- [MCP vs A2A in 2026: How the AI Protocol War Ends (Dubach)](https://philippdubach.com/posts/mcp-vs-a2a-in-2026-how-the-ai-protocol-war-ends/) — reference architecture
- [Implementing Authentication in a Remote MCP Server with Python and FastMCP](https://gelembjuk.com/blog/post/authentication-remote-mcp-server-python/) — remote auth pattern
