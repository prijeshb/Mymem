# Test Reference — MCP Access Layer (ADR-017, Phase 1)

> Module: `mymem/interop/mcp/` · Tests: `tests/test_interop_mcp.py` (22 tests)
> Status: 21 passed + 1 skipped without `fastmcp`; **all 22 pass** with `pip install "mymem[mcp]"`.
> Coverage: pure handlers 97–100% (`auth`/`context`/`resources` 100%); `server.py` covered by the
> smoke test only when `fastmcp` is installed.

This is a future-reference map of **what each function does** and **what each test pins down**, so a
later change can be checked against intended behavior without re-reading every file.

---

## 1. Source modules — function summaries

### `payloads.py` — wire types (frozen dataclasses, each has `as_dict()`)
| Symbol | What it is |
|--------|-----------|
| `ConceptStub` | Lightweight search/list result: `title, slug, domain, description, score`. **Never carries the page body** (keeps caller context small). |
| `ConceptPayload` | A full page as an **OKF v0.1 concept**: `uri, frontmatter (dict), body`. Same shape `mymem export okf` emits. |
| `AskResult` | Synthesized answer: `question, answer, citations[]`. |
| `GapItem` | A referenced-but-unwritten concept: `concept, inbound_refs`. |

### `auth.py` — remote bearer gate (fail-closed)
| Symbol | What it does |
|--------|-----------|
| `Scope` | `StrEnum` — `READ` (Phase 1) / `WRITE` (reserved for Phase 2 contribute tools). |
| `AuthError` | Raised on any auth failure. |
| `check_token(provided, expected) -> Scope` | Constant-time (`hmac.compare_digest`) bearer check. **Raises if `expected` is falsy** (no token configured → fail-closed for remote) or if `provided` is missing/mismatched. Returns `Scope.READ` on success. Pure — env read happens in the CLI. |
| `extract_bearer(headers) -> str\|None` | Pulls the token from a case-insensitive `Authorization: Bearer <token>` header (tolerates a bare token). `None` if absent. |
| `authorize_request(headers, expected) -> Scope` | Per-request gate: **no headers** (stdio/in-memory, not network-reachable) → allow; **HTTP headers present** → require a valid bearer via `check_token`. |

### `middleware.py` — per-request auth (HTTP transport only; imports `fastmcp`)
| Symbol | What it does |
|--------|-----------|
| `BearerAuthMiddleware(expected_token)` | FastMCP middleware; `on_request` reads the `Authorization` header (`get_http_headers(include_all=True)`) and calls `authorize_request` → rejects unauthenticated requests. Attached by `build_mcp_server` only when `auth_token` is set (ADR-017 F3). |

### `context.py` — request context
| Symbol | What it does |
|--------|-----------|
| `WikiContext` | Frozen bundle: `wiki_dir, index_path, log_path, graph_db, rag_db, router`. Makes every handler a pure function of `(ctx, args)`. |
| `context_from_settings(settings, *, router=None)` | Derives the context from app `Settings` (mirrors `cli._paths`): `graph_db`/`rag_db` sit next to `settings.paths.db`. |

### `tools.py` — the 5 read handlers (all delegate to existing internals; none mutate the wiki)
| Function | What it does | Delegates to |
|----------|--------------|--------------|
| `_first_paragraph(body)` | First non-heading, non-empty line → `description`, with `[[wikilinks]]` flattened to plain text (ADR-017 F1; shared `okf/_links.flatten_wikilinks`). Mirrors the exporter. | `okf/_links` |
| `search_wiki(ctx, query, *, domain, limit)` | Ranked `ConceptStub`s (no bodies). Returns `[]` if `index.md` is missing. Domain-filters by `IndexEntry.domain`. | `IndexManager.search` |
| `_resolve_page(ctx, ref)` | Resolve a page by **slug**, **ULID id**, or **slugified title**. | `read_page` / `list_pages` |
| `get_page(ctx, ref) -> ConceptPayload\|None` | Full page as an OKF concept: frontmatter via `to_okf_frontmatter`, body `[[wikilinks]]`→OKF md links. Identity (`id`) preserved. `None` if unresolved. | `okf/_map`, `okf/_links` |
| `list_concepts(ctx, *, domain, tag)` | All concepts as stubs, optional domain/tag filter. | `list_pages` |
| `knowledge_gaps(ctx, *, limit)` | Referenced-but-unwritten concepts ranked by inbound refs. | `graph/gaps.knowledge_gaps` |
| `ask(ctx, question, *, domain)` | Answer + citations. **Raises `ValueError` if `ctx.router` is None.** Calls the query pipeline with `save=False`; adds RAG only if `rag.db` exists. | `pipeline/query.query_wiki` |

