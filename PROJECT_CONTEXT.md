# MyMem — Project Context

## Stack
- Python 3.11+ (strict mypy), FastAPI, Typer/Rich CLI
- React 18 + TypeScript frontend (Vite, Tailwind CSS v3)
- SQLite (sqlite-vec for RAG), markdown files for wiki
- LLM providers: Ollama (default), Anthropic, OpenAI, Groq, NVIDIA, Gemini, OpenRouter
- Testing: pytest + pytest-asyncio + pytest-cov (≥ 80% required)

## Current Branch
- `V1-0006` — active development

## Architecture Decisions

| ADR | Decision | Status |
|-----|----------|--------|
| ADR-001 | RAG chunking strategy | Accepted |
| ADR-002 | Extraction eval strategy (dual-LLM consensus) | Accepted |
| ADR-003 | Wiki storage format (MD over HTML) | Accepted |
| ADR-004 | External integrations (Obsidian, NotebookLM, Notion) | Accepted |
| ADR-005 | Agent decomposition strategy (4 agents + 2 subagents) | Accepted |
| ADR-006 | Extraction quality improvements | Accepted |
| ADR-007 | Lightweight entity layer instead of full GraphRAG | Accepted |

## Completed Features

| Feature | Module | Notes |
|---------|--------|-------|
| CLI (ingest/query/lint/serve/introspect) | `mymem/cli.py` | DONE |
| Wiki page management | `mymem/wiki/` | DONE |
| Multi-LLM router + fallback chain | `mymem/pipeline/router/` | DONE |
| RAG store + embedder (nomic-embed-text 768-dim) | `mymem/rag/` | DONE |
| FastAPI + React SPA | `mymem/web/`, `frontend/` | DONE |
| Eval framework (chunking, wiki quality, retrieval, RAGAS-lite) | `mymem/evals/` | DONE |
| Extraction consensus eval (dual-LLM, cosine matching) | `mymem/evals/extraction_consensus.py` | DONE |
| Obsidian vault integration | `mymem/cli.py` (obsidian subcommand) | DONE |
| NVIDIA provider | `mymem/pipeline/llm.py` | DONE |
| Evals UI (extraction consensus history table) | `frontend/src/pages/EvalsPage.tsx` | DONE — fixed stale build |
| Source reading extracted to Strategy pattern | `mymem/pipeline/readers.py` | DONE |
| LLM provider refactored to Strategy/Bridge pattern | `mymem/pipeline/llm.py` | DONE |
| Evaluator[T] Generic ABC for eval framework | `mymem/evals/_base.py` | DONE |
| Provider credentials abstraction | `mymem/pipeline/router/_credentials.py` | DONE |
| Map-reduce extraction for long sources | `mymem/pipeline/ingest.py` | DONE |
| Idea dedup + ranking (cosine sim > 0.85) | `mymem/pipeline/ingest.py` | DONE |
| Evals API endpoints (/api/evals/extraction, /api/evals/summary) | `mymem/web/routes/api.py` | DONE |
| Eval suite summary grid (cards, staleness, never-run states) | `frontend/src/components/EvalSuiteGrid.tsx` | DONE |
| Eval run trigger (POST /api/evals/run + UI button, RAGAS flag) | `mymem/web/routes/api.py`, `EvalsPage.tsx` | DONE |
| Grades for wiki_quality + chunking summaries | `mymem/evals/ingest_quality.py`, `chunking.py` | DONE |

## Security Status
- **Last Audit**: 2026-06-11
- **Verdict**: PASS
- **Open Issues**: 0 critical, 0 high, 2 medium (SSRF localhost scope, rate limiting), 3 low
- **Fixed This Session**: none needed — scan clean
- **Compliance**: local-first tool, no PII handling

## Known Gaps

1. `mymem/evals/review.py` — human review CLI for extraction eval not built
2. SSRF: user-supplied URLs accepted without allowlist (acceptable for local deployment; document before network-exposing)
3. No rate limiting on write endpoints (acceptable for local deployment)

## Planned Features

### Proposed
- [ ] Graph entity mapping — priority: P0 — PRD: docs/PRD/graph-entity-mapping.md (branch V1-0007)
  - Typed entity extraction folded into ingest LLM call (person/project/system/org/concept + span)
  - 3-tier resolution: exact/alias → rapidfuzz+cosine → batched LLM judge (Graphiti pattern)
  - `data/graph.db`: entities/aliases/mentions; shared-entity edges join pages
  - 1-hop graph expansion + RRF fusion into existing hybrid retrieval
  - Alias frontmatter + deterministic unlinked-mention linter (Obsidian pattern)
  - Evals: entity consensus + span-grounding; KGQAGen-style multi-hop A/B; ship gates:
    multi-hop recall up, single-hop no regression, ingest cost < +20%
  - NOT building: community detection/summaries (ADR-007)
- [ ] Human review track for extraction eval (`mymem/evals/review.py`, `mymem eval --review`)
- [ ] Wire extraction consensus into `EvalReport` (`runner.py` surfaces consensus results)
- [ ] Ontology layer — typed relationships (is-a, part-of, contradicts, etc.)
- [ ] Agent decomposition (4 agents + 2 subagents per ADR-005)
- [ ] NotebookLM / Notion sync integrations (per ADR-004)

### Backlog
- [ ] Rate limiting middleware on write endpoints (before network exposure)
- [ ] URL allowlist / SSRF protection (before network exposure)
- [ ] MIME type validation on file upload

## Success Metrics

- Extraction consensus PASS rate on ingested articles (3 runs recorded: 2× WARN, 1× PASS)
- Mean duplicate concept pairs per ingest (target: near 0 after dedup)
- Wiki page coverage: ideas from full document via map-reduce (no longer limited to 6000 chars)
- Test suite: 579 tests passing as of 2026-06-11
