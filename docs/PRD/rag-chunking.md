# PRD: Better RAG Chunking for Wiki Pages

Branch: V1-0002
Priority: P1 (high)
Research: docs/research/rag-chunking.md

---

## Problem Statement

The current RAG system only indexes PDFs using naive paragraph-aware fixed-size sliding window
chunks (800 chars, 80 char overlap). Wiki pages (`.md` files with YAML frontmatter and `##`
headings) are never indexed into the vector store. When a user queries, semantic search has no
coverage over the wiki itself — only over ingested PDFs. Additionally, chunks carry no metadata
beyond `page_num` and `chunk_index`, so retrieval has no heading context, title, or domain signal.

## Goals

- G1: Wiki pages are chunked by markdown headings and indexed into the vector store
- G2: Each chunk carries rich metadata: title, heading path, domain, tags, source file, dates
- G3: Parent-child chunking: embed small child chunks (200-300 tokens), store larger parent context (512-1024 tokens) returned to LLM
- G4: Metadata prefix fused into embedding: `"{title} > {heading_path}"` prepended before encoding
- G5: Retrieval quality measurably improves — relevant wiki pages surface in query results

## Non-Goals

- PDF-specific chunking improvements (V1-0003)
- Re-ranking or query pipeline changes (V1-0004)
- Chunking for non-wiki, non-PDF sources (articles, YouTube transcripts)
- UI changes beyond confirming wiki pages appear in search results

## User Stories

- As a user querying "what is multi-head attention?", I want wiki pages I've written on that topic to surface as citations, not just PDFs
- As a user, I want search results to include the specific section of a wiki page that answers my question, not a random fragment
- As a developer, I want chunk metadata (title, heading path) visible in RAG search results so I can debug retrieval quality

## Acceptance Criteria

- [ ] AC1: All existing wiki `.md` pages are indexed into `rag.db` on `mymem serve` startup (or via `mymem ingest --reindex-wiki`)
- [ ] AC2: New wiki pages are indexed automatically after being written by the ingest pipeline
- [ ] AC3: Each chunk has: `source_file`, `page_title`, `heading_path`, `domain`, `tags`, `chunk_type` (child/parent), `chunk_index`
- [ ] AC4: Child chunks are 200-300 tokens; parent chunks are 512-1024 tokens
- [ ] AC5: Embedding input is `"{title} > {heading_path}: {chunk_text}"`
- [ ] AC6: Query pipeline returns parent chunk text to LLM when a child chunk matches
- [ ] AC7: `GET /api/stats` reports wiki chunks indexed count alongside page count
- [ ] AC8: Unit tests cover: header splitting, parent-child linking, metadata attachment, empty-heading fallback

## Success Metrics

- Wiki pages appear in citations for queries where they are clearly relevant
- No regression in existing PDF RAG retrieval

## Timeline

- Research: done
- Development: 1-2 sessions
- Testing: included in development

## Dependencies

- `langchain-text-splitters` — `pip install langchain-text-splitters` (add to `pyproject.toml`)
- Existing: `mymem/rag/store.py`, `mymem/rag/embedder.py`, `mymem/rag/ingest.py`
- Wiki pages written by `mymem/pipeline/ingest.py` must trigger re-indexing

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| langchain-text-splitters API changes | Low | Medium | Pin version in pyproject.toml |
| Wiki re-indexing slows down ingest pipeline | Medium | Low | Run async, best-effort (never raise) |
| Duplicate chunks if wiki page updated | Medium | Medium | Hash-based deduplication in store.py |
| Parent chunk retrieval adds latency | Low | Low | Parent text stored in DB, no extra embed call |
