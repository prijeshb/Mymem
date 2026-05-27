# Research: MyMem Wiki as Agent Memory Layer

Date: 2026-05-27
Branch: V1-0003

---

## Core Question

Is the MyMem wiki useful for internal agents as-is, or does it need structural changes?

**Short answer**: Useful today as a *read-only* knowledge source. Needs changes to become a proper *agent memory layer* — meaning agents can also write to it, update it, and traverse it as a graph.

---

## How Agents Use Memory — Taxonomy

Research (2025–2026) identifies four memory types agents need:

| Memory type | What it stores | MyMem today |
|---|---|---|
| **Semantic** | Facts, concepts, domain knowledge | ✅ Core use case — wiki pages |
| **Episodic** | Past interactions, what happened when | ✅ Partial — `wiki/daily/`, log.md |
| **Procedural** | How to do tasks, reusable workflows | ❌ Not stored |
| **Working** | Current task context, scratchpad | ❌ In-context only, not persisted |

MyMem covers semantic and partially episodic. Procedural and working memory are absent.

---

## What Works For Agents Today (No Changes)

### 1. Read tool over `/api/page/:slug`
An agent can call the existing FastAPI endpoint to load a wiki page into context. The structured frontmatter (title, domain, tags, sources, backlinks) gives the agent metadata to reason about without parsing the body.

```
GET /api/page/transformer-architecture-fundamentals
→ { title, body, domain, tags, backlinks, toc }
```

### 2. Search / retrieval tool over `/api/query` (SSE)
The streaming query endpoint is already agent-compatible — it returns an LLM-synthesized answer with cited pages. An agent can call this as a `search_wiki` tool.

### 3. Wikilink graph as semantic navigation
`GET /api/graph` returns nodes + typed edges. An agent can traverse from a concept to related concepts without embedding search — pure graph traversal. This is what the 2025 Agent-as-a-Graph paper calls "type-specific traversal."

### 4. Domain filter as agent routing
The `domain` field (tech, finance, spiritual, etc.) lets a routing agent pre-filter the knowledge space before retrieval — matching what enterprise RAG calls "metadata-filtered retrieval."

### 5. Curiosity engine as agent interest model
`GET /api/curiosity` returns domain + tag weights with trend direction. An agent can use this to decide *what to research next* — which gaps to fill, which areas are underrepresented — without the human having to direct it.

---

## What Needs to Change

### 1. Agent-writable memory (highest impact)

Currently agents can only read. To make the wiki an agent memory layer, agents need to write:

```
POST /api/page          → create a new page from agent output
PATCH /api/page/:slug   → update an existing page with new findings
POST /api/ingest-text   → exists, but no "this is agent output, not a source" flag
```

The problem: agent-written pages should be tagged differently from human-ingested sources. Need a `origin: agent` field in frontmatter so agents can distinguish their own prior outputs from source material.

### 2. Procedural memory store

Agents need to store *how to do things* — not facts, but workflows, patterns, and tool chains. Current wiki has no category for this. Needed:

- A `procedures/` subdomain or `domain: procedure` tag
- Pages like `how-to-debug-rag-retrieval.md` or `weekly-ingest-workflow.md`
- The LLM should be able to call `save_procedure(name, steps)` and later `recall_procedure(name)`

### 3. Structured entity extraction (ontology layer)

The wikilink graph connects pages but doesn't encode *how* they're related. Research (AriGraph, 2025) shows agents benefit from typed relationships:

```
[Attention Mechanism] --is-a--> [Neural Network Component]
[Transformer] --uses--> [Attention Mechanism]
[BERT] --derives-from--> [Transformer]
```

The `ontology` layer is already planned in CLAUDE.md (`mymem/pipeline/ontology.py`) but not built. This is the biggest single upgrade for agent use — it enables multi-hop reasoning without loading all pages into context.

### 4. Agent-scoped episodic memory

`wiki/daily/` captures human-facing summaries. Agents need their own episodic store:
- What queries did I run? What did I learn?
- What did I fail to find? (negative cache)
- What was the quality of my last answer on this topic?

This can be a lightweight SQLite table alongside `curiosity.db` — no need for a new wiki page per agent session.

### 5. Forget / expiry mechanism

Agents need to be able to mark knowledge as stale or retract it. Currently `archive` is the closest. Need:
- `confidence` score per page (partially built in `mymem/evals/confidence.py`)
- Auto-downgrade to `draft` state when a page hasn't been reinforced in N days
- Agent-callable `retract(slug, reason)` for contradiction handling

---

## What to Build: Minimal Agent Interface

To expose MyMem as a proper agent memory tool, add these five tool-callable endpoints:

