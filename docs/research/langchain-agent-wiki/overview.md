# Research: LangChain Ecosystem for MyMem Agents Wiki

Date: 2026-05-27

---

## Core Question

How can LangChain, LangGraph, LangSmith, and related LangChain products be used in MyMem, especially for turning the wiki into an agent memory layer?

**Short answer**: Use **LangGraph** to orchestrate agents, **LangChain** for tool/model integration, and **LangSmith** for tracing and evaluations. Keep **MyMem's wiki as the actual long-term memory** rather than replacing it with a framework-owned memory store.

---

## Product Map

| Product | What it is | Best use in MyMem |
|---|---|---|
| **LangChain** | High-level agent and LLM application framework with model/tool integrations | Wrap MyMem API functions as tools; reuse model/tool abstractions where helpful |
| **LangGraph** | Low-level orchestration framework for long-running, stateful agents | Implement researcher/writer/reviewer workflows with explicit state, graph edges, retries, and human approval |
| **LangSmith** | Observability and evaluation platform for LLM/agent apps | Trace agent runs, inspect tool calls, evaluate answers, convert failures into regression cases |
| **Deep Agents** | Batteries-included agent harness built on LangGraph | Optional prototyping path for autonomous research agents with planning/subagents |
| **LangSmith Deployment / Agent Server** | Runtime and API for deployed graph/agent applications | Later production path for durable hosted agents, threads, streaming, MCP/A2A endpoints |
| **LangChain frontend hooks** | Streaming UI patterns for agents/graphs | Optional future replacement or supplement for MyMem's current SSE chat UI |

---

## How This Fits MyMem

MyMem already has the harder and more interesting piece: a persistent, human-readable wiki with semantic pages, wikilinks, domain tags, retrieval, daily summaries, curiosity tracking, and evals.

The LangChain ecosystem should sit **around** that system:

```text
LangGraph agent runtime
  -> LangChain tool wrappers
  -> MyMem FastAPI / MCP tools
  -> wiki markdown + SQLite + rag.db
  -> LangSmith traces and evals
```

Do not make LangChain memory the canonical memory. The canonical memory should remain:

- Markdown wiki pages
- YAML frontmatter
- Wikilinks
- Source metadata
- RAG/vector index
- Curiosity and eval databases

LangGraph should decide **when and how to use memory**. MyMem should remain **where memory lives**.

---

## Recommended Agent Tools

Expose MyMem to agents through a small tool surface:

| Tool | Backing endpoint/module | Purpose |
|---|---|---|
| `search_wiki(question, domain?)` | `POST /api/query` or pipeline query function | Retrieve and synthesize cited answers |
| `read_page(slug)` | `GET /api/page/:slug` | Load a full wiki page into agent context |
| `browse_graph(slug, depth)` | `GET /api/graph?from=:slug&depth=2` | Traverse nearby concepts |
| `remember(title, body, domain, tags, confidence)` | New `POST /api/remember` | Save agent-written memory |
| `recall_procedure(name)` | New procedure store/page convention | Retrieve reusable workflows |
| `flag_contradiction(slug, reason)` | New review/retraction path | Mark stale or conflicting knowledge |

This can be exposed in two layers:

1. **LangChain tools** for LangGraph/LangChain agents.
2. **MCP tools** for Claude Code, Claude Desktop, Codex, and other MCP-compatible clients.

MCP is probably the better long-term external interface. LangChain tools are useful for agents you own inside this codebase.

---

## LangGraph Workflow Ideas

### Researcher Agent

Purpose: answer research questions, discover missing pages, and write proposed wiki additions.

```text
classify_question
  -> search_wiki
  -> read_top_pages
  -> browse_graph
  -> identify_gap
  -> draft_memory
  -> human_review_if_needed
  -> remember
```

Useful state fields:

- `question`
- `domain`
- `retrieved_pages`
- `graph_neighbors`
- `answer`
- `memory_draft`
- `confidence`
- `citations`

### Reviewer Agent

Purpose: protect the wiki from low-quality or contradictory agent writes.

```text
read_candidate_page
  -> retrieve_supporting_sources
  -> check_claim_support
  -> compare_existing_pages
  -> approve_or_flag
```

This maps well to the existing RAGAS-lite idea in `mymem/evals/ragas_lite.py`: break answers into atomic claims and classify them as supported, unsupported, or contradicted.

### Curiosity Agent

Purpose: use the existing curiosity engine as an autonomous reading/research planner.

```text
read_curiosity_profile
  -> find_sparse_high-interest_topics
  -> propose_research_tasks
  -> run_researcher_agent
  -> save_digest
```

This is a natural extension of `GET /api/curiosity` and daily summaries.

### Procedure Agent

Purpose: store and recall "how to do things" as procedural memory.

Examples:

- `how-to-debug-rag-retrieval`
- `weekly-ingest-workflow`
- `how-to-evaluate-agent-written-pages`

This fills one of the missing memory types identified in `docs/research/wiki-as-agent-memory.md`.

---

