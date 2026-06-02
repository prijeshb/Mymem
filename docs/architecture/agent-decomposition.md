# System Design: Pipeline Agent Decomposition

## Overview

Four top-level agents replace the monolithic pipeline functions. Each agent owns an LLM + a set of typed tool functions the LLM can call. Background work (RAG indexing, extraction eval) runs as two lightweight subagents spawned by `IngestAgent` via isolated `TaskGroup` scopes. All agents receive dependencies via PydanticAI's `RunContext`; no global state.

---

## Agent Count: 4 agents + 2 background subagents

```
IngestAgent          — orchestrates source → wiki page flow
  tools: read_source, scan_security, extract_ideas,
         write_wiki_page, update_index
  background subagents (isolated TaskGroup, failures swallowed):
    RagIndexSubagent     — embed + store new/updated pages
    ExtractionEvalSubagent — consensus eval vs reference model

QueryAgent           — retrieval + answer synthesis
  tools: search_index, search_rag, load_pages,
         synthesize_answer, save_answer

EvalAgent            — wiki health + retrieval quality
  tools: check_wiki_quality, run_retrieval_eval,
         run_ragas_judge, score_confidence

IntrospectAgent      — daily digest + curiosity recommendations
  tools: read_log, get_curiosity_weights,
         generate_summary, rank_suggestions
```

---

## Architecture Diagram

```
CLI / Web API
      │
      ▼
IngestAgent  (LLM: compile model)
      │  calls tools sequentially/conditionally based on source type
      ├── read_source(source, type)          → raw text
      ├── scan_security(text)               → sanitized text | BLOCK
      ├── extract_ideas(text, source_meta)  → list[Idea]
      ├── write_wiki_page(idea) × N         → WikiPage  ← TaskGroup (parallel)
      └── update_index(pages)               → IndexEntry[]
            │
            └── TaskGroup (background, isolated):
                  ├── RagIndexSubagent(pages)
                  └── ExtractionEvalSubagent(ideas, source_text)

CLI / Web API
      │
      ▼
QueryAgent  (LLM: qa model)
      │
      ├── TaskGroup: search_index(q) ∥ search_rag(q)   ← parallel
      ├── load_pages(index_entries)                     ← parallel I/O
      ├── synthesize_answer(question, context)          ← LLM
      └── save_answer(answer, citations)                ← optional

CLI / Web API
      │
      ▼
EvalAgent  (LLM: judge model, optional)
      │
      └── TaskGroup (all parallel):
            ├── check_wiki_quality(wiki_dir)
            ├── run_retrieval_eval(cases, wiki_dir)
            ├── run_ragas_judge(pages) × N              ← LLM, semaphore-capped
            └── score_confidence(pages)

CLI / Web API
      │
      ▼
IntrospectAgent  (LLM: introspect model)
      │
      ├── TaskGroup: read_log(date) ∥ get_curiosity_weights()
      ├── generate_summary(log_entries, wiki_pages)     ← LLM
      └── rank_suggestions(weights, wiki_pages)
```

---

## PydanticAI Agent Structure

### Dependencies (injected via RunContext)

```python
# mymem/agents/deps.py
from dataclasses import dataclass
from pathlib import Path
from mymem.pipeline.router import ModelRouter

@dataclass
class IngestDeps:
    router: ModelRouter
    wiki_dir: Path
    index_path: Path
    log_path: Path
    curiosity_db: Path
    db_path: Path           # for RAG + traces

@dataclass
class QueryDeps:
    router: ModelRouter
    wiki_dir: Path
    index_path: Path
    log_path: Path
    rag_db_path: Path | None

@dataclass
class EvalDeps:
    wiki_dir: Path
    data_dir: Path
    router: ModelRouter | None   # None when LLM judge disabled

@dataclass
class IntrospectDeps:
    router: ModelRouter
    wiki_dir: Path
    log_path: Path
    curiosity_db: Path
```

### Tool call pattern (PydanticAI)

