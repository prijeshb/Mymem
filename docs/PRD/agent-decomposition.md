# PRD: Pipeline Agent Decomposition

## Problem Statement

MyMem's ingest, query, eval, and introspect pipelines are monolithic sequential functions. Each stage blocks the next even when stages are independent. As the wiki grows, this produces slow ingest (single-threaded LLM compilation per idea), slow queries (index and RAG search run serially), and long eval cycles (all eval modules run in sequence). The system also uses `asyncio.ensure_future()` for background tasks — a footgun with no structured cancellation or failure isolation.

## Goals

- G1: Parallel page compilation during ingest — N ideas compile concurrently instead of serially
- G2: Parallel index search + RAG retrieval during query — both start simultaneously, merged before synthesis
- G3: Parallel eval modules — wiki quality, chunking, retrieval, RAGAS all run in one `TaskGroup`
- G4: Structured concurrency everywhere — replace `ensure_future()` with `asyncio.TaskGroup` scopes
- G5: Each agent is independently testable with injected LLM mock

## Non-Goals

- Distributed agents across processes or machines (stay in-process for now)
- Real-time streaming agent-to-agent communication
- Full PydanticAI framework adoption in V1-0005 (incremental — typed messages and TaskGroup first)
- Autonomous self-directing agents (V2 feature)

## User Stories

- As a user, I want `mymem ingest` on a multi-concept article to complete faster, so that I don't wait for sequential per-idea LLM calls
- As a user, I want `mymem query` to return answers faster because index and RAG searches run simultaneously
- As a developer, I want to run `mymem eval` and have all eval modules run concurrently with a progress bar
- As a developer, I want to write a test for a single agent (e.g. PageCompilerAgent) by injecting a mock LLM without wiring the full pipeline

## Acceptance Criteria

- [ ] Ingest: ideas extracted from a source are compiled as concurrent `TaskGroup` tasks, not a sequential loop
- [ ] Query: `IndexManager.search()` and `_fetch_rag_context()` start in the same `TaskGroup`
- [ ] Eval: `run_evals()` launches all enabled eval modules concurrently
- [ ] All background tasks use `TaskGroup` with isolated failure handling (eval failure does not fail ingest)
- [ ] Each agent function has its own unit test with `llm_fn` mock
- [ ] `mypy --strict` passes on all new agent modules
- [ ] No regression in existing test suite (125 tests)

## Success Metrics

- Ingest wall-clock time on a 5-concept source: target < 50% of current time
- Query latency for RAG-enabled queries: target < 60% of current time
- All eval modules complete in parallel; wall-clock ≈ slowest module, not sum of all
- Zero new `ensure_future()` calls; all background work in `TaskGroup` scopes

## Timeline

- Research: done (2026-06-01)
- Phase 1 — TaskGroup + parallel agents (P0+P1): 2–3 days
- Phase 2 — Typed message dataclasses + PydanticAI wrapping: 2–3 days
- Testing: 1 day
- **Total estimate: 5–7 days**

## Dependencies

- Python 3.11+ `asyncio.TaskGroup` (already available in project)
- PydanticAI (optional for Phase 2): `pip install pydantic-ai`
- Existing `ModelRouter` — remains unchanged, injected into agents as dependency

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Concurrent LLM calls exceed Ollama's throughput | Medium | Medium | Cap `TaskGroup` with `asyncio.Semaphore(max_concurrent=3)` |
| TaskGroup exception propagation cancels sibling tasks | Medium | Medium | Wrap background agents in `try/except` inside their task; never let them raise |
| Increased complexity makes debugging harder | Low | Medium | Structured logging already traces by run_id; each agent logs its own spans |
| Race condition on `index.md` when parallel writes | Low | High | IndexManager writes are already atomic; serialise index writes with a per-wiki lock |