## Human-in-the-Loop Rules

Agent writes should not all be equal. Some writes can be automatic, but risky writes should pause for review.

Require review when:

- The agent edits an existing human-authored page.
- `confidence < 0.75`.
- No source citations are attached.
- The write contradicts another page.
- The page domain is sensitive, such as finance, health, or legal.
- The agent wants to archive or retract content.

Allow automatic writes when:

- The page is clearly marked `origin: agent`.
- It is a draft page or procedure note.
- It has citations.
- It does not overwrite human-authored material.

Suggested frontmatter additions:

```yaml
origin: agent
agent_id: researcher-v1
confidence: 0.82
status: draft
review_required: true
source_type: synthesis
```

---

## LangSmith Evaluation Plan

MyMem already has an offline eval framework. LangSmith can extend it by capturing real agent behavior.

Use LangSmith for:

- Tracing each agent step and tool call.
- Seeing which pages were retrieved before an answer.
- Debugging failed or irrelevant graph traversals.
- Comparing prompt versions.
- Collecting human feedback on questionable answers.
- Turning bad production runs into regression examples.

Map existing evals into LangSmith concepts:

| MyMem eval | LangSmith equivalent |
|---|---|
| `tests/eval_cases/retrieval.yaml` | Dataset examples |
| Retrieval precision/MRR/UDCG | Code evaluators |
| RAGAS-lite faithfulness | LLM-as-judge evaluator |
| Wiki richness score | Code evaluator |
| Human review of agent writes | Annotation queue / feedback |
| Production query failures | Online traces converted to offline tests |

LangSmith is most useful once agents start making multi-step decisions. Until then, local evals are probably enough.

---

## Recommended Build Order

1. **Add provenance fields**
   - Add `origin`, `agent_id`, `confidence`, and `status` to wiki frontmatter.
   - Preserve backward compatibility for existing pages.

2. **Add `POST /api/remember`**
   - Purpose-built endpoint for agent-written memory.
   - Should write draft pages by default.
   - Should never silently overwrite human-authored pages.

3. **Add depth-limited graph traversal**
   - Extend `GET /api/graph` to support `from` and `depth`.
   - This makes graph navigation practical for agents.

4. **Create LangChain tool wrappers**
   - Wrap `search_wiki`, `read_page`, `browse_graph`, and `remember`.
   - Keep wrappers thin; call existing MyMem logic.

5. **Build a first LangGraph agent**
   - Start with `ResearcherAgent`.
   - Use explicit state and simple deterministic edges.
   - Add human approval before writes.

6. **Add LangSmith tracing**
   - Trace retrieval, page reads, graph browsing, LLM calls, and writes.
   - Use tags like `agent:researcher`, `domain:tech`, `memory_write:true`.

7. **Export eval cases to LangSmith**
   - Convert `tests/eval_cases/retrieval.yaml` into LangSmith datasets.
   - Keep local evals as the offline, self-hosted baseline.

8. **Add reviewer and procedure agents**
   - Reviewer protects memory quality.
   - Procedure agent fills the procedural memory gap.

---

## What Not To Do

- Do not replace the wiki with a generic vector memory store.
- Do not let agents overwrite human pages without review.
- Do not add LangGraph before defining the tool surface.
- Do not make LangSmith required for local/offline development.
- Do not expose every FastAPI endpoint as an agent tool; keep the tool surface small and intentional.

---

## Architecture Sketch

```text
User / scheduled task
        |
        v
LangGraph agent
        |
        +-- search_wiki --------> MyMem query pipeline -> wiki + rag.db
        +-- read_page ----------> wiki markdown
        +-- browse_graph -------> wikilink graph
        +-- remember -----------> draft wiki page
        +-- recall_procedure ---> procedure pages
        |
        v
LangSmith tracing/evals
```

The important boundary: LangGraph controls workflow state; MyMem controls durable knowledge state.

---

## Resume / Portfolio Framing

If implemented, this becomes a strong project story:

> Extended MyMem into an agent-native memory system by exposing its Markdown wiki and wikilink graph as tool-callable memory operations, orchestrating researcher/reviewer agents with LangGraph, and tracing/evaluating multi-step agent behavior with LangSmith.

More technical version:

> Built LangGraph agents over a human-readable wiki memory layer, with MCP/LangChain tools for semantic retrieval, page reads, graph traversal, and agent-written draft memory; added LangSmith traces and evaluation datasets to measure retrieval quality, faithfulness, and write safety.

---

## Sources

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph
- LangGraph durable execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
- LangChain overview: https://docs.langchain.com/oss/python/langchain/overview
- LangChain agents: https://docs.langchain.com/oss/python/langchain-agents
- LangChain human-in-the-loop: https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- LangSmith evaluation concepts: https://docs.langchain.com/langsmith/evaluation-concepts
- LangSmith / LangChain product overview: https://www.langchain.com/
- LangSmith deployment components: https://docs.langchain.com/langsmith/components
- Deep Agents overview: https://docs.langchain.com/oss/python/
