# ADR 007: Lightweight Entity Layer Instead of Full GraphRAG

## Status: Proposed

## Context

MyMem needs better cross-document connectivity: wikilinks connect pages, not entities, so
the same person/project/concept under different surface forms fragments the graph and caps
multi-hop retrieval. Full GraphRAG (Microsoft-style: entity extraction → Leiden communities
→ LLM community summaries) was evaluated as the obvious candidate. Research:
docs/research/graph-entity-mapping.md.

## Decision

Build a **lightweight entity layer** on SQLite — typed entity extraction folded into the
existing ingest LLM call, 3-tier write-time resolution (exact/alias → rapidfuzz+embedding
cosine → batched LLM judge), shared-entity edges joining pages, and 1-hop graph expansion
fused into existing hybrid retrieval via RRF.

**Explicitly rejected: community detection and community summaries.** Dependencies limited
to `networkx` (BSD-3, zero deps) and `rapidfuzz` (MIT, zero deps); storage is SQLite
(`data/graph.db`) reusing sqlite-vec for entity-name embeddings.

## Rationale

1. **Wiki pages already are community summaries.** Each page is an LLM-curated synthesis;
   index.md is the global layer. Leiden + community reports would re-derive these at
   $20–40/1M-token indexing cost (MS GraphRAG reported numbers).
2. **Incremental updates break full GraphRAG.** MyMem updates pages in place daily; MS
   GraphRAG's update path is append-only and degrades to full re-index. LazyGraphRAG's
   result (equal quality at 0.1% indexing cost) shows deferred graph construction wins.
3. **Graph coverage, not graph reasoning, is the bottleneck** (only ~65.8% of answer
   entities appear in constructed KGs per arXiv 2502.11371). Entity grounding directly
   attacks coverage; communities don't.
4. **Evidence-based scope**: GraphRAG demonstrably helps multi-hop and entity-centric
   queries but can REGRESS single-hop accuracy 13%+ with 2.3× latency. The entity layer
   adds graph signal additively (RRF) and is gated by a no-single-hop-regression eval.
5. **Enterprise alignment**: canonical entities (Glean, Atlassian Teamwork Graph) are the
   enterprise differentiator — not community summaries.

## Alternatives Considered

1. **Adopt microsoft/graphrag** — rejected: parquet/Azure-coupled, no SQLite backend,
   batch-shaped, expensive indexing.
2. **Adopt lightrag-hku** — rejected: no SQLite backend (Neo4j/Postgres/Milvus), bypasses
   MyMem's router/tracing/cost-tracking; its extraction prompts and merge patterns are
   ported instead.
3. **Adopt getzep/graphiti** — rejected: hard Neo4j requirement; its 3-tier entity
   resolution pipeline is ported wholesale onto sqlite-vec.
4. **Classic entity linking (BLINK/ReFinED/spaCy EL)** — rejected: heavyweight models bound
   to Wikipedia/Wikidata; MyMem's KB is its own page catalog at hundreds-of-slugs scale.
5. **Do nothing (vector RAG only)** — rejected: multi-hop and entity-consistency gaps are
   real, measured, and block the enterprise story.

## Consequences

- Positive: incremental by design; ~2 zero-dep packages; reuses router, embedder, eval
  framework; delivers the planned ontology groundwork (mentions/edges tables); falsifiable
  via A/B eval gates before shipping
- Negative: builds custom code where frameworks exist (mitigated: ported patterns are
  small and well-understood); entity quality depends on cloud LLM extraction quality
- Risks: entity explosion (alarmed via singleton-rate metric); single-hop regression
  (eval gate); embeddings still require local Ollama — resolution degrades to
  exact+fuzzy tiers when unavailable