| Tool name | Endpoint | Description |
|---|---|---|
| `search_wiki` | `POST /api/query` | Hybrid retrieval — semantic Q&A with citations |
| `read_page` | `GET /api/page/:slug` | Load full page into context |
| `browse_graph` | `GET /api/graph?from=:slug&depth=2` | Traverse wikilink neighbors |
| `remember` | `POST /api/remember` | Agent writes a new page or appends to existing |
| `recall_procedure` | `GET /api/procedure/:name` | Fetch a stored workflow |

These five cover the core agentic memory operations: recall, retrieve, traverse, write, and reuse.

---

## MCP Server as the Right Interface

The cleanest way to expose this to agents is as an **MCP (Model Context Protocol) server**. Each of the five tools above becomes an MCP tool definition. Any Claude agent (or other MCP-compatible agent) can then `use_mcp_tool("mymem", "search_wiki", ...)` without knowing FastAPI exists.

Benefits:
- Agents treat the wiki as a first-class tool, not an HTTP client
- Tool schemas are self-describing — agents know what arguments each operation takes
- Compatible with Claude Code, Claude Desktop, and any MCP-compatible agent framework

Implementation: add `mymem/mcp/server.py` — a thin wrapper that maps MCP tool calls to existing FastAPI route logic.

---

## Multi-Agent Use: Shared vs. Private Memory

For a team of agents (e.g., a researcher agent + a writer agent + a reviewer agent), the wiki can serve as **shared semantic memory**:

```
Researcher agent → ingest findings → wiki pages
Writer agent     → read wiki pages → draft content
Reviewer agent   → read + compare pages → flag contradictions
```

This matches the "Intrinsic Memory Agents" pattern (2025): heterogeneous agents with different roles sharing a structured contextual memory store. The key requirement is the `origin: agent` field — so agents know which pages were produced by other agents vs. primary sources.

---

## Comparison: MyMem vs. Dedicated Agent Memory Solutions

| Capability | MyMem (today) | Cloudflare Agent Memory | Mem0 | MemGPT |
|---|---|---|---|---|
| Semantic memory (facts) | ✅ Full wiki | ✅ | ✅ | ✅ |
| Episodic memory | ✅ Partial (daily log) | ✅ | ✅ | ✅ |
| Procedural memory | ❌ | ❌ | ❌ | ✅ |
| Wikilink graph traversal | ✅ Unique | ❌ | ❌ | ❌ |
| Human-readable / editable | ✅ Markdown | ❌ | ❌ | ❌ |
| LLM-synthesized (not raw chunks) | ✅ Unique | ❌ | ❌ | ❌ |
| Self-hosted | ✅ | ❌ | Partial | ✅ |
| MCP-native | ❌ (to build) | ❌ | ❌ | ❌ |

MyMem's unique strengths: synthesized wiki pages (not raw chunks), human-readable/editable output, and wikilink graph traversal. These are genuinely differentiated from dedicated memory services.

---

## Recommended Build Order

1. **`origin` field in WikiPage** — tag agent-written pages; prevents confusion with source material
2. **`POST /api/remember`** — agent write endpoint (wraps ingest-text with `origin: agent`)
3. **`GET /api/graph?from&depth`** — depth-limited graph traversal for multi-hop agent reasoning
4. **`mymem/mcp/server.py`** — MCP server exposing 5 tools; unlocks any Claude agent
5. **Ontology layer** (`mymem/pipeline/ontology.py`) — typed relationships; enables multi-hop reasoning without loading full pages

Items 1–3 are small (< 1 day each). Item 4 makes the whole system agent-native. Item 5 is the long-term differentiator.

---

## Sources

- [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [LLM Wiki v2 with agent memory lessons](https://gist.github.com/rohitg00/2067ab416f7bbe447c1977edaaa681e2)
- [Agentic RAG Survey (2025)](https://arxiv.org/abs/2501.09136)
- [Graph-based Agent Memory: Taxonomy and Techniques](https://arxiv.org/html/2602.05665v1)
- [Agent-as-a-Graph: Tool and Agent Retrieval](https://arxiv.org/html/2511.18194v1)
- [Intrinsic Memory Agents: Heterogeneous Multi-Agent Systems](https://arxiv.org/html/2508.08997v1)
- [ProcMEM: Procedural Memory for LLM Agents](https://arxiv.org/pdf/2602.01869)
- [Episodic Memory is the Missing Piece for Long-Term LLM Agents](https://arxiv.org/pdf/2502.06975)
- [IWE Context Bridge: Agentic RAG + Graph Traversal](https://www.marktechpost.com/2026/03/27/an-implementation-of-iwes-context-bridge-as-an-ai-powered-knowledge-graph-with-agentic-rag-openai-function-calling-and-graph-traversal/)
