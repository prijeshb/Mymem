"""MCP access layer for the MyMem wiki (ADR-017, Phase 1 — read-only).

Exposes the wiki as MCP Tools (`search_wiki`, `get_page`, `ask`, `list_concepts`,
`knowledge_gaps`) and Resources (`okf://index`, `okf://concept/{slug}`). Every handler
delegates to an existing internal function and returns OKF-formatted payloads, so the
MCP channel carries the same standard format MyMem already exports (ADR-016).

Design split (so the logic is testable without a live MCP client or LLM):
- `payloads`  — frozen wire dataclasses
- `context`   — paths + router bundle
- `tools`     — pure handler functions (the unit-tested core)
- `resources` — OKF read-context renderers
- `auth`      — bearer-token gate for remote transport (fail-closed)
- `server`    — thin FastMCP registration (the only module importing `fastmcp`)
"""
