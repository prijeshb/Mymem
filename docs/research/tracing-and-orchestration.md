# Research: Tracing & Agent Orchestration for MyMem

Date: 2026-05-27
Branch: V1-0003

---

## Part 1 — LLM Tracing / Observability

### What we have today

`mymem/observability/` has a structured logger and tracer, and `data/mymem.db` records LLM call cost per task. But there's no span-level trace — no way to see token counts, latency, prompt content, or retrieval quality for a given query end-to-end.

### Options compared

| Tool | Open source | Self-hosted | LangChain dependency | OTel native | Best for |
|---|---|---|---|---|---|
| **Langfuse** | ✅ MIT | ✅ Docker | ❌ none | ✅ | Self-hosted, any framework |
| **LangSmith** | ❌ | Enterprise only | Tight | ❌ | LangChain-heavy stacks |
| **Arize Phoenix** | ✅ Elastic 2.0 | ✅ | ❌ none | ✅ OpenInference | Notebook + eval-heavy |
| **Traceloop** | ✅ | ✅ | ❌ none | ✅ OpenLLMetry | Vendor-neutral OTEL |
| **Helicone** | ✅ | ✅ | ❌ none | ❌ proxy-based | Quick raw LLM logging |
| **Braintrust** | ❌ | ❌ | ❌ | ❌ | Managed eval + tracing |

### Recommendation: Langfuse

**Why Langfuse fits MyMem best:**
- MIT licensed, self-hosted with `docker compose up` — no SaaS data sharing
- No LangChain dependency — integrates with any Python code via `langfuse` SDK or pure OTEL
- Receives traces on `/api/public/otel` (OTLP endpoint) — standard OpenTelemetry spans
- Works directly with the existing `mymem/pipeline/router/` without refactoring
- Gives: prompt content, token usage, latency, cost per call, session grouping, eval scores

### How to integrate into MyMem

**Option A — Langfuse Python SDK (simplest)**

```python
# mymem/observability/tracer.py — add alongside existing logger
from langfuse import Langfuse

langfuse = Langfuse(
    public_key=settings.langfuse_public_key,
    secret_key=settings.langfuse_secret_key,
    host=settings.langfuse_host,  # self-hosted URL
)

# In mymem/pipeline/router/_router.py — wrap every LLM call
trace = langfuse.trace(name="query", session_id=session_id)
span = trace.span(name="llm_call", input=prompt, metadata={"task": task})
result = await llm_call(...)
span.end(output=result, usage={"input": in_tokens, "output": out_tokens})
```

**Option B — Pure OpenTelemetry (more portable)**

```python
# No Langfuse SDK needed — any OTEL backend works
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

tracer = trace.get_tracer("mymem")

with tracer.start_as_current_span("query_wiki") as span:
    span.set_attribute("query", question)
    span.set_attribute("domain", domain)
    result = await query_wiki(...)
    span.set_attribute("pages_used", len(result.pages))
```

Point the OTLP exporter at Langfuse's `/api/public/otel` endpoint — zero vendor lock-in.

### What to trace in MyMem

| Span | Key attributes |
|---|---|
| `ingest_source` | source URL, type, domain, pages_written, chunk_count, duration |
| `router.call` | task, model selected, in_tokens, out_tokens, cost_usd, fallback_used |
| `query_wiki` | query, domain_filter, pages_retrieved, rag_chunks_used, answer_length |
| `rag.embed` | text_length, model, duration |
| `rag.search` | query, k, top_score, results_count |
| `introspect` | date, pages_read, recommendations_generated |

This gives end-to-end visibility: from user query → retrieval → LLM call → response, with cost and latency at every step.

### Self-hosting Langfuse

```bash
# docker-compose.yml addition
langfuse:
  image: langfuse/langfuse:latest
  ports: ["3000:3000"]
  environment:
    DATABASE_URL: postgresql://...
    NEXTAUTH_SECRET: ...
    SALT: ...
```

Or use the pre-built `docker compose` from the Langfuse repo — spins up in ~2 minutes.

---

## Part 2 — LangGraph for Agent Orchestration

### What LangGraph is

LangGraph is a **stateful graph-based orchestration framework** from LangChain Inc. It models agent workflows as a directed graph where:
- **Nodes** = Python functions (LLM calls, tools, decisions)
- **Edges** = control flow (conditional branching, loops)
- **State** = a typed dict shared across all nodes, checkpointed after every step

Key feature: **built-in checkpointing** — if a step fails, resume from the last saved state. This is what separates it from plain async Python.

### LangGraph does NOT require LangChain

You can use LangGraph with any Python function as a node — including MyMem's existing `ingest_source`, `query_wiki`, `introspect`. No LangChain abstractions needed.

