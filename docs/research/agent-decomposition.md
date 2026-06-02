# Research: Decomposing MyMem Pipeline into Agents

**Date**: 2026-06-01  
**Question**: How should the monolithic ingest/query/eval/introspect pipelines be broken into discrete, parallelisable, specialised agents?

---

## Framework Evaluation

### Ranked 1–5 for MyMem's constraints (strict mypy, async-first, injectable LLM, no global state)

| Rank | Framework | Key Reason |
|------|-----------|-----------|
| 1 | **PydanticAI** | Async-first, zero hidden globals, Pydantic v2 types = perfect mypy, injectable via `model_fn` |
| 2 | LangGraph | Mature, graph-based, good typing — but forces graph-upfront design, heavier lock-in |
| 3 | CrewAI | Clean role model — but async is bolted on, rigid task composition |
| 4 | AutoGen | Overkill; conversational model doesn't fit deterministic pipeline |
| 5 | Raw asyncio | Full control — but rebuilds observability, fallback, cost tracking already in ModelRouter |

**Winner: PydanticAI** — define agents as pure `async def` returning typed frozen dataclasses, compose with Python 3.11 `TaskGroup`, inject `ModelRouter` as dependency. No framework magic.

---

## Recommended Message-Passing Pattern

**Dataflow DAG + Python 3.11 `asyncio.TaskGroup`**

- Each agent is a node with typed input/output frozen dataclasses
- Parent `TaskGroup` scopes ensure structured concurrency — parent doesn't exit until children complete or are explicitly cancelled
- Background tasks (eval, RAG indexing) run in isolated `TaskGroup` scopes → their failures do NOT cascade to foreground tasks
- Replaces the current `asyncio.ensure_future()` footguns with explicit scopes

```python
@dataclass(frozen=True)
class SourceMessage:
    source: str
    source_text: str
    source_type: str

@dataclass(frozen=True)
class ExtractionResult:
    ideas: tuple[dict, ...]
    source_name: str

async def ingest_workflow(msg: SourceMessage, router: ModelRouter) -> IngestResult:
    # Step 1: parallel security + chunking
    async with asyncio.TaskGroup() as tg:
        security_task = tg.create_task(security_agent(msg))
        chunks_task   = tg.create_task(chunker_agent(msg))

    # Step 2: extraction (sequential per chunk, parallel across chunks via TaskGroup)
    extraction = await extraction_agent(chunks_task.result(), router)

    # Step 3: compilation (parallel per idea) + background eval (isolated)
    async with asyncio.TaskGroup() as tg:
        compile_tasks = [tg.create_task(page_compiler_agent(idea, router))
                         for idea in extraction.ideas]
        tg.create_task(_eval_background(extraction))  # failure isolated

    return IngestResult(pages=[t.result() for t in compile_tasks])
```

---

## Current Pipeline → Agent Mapping

### Ingest Pipeline

| Agent | Input | Output | Model | Parallelisable |
|-------|-------|--------|-------|----------------|
| **SourceReaderAgent** | `str` (path/URL) | `SourceMessage` | None | Yes (with security scan) |
| **SecurityScannerAgent** | `str` (raw text) | `SecurityResult` | None (rule-based) | Yes (with reading) |
| **ChunkerAgent** | `str` (raw text) | `list[str]` chunks | None (deterministic) | Yes |
| **IdeaExtractorAgent** | chunk `str` + metadata | `ExtractionResult` | `compile` model | No (sequential per chunk) |
| **PageCompilerAgent** | idea `dict` + source text | `WikiPage` | `compile` model | **Yes (per idea)** |
| **IndexerAgent** | `WikiPage` | `IndexEntry` | None | Yes |
| **RAGIndexerAgent** | page body / PDF | embeddings in DB | `embed` model | Yes (fire-and-forget) |
| **EvalAgent** | `ExtractionResult` | consensus score | reference model | Yes (background, isolated) |

**Key coupling to fix**: IdeaExtractor → PageCompiler currently runs sequentially. Extract all ideas first, then fan-out compilations in a `TaskGroup`.

### Query Pipeline

