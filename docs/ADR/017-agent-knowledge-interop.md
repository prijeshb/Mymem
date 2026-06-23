# ADR 017: Agent-to-Agent Knowledge Interop (MCP access layer + OKF payloads; A2A later)

## Status: Accepted (Phase 1 implemented in V1-0015)

**Date:** 2026-06-22 · **Priority:** P1
**Relates to:** ADR-016 (OKF interchange — provides the payload format), ADR-013/014 (stable page
identity), ADR-011/015 (compounding ingest — the Phase 2 contribute path), ADR-004 (external
integrations), ADR-005 (agent decomposition).
**Research:** docs/research/agent-knowledge-interop.md · **PRD:** docs/PRD/agent-knowledge-interop.md ·
**Architecture:** docs/architecture/agent-knowledge-interop.md

## Context

ADR-016 made MyMem knowledge portable as a **static OKF file bundle** (`export/import okf`). That is
snapshot interchange — it does not let a *running* agent reach a *live* MyMem wiki to search it, read a
page, or ask it a question, nor let one agent contribute to another's wiki.

The requester wants agents to talk to each other's wiki/docs, scoped as: **both** topologies
(MyMem↔MyMem federation **and** external-agent consumption); **read/query first**, then contribute,
then bidirectional sync; channel **MCP + OKF combined now**, **A2A/other later**; **P1**.

The 2026 industry consensus is a **two-layer reference architecture** (governed by the Linux
Foundation's Agentic AI Foundation): **MCP** as the agent↔tool/data **access** layer, **A2A** as the
agent↔agent **coordination** layer, with a knowledge **payload format** underneath. MyMem already owns
the payload layer (OKF, ADR-016) and all retrieval/synthesis logic; it lacks the access and
coordination layers. The requester's phasing *is* that reference stack, adopted bottom-up.

## Decision

Adopt the **two-layer stack incrementally**, OKF as the wire format throughout:

1. **Phase 1 (this branch, P1) — MCP access layer, read-only.** New thin package
   `mymem/interop/mcp/` (FastMCP) exposing Tools `search_wiki`, `get_page`, `ask`, `list_concepts`,
   `knowledge_gaps` and Resources `okf://index`, `okf://concept/{slug}`. **Every handler delegates to
   an existing internal function** — no new retrieval/synthesis logic. **Tool payloads are OKF v0.1
   concepts** (reuse `knowledge/okf/`), so "MCP + OKF combined" is literal: OKF is the schema, MCP is
   the channel. CLI: `mymem mcp serve` (**stdio default**, local; **HTTP/SSE opt-in**, bearer-token
   gated, **fail-closed**).
2. **Phase 2 (later) — contribute.** Add `propose_claim`/`propose_page` under a `WRITE` auth scope that
   flow through the existing compounding-ingest pipeline (`reconcile_source_claims`, ADR-011/015) with
   `provenance.peer_id` and a `security/` scan on inbound content. Remote contributions are just
   another ingest source — no new pipeline.
3. **Phase 3 (later) — A2A federation + bidirectional sync.** Sibling `mymem/interop/a2a/` with an
   Agent Card, peer registry, OKF-bundle pull (reuse `import okf`), and live delegation; conflict
   resolution rides the bi-temporal claims ledger with `peer_id:page_id` namespacing. Re-evaluate A2A
   vs ACP at this gate.
4. **Keep storage and FastAPI routes unchanged.** MCP is additive, not a migration; native identity
   (ADR-013), claims (ADR-015), and graph (ADR-007/008) stay authoritative.
5. **Dependency:** add **`fastmcp`** (Apache-2.0, Python ≥3.10) — the only new runtime dep; isolated
   behind `interop/mcp/` so version churn is one-module.

## Rationale

- **The access layer is ~1 thin module away.** Every tool the MCP server needs is already an internal
  function (`query_wiki`, `list_pages`, `to_okf_frontmatter`, `wikilinks_to_markdown`, `rank_gaps`).
  Highest interop payoff per unit of effort — mirrors the ADR-016 "substrate already matches" logic.
- **OKF-as-payload makes MCP and OKF reinforce, not compete.** What flows over the live channel is the
  same standard format MyMem already exports. Any OKF-aware consumer understands it.
- **Bottom-up phasing matches the industry layering and de-risks.** MCP (stable, Anthropic) ships
  value now; A2A (still converging with ACP) is deferred until it must exist, with zero Phase-1 risk.
- **Security posture preserved by default.** stdio/localhost default + opt-in fail-closed remote keeps
  the local-first audit (PASS) intact; read-only Phase 1 means a leaked token reads, never corrupts.
- **Forward-compatible by construction.** Auth scopes (`READ`/`WRITE`) and a delegating tool registry
  make Phase 2/3 additive — no rewrite.

## Alternatives Considered

1. **Extend OKF file exchange only (scheduled export/import + fetch-remote-bundle)** — rejected as the
   end state. Reuses ADR-016 with no new runtime surface, but it is batch/stale, has no live query or
   synthesis, and no path to contribute/coordinate. Folded in as the Phase 3 *sync* mechanism, not the
   primary channel.
2. **Bespoke authed HTTP API for federation (extend FastAPI `/api/*`)** — rejected. MyMem already has
   JSON routes, but they are non-standard; external agents would need a custom client. MCP gives every
   MCP-capable agent (Claude Code/Desktop, Cursor, ADK) zero-integration access for free.
3. **A2A first / A2A-only** — rejected for now. A2A is the *coordination* layer; without the *access*
   layer beneath it there is nothing to coordinate over, and A2A vs ACP is still converging. Right
   layer, wrong order — scheduled as Phase 3.
4. **Make OKF/MCP the native storage and retrieval** — rejected (consistent with ADR-016 #1). MyMem's
   identity/claims/graph exceed OKF v0.1; the access layer is a projection, not a replacement.
5. **Skip FastMCP, hand-roll on the reference `python-sdk`** — rejected. FastMCP auto-generates
   tool schemas from typed signatures (fits strict-mypy/frozen-dataclass style) and ships remote
   transport + auth middleware; hand-rolling adds maintenance for no gain. Pin the version; isolate it.

## Consequences

- **Positive:** any MCP agent can query a MyMem wiki live; MyMem becomes a first-class node in the
  MCP/A2A ecosystem; reuses ~all existing retrieval/OKF code; one new pure-Python dep; storage and
  HTTP API untouched; clean additive path to contribute + federate.
- **Negative / tradeoffs accepted:** a new protocol surface to keep conformant as the wiki evolves;
  `fastmcp` is young and may churn (isolated to one package); remote transport, when enabled, widens
  the threat model beyond local-first.
- **Risks:** network exposure re-opens the deferred SSRF/rate-limit gaps (mitigated: stdio default,
  opt-in fail-closed token, read-only Phase 1, "trusted-network only" doc); prompt-injection via served
  content (mitigated: content-is-data doc; Phase-2 inbound security scan); cross-instance identity
  collisions (mitigated by `peer_id:page_id` namespacing, designed now).

## Revisit when

- Phase 1 ships → assess demand for Phase 2 contribute tools (gate: a real second consumer exists).
- Phase 3 planning → re-evaluate **A2A vs ACP** maturity and convergence before committing the
  coordination layer.
- `fastmcp` major version bump or MCP spec revision → re-pin and re-check the adapter.
- Remote transport moves past trusted-network use → pull the SSRF-allowlist + rate-limit backlog items
  forward as a hard prerequisite.
- OKF v0.2 lands (ADR-016 revisit) → bump the payload mapping the MCP tools return.