### `resources.py` — OKF read-context resources
| Function | What it does |
|----------|--------------|
| `okf_index(ctx)` | OKF `index.md` directory listing (no frontmatter, spec form). |
| `okf_concept(ctx, slug) -> str\|None` | A single OKF concept file (frontmatter + body), or `None` if not found. |

### `server.py` — FastMCP wiring (only module importing `fastmcp`; lazy import)
| Function | What it does |
|----------|--------------|
| `build_mcp_server(ctx, *, name="mymem")` | Registers 5 tools (`search_wiki`, `get_page`, `list_concepts`, `knowledge_gaps`, `ask`) + 2 resources (`okf://index`, `okf://concept/{slug}`) as thin wrappers returning plain dicts/strings. Raises `ImportError` with an install hint if `fastmcp` is absent. |

---

## 2. Test-by-test summary (`tests/test_interop_mcp.py`)

**Fixtures:** `wiki` builds a 2-page temp wiki (`Self Attention` → links `Multi Head Attention`) + a
matching `index.md`; `ctx` wraps it with temp `graph.db`/`rag.db`; `_FakeRouter` returns a canned
answer so `ask` runs without a live LLM.

| # | Test | Pins down |
|---|------|-----------|
| 1 | `test_payload_as_dict_roundtrip` | `ConceptStub`/`AskResult`/`GapItem` serialize to the exact expected dicts. |
| 2 | `test_check_token_ok` | Matching token → `Scope.READ`. |
| 3 | `test_check_token_fail_closed_when_unset` | `expected=None` **and** `expected=""` → `AuthError` (the remote fail-closed guard). |
| 4 | `test_check_token_rejects_mismatch_and_missing` | Wrong token and `provided=None` → `AuthError`. |
| 4a | `test_extract_bearer_parses_header` | `Bearer x`/`bearer x`/bare `x` → `x`; `{}` → `None`. |
| 4b | `test_authorize_request_local_allows_without_token` | Empty headers (stdio/in-memory) → `Scope.READ`, even with a token configured. |
| 4c | `test_authorize_request_http_requires_valid_token` | HTTP headers: valid token → READ; wrong/missing token → `AuthError`; token unset server-side → `AuthError`. |
| 5 | `test_search_wiki_returns_stubs` | Returns `ConceptStub`s; slug=`self-attention`, domain=`tech`. |
| 6 | `test_search_wiki_domain_filter_excludes` | `domain="finance"` → `[]`. |
| 7 | `test_search_wiki_missing_index_returns_empty` | No `index.md` → `[]` (no crash). |
| 8 | `test_get_page_returns_okf_payload` | OKF `type`/`title`, **`id` preserved**, `uri`, and body wikilink → `[Multi Head Attention](/multi-head-attention.md)`. |
| 9 | `test_get_page_by_ulid_id` | Resolves a page by its ULID id. |
| 10 | `test_get_page_unknown_returns_none` | Unknown ref → `None`. |
| 11 | `test_list_concepts_all` | Returns both page titles. |
| 12 | `test_list_concepts_tag_filter` | `tag="transformers"` → only the MHA page. |
| 13 | `test_list_concepts_domain_filter` | `domain="finance"` → `[]`. |
| 14 | `test_knowledge_gaps_missing_db` | No `graph.db` → `[]`. |
| 15 | `test_knowledge_gaps_ranks_pageless_entities` | Hand-built graph db → `GapItem("Rotary Embeddings", 2)`. |
| 16 | `test_ask_synthesizes_with_fake_router` | `ask` returns the fake answer + a real citation (`Self Attention`). |
| 17 | `test_ask_requires_router` | `ctx.router=None` → `ValueError`. |
| 18 | `test_okf_index_resource` | Index markdown lists both pages as OKF links. |
| 19 | `test_okf_concept_resource` | Concept file starts with `---` and has `title: Self Attention`. |
| 20 | `test_okf_concept_resource_missing` | Unknown slug → `None`. |
| 21 | `test_context_from_settings_derives_paths` | `index/log/graph/rag` paths derived correctly; `router` defaults `None`. |
| 22 | `test_build_mcp_server_smoke` | Builds a real FastMCP server (skipped without `fastmcp`). |

---

## 3. How to run

```bash
pip install -e ".[dev,mcp]"                       # mcp extra installs fastmcp
pytest tests/test_interop_mcp.py -q               # this module
pytest tests/test_interop_mcp.py --cov=mymem.interop --cov-report=term-missing
ruff check mymem/interop && mypy mymem/interop    # lint + types (yaml-stub note below)
```

> `mypy mymem/interop` reports one `import-untyped` for `yaml` — a **pre-existing baseline** shared
> with `mymem/knowledge/okf/` (the dev env lacks `types-PyYAML`); not specific to this module.