```python
# mymem/agents/ingest_agent.py
from pydantic_ai import Agent, RunContext
from mymem.agents.deps import IngestDeps
from mymem.pipeline.ingest import IngestResult

ingest_agent: Agent[IngestDeps, IngestResult] = Agent(
    model="ollama:gemma4:12b",        # overridden by router at call time
    deps_type=IngestDeps,
    result_type=IngestResult,
    system_prompt=(
        "You are a knowledge ingestion agent. "
        "Given a source, extract key ideas and write them as wiki pages. "
        "Use your tools in order: read → scan → extract → write → index."
    ),
)

@ingest_agent.tool
async def read_source(
    ctx: RunContext[IngestDeps],
    source: str,
    source_type: str,
) -> str:
    """Read raw text from a file path, URL, or YouTube video ID."""
    from mymem.pipeline.ingest import _read_source   # reuse existing impl
    return await _read_source(source, source_type)

@ingest_agent.tool
async def scan_security(
    ctx: RunContext[IngestDeps],
    text: str,
) -> str:
    """Scan text for secrets/injection. Returns sanitized text or raises on HIGH risk."""
    from mymem.security.scanner import has_high_severity_secret
    from mymem.security.sanitize import sanitize_for_prompt
    if has_high_severity_secret(text):
        raise ValueError("HIGH severity secret detected — ingest blocked")
    return sanitize_for_prompt(text)

@ingest_agent.tool
async def extract_ideas(
    ctx: RunContext[IngestDeps],
    text: str,
    source_name: str,
    source_type: str,
    max_concepts: int = 3,
) -> list[dict]:
    """Extract key ideas from source text using the compile model."""
    # calls ctx.deps.router — no direct LLM call; router handles fallback + cost
    ...

@ingest_agent.tool
async def write_wiki_page(
    ctx: RunContext[IngestDeps],
    idea: dict,
    source_text: str,
) -> str:
    """Compile one idea into a wiki page. Returns slug. Call in parallel for multiple ideas."""
    ...

@ingest_agent.tool
async def update_index(
    ctx: RunContext[IngestDeps],
    slugs: list[str],
) -> None:
    """Update index.md and log.md after pages are written."""
    ...
```

### Tool calls the LLM chooses at runtime

The LLM calls tools based on the source type. For a YouTube video it calls `read_source` with `source_type="youtube"`; for a PDF it skips extraction and calls only `update_index` (PDF is RAG-only). The agent decides — not hardcoded if-chains.

---

## Background Subagents

These are **not** PydanticAI agents — they are plain `async def` functions in isolated `TaskGroup` scopes. They use the same `ModelRouter` but have no LLM-directed tool calling.

```python
# mymem/agents/background.py

async def rag_index_subagent(
    pages: list[WikiPage],
    deps: IngestDeps,
) -> None:
    """Embed and store new/updated wiki pages in the RAG vector DB."""
    ...

async def extraction_eval_subagent(
    ideas: list[dict],
    source_text: str,
    source_name: str,
    deps: IngestDeps,
) -> None:
    """Run extraction consensus eval against reference model. Saves to evals.db."""
    ...

async def _run_background(coro: Coroutine[Any, Any, None]) -> None:
    """Wrap a background subagent — log and swallow all errors."""
    try:
        await coro
    except Exception as exc:
        log.warning("Background subagent failed", error=str(exc))
```

Usage in `IngestAgent`'s orchestrating layer (called after agent run completes):

```python
async with asyncio.TaskGroup() as tg:
    tg.create_task(_run_background(rag_index_subagent(pages, deps)))
    tg.create_task(_run_background(extraction_eval_subagent(ideas, text, name, deps)))
```

---

## Concurrency Controls

```python
# Semaphore injected into any tool that calls router.call()
# Prevents saturating Ollama with concurrent requests
LLM_SEM = asyncio.Semaphore(3)

# Per-wiki-dir lock for index.md writes
# Prevents race when parallel write_wiki_page calls all finish near-simultaneously
_INDEX_LOCKS: dict[Path, asyncio.Lock] = {}
```

---

## File Layout

```
mymem/
  agents/
    __init__.py
    deps.py              # IngestDeps, QueryDeps, EvalDeps, IntrospectDeps
    messages.py          # frozen dataclass results (IngestResult etc.)
    ingest_agent.py      # IngestAgent + 5 tools
    query_agent.py       # QueryAgent + 5 tools
    eval_agent.py        # EvalAgent + 4 tools
    introspect_agent.py  # IntrospectAgent + 4 tools
    background.py        # rag_index_subagent, extraction_eval_subagent, _run_background
  pipeline/
    ingest.py            # thin: builds IngestDeps, calls ingest_agent.run()
    query.py             # thin: builds QueryDeps, calls query_agent.run()
    ...
```

**6 files total in `agents/`** — one per agent + deps + messages + background subagents.

---

## API Contract (unchanged)

All existing CLI commands and web endpoints are unchanged. `pipeline/ingest.py` becomes:

```python
async def ingest_source(source: str, *, wiki_dir, ..., router) -> IngestResult:
    deps = IngestDeps(router=router, wiki_dir=wiki_dir, ...)
    result = await ingest_agent.run(
        f"Ingest this source: {source}",
        deps=deps,
    )
    return result.data
```

---

## Testing Strategy

```python
# tests/agents/test_ingest_agent.py
from pydantic_ai.models.test import TestModel

async def test_ingest_agent_writes_page(tmp_path):
    deps = IngestDeps(router=mock_router, wiki_dir=tmp_path / "wiki", ...)

    # TestModel controls which tools the agent calls and with what args
    with ingest_agent.override(model=TestModel()):
        result = await ingest_agent.run("Ingest test.md", deps=deps)

    assert result.data.pages_written  # at least one page written
```

- Each tool function is testable independently (pure async functions)
- `TestModel` from `pydantic_ai.models.test` replaces the LLM for full agent tests
- Background subagents tested independently from agents
