# ADR 005: Pipeline Agent Decomposition Strategy

## Status: Proposed

## Context

MyMem's `ingest_source()`, `query_wiki()`, `run_evals()`, and `introspect()` are monolithic sequential async functions. As the wiki grows:

1. Ingest of a multi-concept article blocks on sequential per-idea LLM compilations
2. Query blocks on sequential keyword → vector search when both could start simultaneously
3. Eval modules run serially when all are independent
4. Background tasks use `asyncio.ensure_future()` — no structured cancellation, silent failures

The codebase is ready for decomposition: strict mypy, frozen dataclasses, injectable `llm_fn`, and `ModelRouter` abstraction are all already in place.

## Decision

**4 agents + 2 background subagents. No more.**

| Agent | LLM | Tools |
|-------|-----|-------|
| `IngestAgent` | compile model | `read_source`, `scan_security`, `extract_ideas`, `write_wiki_page`, `update_index` |
| `QueryAgent` | qa model | `search_index`, `search_rag`, `load_pages`, `synthesize_answer`, `save_answer` |
| `EvalAgent` | judge model (optional) | `check_wiki_quality`, `run_retrieval_eval`, `run_ragas_judge`, `score_confidence` |
| `IntrospectAgent` | introspect model | `read_log`, `get_curiosity_weights`, `generate_summary`, `rank_suggestions` |
| `RagIndexSubagent` | embed model | background, no tool calling |
| `ExtractionEvalSubagent` | reference model | background, no tool calling |

**Phase 1 — Structured concurrency** (no new files, pure stdlib):
- Replace sequential compilation loops in `ingest.py` with `TaskGroup`
- Replace `ensure_future()` with `TaskGroup` + `_run_background()` wrapper
- Parallelise `IndexManager.search()` + `_fetch_rag_context()` in `query.py`
- Parallelise all eval modules in `runner.py`

**Phase 2 — PydanticAI agent package** (new `mymem/agents/` — 6 files):
- `deps.py` — typed dependency classes (IngestDeps, QueryDeps, EvalDeps, IntrospectDeps)
- `ingest_agent.py` — IngestAgent + 5 tools
- `query_agent.py` — QueryAgent + 5 tools
- `eval_agent.py` — EvalAgent + 4 tools
- `introspect_agent.py` — IntrospectAgent + 4 tools
- `background.py` — 2 background subagents + `_run_background()` wrapper

**Framework: PydanticAI** (Phase 2 only — Phase 1 is pure stdlib).

## Rationale

### Why TaskGroup over `ensure_future()`

`ensure_future()` has no structured lifetime — tasks outlive their parent scope silently, exceptions surface on the event loop's exception handler (not in the calling code), and there is no clean cancellation path. Python 3.11 `TaskGroup` fixes all three: parent scope waits for children, first exception cancels siblings, explicit `_run_background()` wrapper isolates failures by catching and logging them before they propagate.

### Why PydanticAI over LangGraph/CrewAI

PydanticAI was chosen because:
1. **Zero hidden state** — no singleton registries, no implicit LangChain config objects
2. **Strict mypy** — Pydantic v2 types; LangGraph has `Any` leakage in node state types
3. **Tool calls are first-class** — `@agent.tool` decorator produces typed functions the LLM can call at runtime; no manual dispatch tables
4. **Incremental** — agents are `async def` + tools; adoption is file-by-file
5. **No provider lock-in** — injects `ModelRouter` as dependency; doesn't enforce its own LLM client

LangGraph was rejected because its graph-upfront design requires all nodes and edges defined before the first run — MyMem's pipeline branches dynamically (e.g. split on source length, RAG optional). CrewAI was rejected because its async support is not first-class. Raw asyncio was not chosen because PydanticAI adds typed tool-call contracts without any new runtime dependencies beyond Pydantic (already in the project).

### Why 4 agents, not 14 micro-agents

The first design had a micro-agent for every pipeline stage (SourceReaderAgent, SecurityScannerAgent, ChunkerAgent, etc.). This is wrong for two reasons:

1. **Tool calls replace micro-agents**: In PydanticAI, the LLM decides which tools to call and in what order at runtime. A `read_source` tool replaces a `SourceReaderAgent`. The agent itself is the reasoning unit; tools are its capabilities.

2. **Background work is not an agent**: RAG indexing and extraction eval don't need LLM reasoning — they just run. They're `async def` subagents, not PydanticAI agents. Calling them "agents" overstates their intelligence and adds unnecessary overhead.

### Why frozen dataclasses as messages

MyMem already uses frozen-like dataclasses for `IngestResult`, `QueryResult`, `EvalReport`. Adding `frozen=True` and using them as inter-agent messages enforces immutability (required by coding style), enables structural typing in mypy, and makes test fixtures trivially constructible. No additional serialisation library is needed — messages are in-process only.

### Why keep `pipeline/` public API unchanged

The CLI (`mymem ingest`, `mymem query`, `mymem eval`) and all web routes call `ingest_source()`, `query_wiki()`, and `run_evals()` directly. Changing these signatures would break 20+ call sites. The agent decomposition is an internal refactoring: the orchestrators in `pipeline/` thin-wrap the new `agents/` modules. The public API is untouched and all existing tests continue to pass.

## Alternatives Considered

### Replace entire pipeline with LangGraph graph

**Rejected.** Would require rewriting all pipeline functions as graph nodes upfront before any parallelism benefit is visible. Risk of introducing hidden state in LangGraph's `ConfigurableField` system. Estimated 2–3× more migration effort than TaskGroup approach.

### Actor model (asyncio Queue per agent mailbox)

**Rejected.** Adds message persistence, backpressure, and queue management complexity that is unnecessary for in-process, request-scoped workflows. Every ingest request spawns its own agent cluster — there is no shared queue between requests.

### Pure raw asyncio with no framework

**Deferred.** Viable for Phase 1 (TaskGroup is stdlib). PydanticAI in Phase 2 adds typed contracts that raw asyncio cannot enforce. Re-evaluated if PydanticAI license or stability becomes a concern.

## Consequences

**Positive:**
- Ingest wall-clock time drops proportionally to concept count (parallel compilation)
- Query latency drops by ~40% on RAG-enabled queries (parallel search)
- Eval suite completes in time ≈ slowest module, not sum of all
- Background task failures are isolated and logged; foreground results always returned
- Each agent is independently testable — no full pipeline wiring needed

**Negative:**
- `asyncio.TaskGroup` exception semantics require care: first exception cancels siblings. Every background agent must be wrapped in `_run_background()` to avoid cancelling foreground compilation tasks.
- Concurrent Ollama calls may saturate local GPU. Mitigated by `asyncio.Semaphore(3)` guard injected into LLM-calling agents.
- Phase 2 (PydanticAI) adds one new dependency. Mitigated by phasing — Phase 1 is pure stdlib.

**Risks:**
- Index write race condition when parallel compilations finish simultaneously → mitigated by per-wiki-dir `asyncio.Lock`
- Increased agent count makes traces harder to follow → mitigated by existing structured logging with `run_id` per request
