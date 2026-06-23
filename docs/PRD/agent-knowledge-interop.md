# PRD: Agent-to-Agent Knowledge Interop (MCP + OKF, A2A later)

> Slug: `agent-knowledge-interop` · Priority: **P1** · Status: Proposed
> Research: docs/research/agent-knowledge-interop.md · Architecture: docs/architecture/agent-knowledge-interop.md · ADR-017

## Problem Statement

MyMem can already *publish* knowledge as a static OKF bundle (ADR-016), but a running agent — another
MyMem instance, or an external agent like Claude Code / Cursor — cannot reach a MyMem wiki **live** to
search it, read a page, or ask it a question. There is no standard access channel, and no path for one
agent to contribute knowledge to another's wiki. MyMem's knowledge is a silo with a file export.

## Goals

- **G1** — Expose the MyMem wiki over **MCP** so any MCP client can `search`, `get_page`, `ask`, and
  list `knowledge_gaps` against it, **with tool payloads formatted as OKF concepts** (markdown+YAML).
- **G2** — Reuse existing retrieval/synthesis/OKF code; **no new retrieval logic** — the server is a
  thin adapter over `query_wiki`, `list_pages`, `okf/_map`, `graph/gaps`.
- **G3** — Work both **locally** (stdio transport → Claude Desktop/Code, zero config) and **remotely**
  (Streamable HTTP/SSE → bearer-token gated, opt-in), preserving the current local-first security
  posture by default.
- **G4** — Architect tool registry + auth scopes so **Phase 2 (contribute)** and **Phase 3 (A2A
  federation / bidirectional sync)** are additive — no rewrite of Phase 1.

## Non-Goals (this branch)

- Write/contribute tools (Phase 2 — designed, not built here).
- A2A Agent Card, peer registry, bidirectional sync, conflict resolution across instances (Phase 3).
- A web-UI surface for the MCP server (CLI-first, mirrors the OKF decision).
- Replacing the existing FastAPI `/api/*` JSON routes (MCP is additive, not a migration).
- Network-hardening backlog (SSRF allowlist, rate-limit middleware) beyond the bearer-token gate —
  tracked separately; remote transport ships **opt-in + documented** until those land.

## User Stories

- As a **developer using Claude Code**, I add MyMem as an MCP server so the agent can answer from my
  personal wiki while I work, with citations back to wiki pages.
- As an **external agent**, I call `search_wiki("multi-head attention")` and get OKF concept stubs I
  can render or ingest, then `get_page` for the full concept.
- As a **second MyMem instance** (Phase 3 foundation), I discover a peer's capabilities and `ask` it a
  question my own wiki can't answer.
- As the **wiki owner**, I run `mymem mcp serve` and, by default, nothing is network-exposed; enabling
  remote access requires an explicit flag + a token from `.env`.

## Acceptance Criteria

- [ ] **AC1** — `mymem mcp serve` starts an MCP server over **stdio** by default; a real MCP client
      (e.g. Claude Code) can connect and list the tools.
- [ ] **AC2** — `search_wiki(query, domain?, limit?)` returns ranked **OKF concept stubs**
      (title, description, slug, domain, score) — no full bodies — reusing `pipeline/query.py`
      retrieval.
- [ ] **AC3** — `get_page(slug|id)` returns the full page as an **OKF concept** (frontmatter+body via
      `okf/_map.to_okf_frontmatter` + `wikilinks_to_markdown`), identity-stable (ULID preserved).
- [ ] **AC4** — `ask(question, domain?)` returns a synthesized answer **with citations**, reusing
      `query_wiki()`; LLM calls go through the router (no direct `llm.py`).
- [ ] **AC5** — `list_concepts(domain?, tag?)` and `knowledge_gaps(limit?)` reuse `list_pages` and
      `graph/gaps.rank_gaps` respectively.
- [ ] **AC6** — Resources `okf://index` and `okf://concept/{slug}` serve the same OKF content as the
      tools, as read-only context.
- [ ] **AC7** — `mymem mcp serve --transport http --port N` requires `MYMEM_MCP_TOKEN` (from `.env`);
      missing token → **fail-closed** (refuse to start remote, like the dashboard PIN 503 pattern).
- [ ] **AC8** — All tools are **read-only**; no code path mutates the wiki. (Write tools are Phase 2.)
- [ ] **AC9** — ≥ 80% test coverage on `mymem/interop/mcp/`; tools tested without a live MCP client
      (call the underlying handlers directly) and without live LLM/Ollama (inject/mock `llm_fn`).
- [ ] **AC10** — `fastmcp` added to `pyproject.toml`; `pytest` green; strict mypy clean.

## Success Metrics

- A Claude Code session with MyMem attached answers a wiki question end-to-end (manual smoke).
- Tool-result payloads validate against the OKF v0.1 conformance check already in
  `okf/conformance.py` (concept frontmatter is spec-conformant).
- Zero regressions: existing 950-test suite stays green; no change to FastAPI routes.
- `search_wiki` p50 latency ≈ existing `/api/query` retrieval (it's the same code path).

## Timeline (estimate)

- Research: **done**.
- Phase 1 development (this branch): server module + 5 tools + 2 resources + CLI + auth gate + tests.
- Phase 2 (later branch): contribute tools through compounding ingest.
- Phase 3 (later): A2A Agent Card + federation + sync.

## Dependencies

- **New:** `fastmcp` (Apache-2.0, Python ≥3.10) — only new runtime dep.
- **Internal (reused):** `pipeline/query.py`, `wiki/page.py`, `knowledge/okf/`, `graph/gaps.py`,
  `pipeline/router/`, `security/` (Phase 2), `config.py`.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Remote transport widens attack surface | Med | High | stdio/localhost default; remote opt-in + bearer token; read-only Phase 1; fail-closed on missing token |
| Prompt injection via served page content | Med | Med | Document "served content is data"; Phase 2 inbound runs `security/` scanner before reconcile |
| Tool results flood caller context | Med | Med | Search returns stubs only; full body via `get_page`/resource (OKF index-vs-concept split) |
| `fastmcp` API churn (young, fast-moving) | Med | Low | Pin version; isolate behind `interop/mcp/` adapter so a bump is one-module |
| A2A vs ACP not yet settled (Phase 3) | High | Low | Defer coordination layer; Phase 1/2 deliver value with zero A2A commitment |
| Identity collisions across instances (Phase 3) | Med | Med | Namespace provenance `peer_id:page_id`; designed now, enforced at federation |