### Where LangGraph would fit in MyMem

**Use case 1: Multi-step ingest pipeline as a resumable graph**

Currently `ingest_source` is one big async function. As a LangGraph graph:

```
fetch_source → security_scan → chunk → embed → write_wiki_pages → update_index → log
```

Each node is checkpointed. If embedding fails halfway through a large document, it resumes from the last completed page — no full re-ingest.

**Use case 2: Agentic research loop**

```
receive_query
  → search_wiki        (retrieve relevant pages)
  → evaluate_coverage  (is the answer complete?)
  → [if gap] web_search → ingest_new_source → search_wiki  (loop)
  → synthesize_answer
  → [if --save] write_wiki_page
```

This is the "self-improving wiki" pattern — the agent finds gaps, fills them, and answers. Impossible to build cleanly without a state machine.

**Use case 3: Multi-agent eval runner**

```
orchestrator
  ├─ wiki_quality_agent   (runs in parallel)
  ├─ retrieval_eval_agent (runs in parallel)
  └─ chunking_eval_agent  (runs in parallel)
  → aggregate_results → save_report
```

LangGraph's `Send` API allows true fan-out to parallel subgraphs.

### LangGraph vs keeping plain async Python

| Aspect | Plain async (today) | LangGraph |
|---|---|---|
| Resumability on failure | ❌ full restart | ✅ checkpoint per node |
| Conditional branching | Ad-hoc if/else | ✅ typed conditional edges |
| Parallel subgraphs | Manual asyncio.gather | ✅ Send API |
| Human-in-the-loop pause | ❌ | ✅ interrupt_before |
| Visual debugging | ❌ | ✅ LangGraph Studio |
| Overhead | None | ~200ms cold start, small dep |
| Complexity | Low | Medium — graph design required |

**When to add LangGraph**: when any of these are true:
- A pipeline has >3 steps that can fail independently and need resuming
- You need conditional branching based on LLM output (e.g. "is coverage sufficient?")
- You want a human approval step (interrupt_before)
- You want parallel subgraph execution with fan-out

**When plain async is fine**: single-path pipelines with no branching, short scripts, eval runners.

### Minimal integration pattern (no LangChain)

```python
# pip install langgraph
from langgraph.graph import StateGraph, END
from typing import TypedDict

class IngestState(TypedDict):
    source: str
    raw_text: str
    chunks: list[str]
    pages_written: int

graph = StateGraph(IngestState)

graph.add_node("fetch",  fetch_node)    # wraps existing fetch logic
graph.add_node("chunk",  chunk_node)    # wraps ChunkSplitter
graph.add_node("write",  write_node)    # wraps write_page

graph.set_entry_point("fetch")
graph.add_edge("fetch", "chunk")
graph.add_edge("chunk", "write")
graph.add_edge("write", END)

# Checkpointing (SQLite — already a dep)
from langgraph.checkpoint.sqlite import SqliteSaver
checkpointer = SqliteSaver.from_conn_string("data/langgraph.db")

app = graph.compile(checkpointer=checkpointer)
await app.ainvoke({"source": url})
```

Total new code: ~30 lines wrapping existing functions.

---

## Recommendation

| Decision | Recommendation | Effort |
|---|---|---|
| Tracing | **Langfuse** self-hosted via Docker + OTEL spans in `router/_router.py` | ~1 day |
| Orchestration now | Keep plain async — current pipelines are single-path | 0 |
| Orchestration next | Add **LangGraph** for the agentic research loop (query → gap-fill → answer) | ~2 days |

Start with Langfuse tracing — it's low-risk (additive, no refactor), gives immediate visibility into which models are slow/expensive, and the OTEL spans feed directly into the existing `mymem.db` cost data for a richer dashboard.

Add LangGraph only when building the agentic research loop or multi-step human-in-the-loop ingest — those are the cases where its checkpointing and branching are genuinely needed.

---

## Sources

- [Langfuse vs LangSmith comparison](https://langfuse.com/faq/all/langsmith-alternative)
- [Langfuse OpenTelemetry integration](https://langfuse.com/integrations/native/opentelemetry)
- [Langfuse GitHub (MIT)](https://github.com/langfuse/langfuse)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph without LangChain — Real Python](https://realpython.com/langgraph-python/)
- [LangGraph vs alternatives — ZenML](https://www.zenml.io/blog/langgraph-alternatives)
- [LangSmith alternatives — ZenML](https://www.zenml.io/blog/langsmith-alternatives)
- [Arize Phoenix](https://www.braintrust.dev/articles/best-ai-observability-platforms-2025)