| Agent | Input | Output | Model | Parallelisable |
|-------|-------|--------|-------|----------------|
| **QuerySanitizerAgent** | `str` (question) | `SanitizedQuery` | None | N/A (always inline) |
| **IndexSearchAgent** | `SanitizedQuery` | `list[IndexEntry]` | None (BM25) | **Yes — parallel with RAG** |
| **RAGRetrieverAgent** | `SanitizedQuery` | `list[tuple[str, str]]` | `embed` model | **Yes — parallel with Index** |
| **PageLoaderAgent** | `list[IndexEntry]` | `list[tuple[str, str]]` | None (I/O) | Yes |
| **AnswerSynthesizerAgent** | question + context | `str` answer | `qa` model | No (waits on retrieval) |
| **AnswerSaverAgent** | answer + citations | saved `Path` | None | Yes (optional) |

**Key coupling to fix**: `IndexManager.search()` and `_fetch_rag_context()` run sequentially. Both can start immediately from the query — run in parallel `TaskGroup`, merge results.

### Eval Pipeline

| Agent | Input | Output | Model | Parallelisable |
|-------|-------|--------|-------|----------------|
| **WikiQualityAgent** | wiki dir `Path` | `WikiQualityReport` | None | **Yes** |
| **ChunkAblationAgent** | sample text `str` | `ChunkingReport` | None | **Yes** |
| **RetrievalEvalAgent** | test cases + wiki dir | `RetrievalReport` | None (BM25) | **Yes** |
| **RagasAgent** | sampled `WikiPage` list | `list[RagasResult]` | LLM judge | **Yes per page** |
| **ConfidenceScorerAgent** | `WikiPage` | `ConfidenceState` | None (heuristic) | **Yes** |

**All eval agents are currently sequential — they should all run in a single `TaskGroup`.**

### Introspect Pipeline

| Agent | Input | Output | Model | Parallelisable |
|-------|-------|--------|-------|----------------|
| **CuriosityTrackerAgent** | event (domain, tags) | weight decay update | None (SQLite) | Yes |
| **DailySummarizerAgent** | log entries + wiki pages | summary markdown | `introspect` model | No (depends on log read) |
| **SuggestionRankerAgent** | curiosity weights + wiki | ranked recommendations | weights + heuristic | Yes (with summariser) |

---

## Existing Clean Boundaries (Keep As-Is)

These are already well-decoupled — they map cleanly to agents with minimal change:

- `has_high_severity_secret()` + `sanitize_for_prompt()` → SecurityScannerAgent
- `ChunkSplitter.split()` → ChunkerAgent  
- `IndexManager` CRUD → IndexerAgent
- `asyncio.ensure_future()` for RAG indexing → upgrade to `TaskGroup` scope only
- `log_curiosity_event()` → CuriosityTrackerAgent

---

## Final Agent Design: 4 Agents + 2 Background Subagents

The micro-agent-per-stage design was rejected. The correct model is:

| Type | Name | LLM | Capabilities |
|------|------|-----|--------------|
| Agent | `IngestAgent` | compile | 5 tool calls: read, scan, extract, write, index |
| Agent | `QueryAgent` | qa | 5 tool calls: search_index, search_rag, load_pages, synthesize, save |
| Agent | `EvalAgent` | judge (optional) | 4 tool calls: wiki_quality, retrieval_eval, ragas_judge, confidence |
| Agent | `IntrospectAgent` | introspect | 4 tool calls: read_log, curiosity_weights, summarize, rank |
| Subagent | `RagIndexSubagent` | embed | background only — no tool calling |
| Subagent | `ExtractionEvalSubagent` | reference | background only — no tool calling |

**Why tool calls replace micro-agents**: `@ingest_agent.tool def read_source(...)` IS the SourceReaderAgent. The LLM calls it at runtime. No separate agent class, no message passing, no orchestration boilerplate.

**Why background subagents are NOT agents**: RAG indexing and extraction eval do not require LLM reasoning — they execute deterministically once triggered. They are plain `async def` functions wrapped in `_run_background()`, not PydanticAI agents.

## Priority Order for Migration

| Priority | Change | Why |
|----------|--------|-----|
| P0 | Parallel Index + RAG search in `query.py` | Direct latency win on every query |
| P0 | Parallel page compilation in `ingest.py` | `TaskGroup` fan-out per idea |
| P1 | Parallel eval modules in `runner.py` | All independent; currently serialised |
| P1 | Replace `ensure_future()` with `TaskGroup` + `_run_background()` | Structured cancellation |
| P2 | `mymem/agents/` package with PydanticAI tool calls | Typed tool contracts per agent |
| P3 | Cron-triggered `IntrospectAgent` (autonomous) | Self-directed wiki maintenance |
