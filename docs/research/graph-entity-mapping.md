# Research: Graph Entity Mapping & GraphRAG for MyMem

**Date:** 2026-06-10 · **Status:** Complete · **Decision:** Lightweight entity layer, NOT full GraphRAG (see ADR-007)

## Question

MyMem already has pages-as-nodes + [[wikilinks]]-as-edges. Should we adopt full GraphRAG
(entity extraction → community detection → community summaries), and how do we improve
entity identification/grounding so the graph and RAG actually get better — measurably?

## Evidence: when GraphRAG helps, and when it doesn't

| Finding | Source |
|---------|--------|
| GraphRAG wins on **multi-hop** (HotpotQA, MultiHop-RAG) and **global/sensemaking** queries; vanilla RAG wins single-hop detail retrieval | arXiv 2502.11371 (systematic eval) |
| Only ~65.8% of answer entities appeared in constructed KGs — **graph coverage is the bottleneck**, not graph reasoning | arXiv 2502.11371 |
| Discarding text chunks for triples performs poorly — **keep original text**; graph is an additive signal | VLDB 2025 unified framework (Zhou et al.) |
| GraphRAG can be 13.4% WORSE on Natural Questions, 2.3× latency, for +4.5% on multi-hop | arXiv 2506.05690 survey |
| MS GraphRAG indexing: **$20–40 per 1M tokens**; LazyGraphRAG matches quality at **0.1% of indexing cost** — deferred graph construction wins | MS Research blog, GitHub discussion #440 |
| Agentic multi-round search over dense RAG narrows most of the GraphRAG gap | arXiv 2604.09666 "Do We Still Need GraphRAG?" |

**Interpretation for MyMem:** wiki pages already ARE community summaries (LLM-curated
syntheses), and index.md is the global layer. Full GraphRAG would re-derive, at high LLM
cost, artifacts the ingest pipeline produces natively — and its community recompute breaks
under MyMem's update-pages-in-place model (MS GraphRAG `update` is append-only,
degrades to full re-index). The actual gap: wikilinks connect *pages*, not *entities* —
"Sarah from the platform team" on page A and "S. Chen" on page B share no edge.

## OSS prior art — what to port (not adopt)

| Project | Stars | Verdict | What to steal |
|---------|-------|---------|---------------|
| microsoft/graphrag | 33.6k | SKIP — parquet/Azure-coupled, batch-shaped, no SQLite | local/global search *concept* only |
| HKUDS/LightRAG | 36.4k | PORT PATTERN — best extraction prompts, but no SQLite backend | tuple extraction prompt + gleaning loop, name-keyed entity merge, `summarize_entity_descriptions`, dual-level (entity/relation) retrieval framing |
| getzep/graphiti | 27.3k | SKIP dep (needs Neo4j) — PORT resolution pipeline | **3-tier entity resolution**: embed+cosine candidates (top 15, thr 0.6) → deterministic name/alias match → batched LLM judge for leftovers. Plus entropy-gated fuzzy match, MinHash/LSH accept at Jaccard >0.9 without LLM |
| circlemind/fast-graphrag | 3.8k | SKIP (stale ~14mo, vendor-coupled deps) | Personalized PageRank from query-matched seeds — ~20 lines of networkx, zero LLM calls |
| gusye1234/nano-graphrag | 3.9k | SKIP dep (stale) — best *reference read* (~1,100 LOC) | full-loop reference implementation |
| Obsidian plugins (note-linker, virtual-linker, smart-connections) | — | PORT heuristics | layered linking: exact title → **frontmatter aliases** → word-boundary regex w/ capitalization rules; embeddings only for *suggestions*, never silent auto-insert |

Classic entity-linking systems (BLINK, ReFinED) are overkill: MyMem's "KB" is its own
wiki index — hundreds of slugs with titles/tags/aliases. Graphiti's resolution *is*
entity linking at exactly this scale, using infra MyMem already has (sqlite-vec + router).

## Dependency audit

**Recommended additions (path: build-it-ourselves):**

```toml
"networkx==3.6.1",     # BSD-3, ZERO required deps — Louvain, PageRank, traversal; fine to 10k+ nodes
"rapidfuzz==3.14.5",   # MIT, zero deps, win wheels — alias/fuzzy name matching
```

Everything else reuses existing infra: SQLite (`WITH RECURSIVE` CTEs for k-hop), sqlite-vec
+ nomic-embed-text for entity-name cosine, LLM router for extraction.

**Rejected:**
- `gliner`, `spacy`, `sentence-transformers` — heavy (torch/transformers chain carries 2025
  deserialization-RCE CVEs: CVE-2025-14920/-14921/-14924/-14929); flat NER beats nothing
  when cloud LLM extraction is available
- `graphrag` (Azure/parquet, no SQLite), `lightrag-hku` (no SQLite backend, bypasses router),
  `nano-graphrag`/`fast-graphrag` (stale), `kuzu` (**archived — company shut down**)
- `leidenalg`/`igraph` — GPL + unnecessary; nx Louvain fine below 100k nodes

## Enterprise perspective

- **Glean**: canonical entities (projects, people, customers, products) are the spine that
  lets one query span silos; every item keeps **permission metadata**, scoped per user at query time
- **Atlassian Teamwork Graph / Rovo**: "150B connections" — the graph is the moat
- For Confluence/Notion migrants: same project/person appears under a dozen surface forms;
  without canonical entities, cross-doc Q&A silently mixes "Project Phoenix (2023)" with "Phoenix DB"
- **Failure modes to design against**: stale entities (Zep solves with temporal edge
  invalidation — `valid_at`/`invalid_at`, never delete), permission leaks via graph expansion
  (every traversal must be ACL-filtered when multi-user), hallucinated relations (mitigate
  with span-grounding: every entity must match a span in the source)

## Hybrid retrieval patterns, ranked by lift-per-complexity

1. **Vector-first → 1-hop graph expansion** — 15–30% faithfulness/relevancy gains reported; cap neighbors, hard depth limit
2. **RRF fusion** of graph-derived candidates with existing keyword+vector ranks — rank-based, no tuning
3. **Entity-tagged chunks** + post-retrieval shared-entity adjacency — multi-hop ability without triple extraction
4. Adaptive query routing — defer; worth it only at scale

## Eval methodology (no gold labels needed)

- **Extraction**: extend existing dual-LLM consensus to entities + mechanical **span-grounding
  check** (every extracted entity must fuzzy-match a source span — catches hallucination, zero LLM cost)
- **Resolution**: self-supervised alias pairs (LLM proposes, human spot-checks once);
  report pairwise precision/recall + V-measure (Google EKG metrics)
- **End-to-end (decisive)**: KGQAGen pattern (NeurIPS 2025) — sample 2-hop paths
  (page —entity— page), LLM generates questions answerable only with both pages, verify,
  keep 50–100 cases. A/B vector-only vs vector+graph-expansion on recall@k + order-swapped
  LLM-judge. KGQAGen headline: GPT-4o 54.2%→84.9% when handed the supporting subgraph —
  **retrieval is the bottleneck a graph layer must prove it fixes**
- **Known LLM-judge trap**: pairwise wins reverse with presentation order — always order-swap

## Ship/no-ship criteria

Ship the graph layer only if:
1. Multi-hop recall@k improves significantly (A/B)
2. Single-hop retrieval eval shows **no regression** (the documented GraphRAG failure mode)
3. Ingest token cost increase stays under ~20% (router cost tracking measures this)
