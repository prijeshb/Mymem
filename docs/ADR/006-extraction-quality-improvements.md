# ADR 006: Extraction Quality Improvements

## Status: Proposed

## Context

MyMem's ingest pipeline extracts key ideas from source documents using a single-pass LLM call. Three structural problems have been identified through eval data and user observation:

1. Sources longer than ~4 pages are silently truncated to 6000 chars. A paper's methods, results, and conclusions are never ingested — only the abstract and introduction.
2. The LLM sometimes extracts semantically overlapping concepts as separate ideas ("attention mechanism" and "self-attention" as distinct pages), creating wiki noise and confusing the knowledge graph.
3. The consensus eval uses ROUGE-1 F1 to measure whether two idea sets agree. Academic benchmarks (ICAT, ACL 2025) show ROUGE-1 has Pearson ρ = −0.058 with human coverage judgments — near-random signal quality.

Research covered: microsoft/graphrag, google/langextract, getzep/graphiti, ConExion (2025), ICAT/GenRES/DeCE eval frameworks, LLM×MapReduce (ACL 2025).

## Decision

Four targeted improvements to the extraction pipeline, in priority order:

1. **Map-reduce extraction for long sources** — when `len(source_text) > 6000`, split with `ChunkSplitter`, extract `{ideas, confidence}` from each chunk, merge with the existing `merge` model resolving conflicts by confidence. Short sources (≤ 6000 chars) are unchanged.

2. **Embedding-cosine consensus scoring** — replace ROUGE-1 F1 matching in `extraction_consensus.py` with cosine similarity on `nomic-embed-text` embeddings (threshold 0.78). Fallback to ROUGE-1 if Ollama is offline.

3. **Gleaning loop** — after main extraction, send one additional LLM turn: "What did you miss?" Append any new ideas returned. Capped at 1 gleaning turn.

4. **Post-extraction semantic deduplication** — embed all extracted concept titles+summaries, remove pairs with cosine sim > 0.85 (keep the one with longer summary as the richer representation). New module `extraction_dedup.py`.

Additionally: remove the fixed `max_concepts=N` constraint from the extraction prompt, replacing it with a quality-floor instruction.

## Rationale

**Why map-reduce over sliding window?** The existing `ChunkSplitter` already handles overlapping chunks and the `merge` model already exists in the router registry. Map-reduce with confidence scores (borrowed from LLM×MapReduce, ACL 2025) gives the merge model enough signal to resolve cross-chunk conflicts — a bare concatenation approach would not. The infrastructure is already there; the missing piece is the structured confidence protocol per chunk.

**Why embedding cosine over BERTScore?** BERTScore requires `transformers` + model weights (~500 MB). Embedding cosine over `nomic-embed-text` uses the embedder already running for RAG — zero new dependencies, and the 768-dim vectors from nomic-embed-text capture semantic equivalence well enough for concept matching. Threshold 0.78 chosen based on GenRES findings (binarized semantic coverage at 0.75–0.80 threshold best correlated with human judgments).

**Why keep ROUGE-1 as fallback?** The eval runs fire-and-forget in background. If Ollama is offline, the eval should still produce a result (even a degraded one) rather than silently failing. All existing tests are against ROUGE-1 behavior; keeping the fallback means no test rewrites are needed for offline-mode tests.

**Why a gleaning loop over multi-pass extraction (google/langextract)?** The gleaning pattern (GraphRAG) fits MyMem's existing single-LLM-call structure — it's a second turn in the same conversation context, not a full second extraction run. Multi-pass extraction (run 2x, merge by character offset) would require source grounding (character offset tracking) which MyMem doesn't currently have. Gleaning is lower risk, lower cost, and directly ports from a 33k-star production codebase.

**Why threshold 0.85 for dedup?** ConExion research showed that aggressive thresholds (< 0.80) merge distinct but related concepts. At 0.85, "attention mechanism" and "self-attention" are merged (same concept, different names) but "attention mechanism" and "positional encoding" are kept separate (related but distinct). Threshold is configurable in `config.yaml` for tuning.

## Alternatives Considered

1. **Increase `source_text` truncation limit to 12000 chars** — rejected. Treats the symptom, not the cause. A 50-page paper still loses 80% of its content. Map-reduce properly covers the full document.

2. **sentence-transformers for embedding-cosine matching** — rejected. Cannot reuse Ollama-served embeddings; requires PyTorch (~2 GB install). The existing `embedder.py` + sklearn covers the same need with zero new dependencies.

3. **ragas or deepeval for eval framework** — rejected. `ragas` hardcodes `langchain_openai` as a required dependency; `deepeval` is 118 MB with forced opentelemetry. Both conflict with the provider-agnostic architecture. The existing `ragas_lite.py` covers the same metrics.

4. **KGGen-style hierarchical entity clustering for dedup** — rejected for now. Requires MinHash LSH (`datasketch`) + BFS graph traversal — significant complexity for marginal gain over cosine threshold at current wiki scale (< 200 pages). Flagged as future improvement when wiki exceeds 500 pages.

5. **Classify-then-extract (type-specific few-shot)** — deferred to P2. Would improve precision for diverse source types (paper vs. YouTube vs. note) but requires maintaining a per-domain example set and adds latency to the first pass. Revisit when ingest quality evals show type-specific recall gaps.

## Consequences

**Positive:**
- Long sources (papers, books, long articles) now contribute wiki pages from their full content
- Consensus eval scores become meaningful — changes in PASS/WARN/FAIL rates reflect actual quality changes
- Duplicate concept pairs eliminated before wiki pages are written — cleaner knowledge graph
- Gleaning adds ~1 LLM call per ingest; for typical sources this recovers 1–3 missed concepts

**Negative:**
- Ingest for long sources now makes `N_chunks + 1 (merge) + 1 (gleaning)` LLM calls instead of 1. For a 20,000-char source with 512-token chunks (~40 chunks), this is significant cost. Mitigated by: these run against the local `compile` model (Ollama), not the Anthropic API; dedup runs against Ollama embedder, not an LLM.
- `IdeaMatch.rouge1_score` field is repurposed to hold cosine similarity — the field name becomes misleading. Accepted as a backward-compatible change (field semantics change, not the schema).
- Existing test fixtures for `score_consensus` use ROUGE-1 logic. The online path changes to embedding-cosine; tests that mock the embedder will need updating.

**Risks:**
- Embedding dedup with threshold 0.85 may over-merge in highly specialized domains (two concepts that look similar but are technically distinct). Mitigated by: threshold is configurable and every merge is logged.
- Gleaning loop with a poorly calibrated "nothing missed" condition could add noise. Mitigated by: the prompt explicitly asks for `[]` if nothing was missed, and the gleaning result is appended (not used to replace) the main extraction.
