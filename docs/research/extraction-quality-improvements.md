# Research: Extraction Quality Improvements

## Summary

Three parallel research tracks — GitHub production systems, academic literature (ACL/EMNLP/NeurIPS 2024–2025), and PyPI dependency audit — converge on the same set of improvements for MyMem's ingest extraction pipeline.

---

## GitHub Production Patterns

### 1. GraphRAG (microsoft/graphrag, ~33k stars) — Gleaning Loop

The strongest single-effort recall improvement. After the main extraction pass, send a second LLM turn:

> "MANY entities were missed in the last extraction. Add them below using the same format:"

Followed by a binary probe: "Answer Y if there are still entities to add, or N if there are none." Loop runs until N or `max_gleanings` cap (default 1). Cited as the primary driver of recall improvement over single-pass extraction.

**MyMem mapping:** Add one gleaning turn after the main `ingest.py` extraction call, using the same JSON schema for ideas.

### 2. google/langextract (~1.5k stars) — Multi-Pass + Character-Interval Dedup

Run `extraction_passes=2` independent full-text passes with the same prompt. Merge with first-pass-wins, character-interval deduplication. Exploits LLM stochasticity — each pass catches different concepts. Also enforces **source grounding**: every extracted concept is anchored to its character offset in source text (hallucination check — fabricated concepts have no valid span).

**MyMem mapping:** Two-pass extraction for sources > 4000 chars; merge by concept title similarity (rapidfuzz token_set_ratio > 90).

### 3. getzep/graphiti (~20k stars) — Three-Layer Semantic Dedup

1. **MinHash + LSH** (3-gram Jaccard ≥ 0.9) — candidate generation with entropy gate: skip LSH for short/low-entropy names, route directly to LLM judgment
2. **Cosine similarity on embeddings** — catches semantic equivalents with different surface forms ("ML" vs "machine learning")
3. **BFS neighborhood similarity** — resolves near-duplicates that share wikilink neighbors

**MyMem mapping:** Entropy gate + embedding cosine (nomic-embed-text, already in stack) for post-extraction dedup. The wikilink neighbor check maps directly to MyMem's wikilink graph.

### 4. ISE-FIZKarlsruhe/concept_extraction (ConExion, 2025) — Prompt Design Findings

Benchmarked on ontology-learning datasets:
- Zero-shot with domain hint **lowers** both precision and recall (over-constrains)
- **Few-shot 1-random** (one gold example, no domain hint) achieves best F1 with Llama3-70B
- Structured 3-part prompt (system → user → assistant prefix) outperforms bare instruction
- Correctness-prioritizing prompts under-generate (recall collapse) — workaround: hybrid LLM+RAKE, then LLM re-scores RAKE candidates

---

## Academic Literature (2024–2025)

### Prompt Engineering

**Chain-of-Structured-Thought (LITECOST, OpenReview 2025):** Separate extraction into three explicit steps: (a) identify document structure and concept types present, (b) reason step-by-step with evidence anchors for each concept, (c) verify/refine — reject if not fully supported by source text. Outperforms single-pass prompts on precision without recall loss.

**Classify-then-Extract (ACL 2024, ml4ai/pc4wills):** Fast first pass (small model) classifies what concept types are present in the source (mechanism, definition, claim, biographical fact, etc.). Main extraction pass uses type-specific few-shot examples per type. Avoids the current problem where one generic prompt serves articles, papers, YouTube transcripts, and notes equally.

**Fixed concept count is harmful:** Prompting with `max_concepts=N` forces padding with weak ideas or truncating real ones. LLM should decide how many concepts exist; prompt should specify minimum quality, not minimum quantity. Replace with: "Extract as many distinct concepts as needed. Omit any concept unless it is fully supported by the source text and not covered by another concept you've already listed."

### Metrics Beyond ROUGE-1

ROUGE-1 has near-zero or negative correlation with human topic coverage judgments (Pearson ρ = −0.058 in ICAT, ACL 2025 Findings). Concrete replacements:

| Metric | What it measures | Implementation |
|--------|-----------------|----------------|
| Semantic recall | For each reference idea, cosine sim to closest pipeline idea ≥ 0.78 | `embedder.py` + `sklearn.cosine_similarity` (already in stack) |
| Semantic precision | For each pipeline idea, highest cosine sim to any reference idea | Same |
| Uniqueness score | Mean pairwise cosine sim within pipeline ideas; high = redundant | Detects "attention mechanism" vs "self-attention" duplicate |
| Factualness proxy | BM25 score of idea summary against source text | `rank_bm25` already in stack |

GenRES framework (arXiv 2402.10744) proposes 5 dimensions — Topic Similarity, Uniqueness, Factualness, Granularity, Completeness — all computable without ground truth using embeddings + source-text retrieval.

### Long Document Handling

**LLM×MapReduce (ACL 2025):** Map step extracts `{ideas, confidence_score}` per chunk with the same schema. Confidence anchored to rubric: "5 = fully supported by this chunk's text; 3 = inferred; 1 = not present." Collapse step merges when chunk outputs overflow context. Reduce step synthesizes final concept list using confidence to resolve cross-chunk conflicts.

**MyMem has the infrastructure:** `ChunkSplitter` and `merge_prompt` already exist. Missing piece: structured information protocol — each chunk extraction should output confidence scores. The `merge` model then deduplicates and ranks by confidence instead of just concatenating.

Current `source_text[:6000]` truncation discards the majority of any article > ~4 pages. This is the highest-impact single fix.

### LLM-as-Judge Biases

- When true accuracy < 0.75: judge **overestimates**. When > 0.75: **underestimates** (arXiv 2511.21140)
- Judges prefer longer concept lists (length bias)
- Egocentric bias: same model family for generation and evaluation inflates agreement scores
- Fix: different model family for reference extraction than pipeline (already done — groq/nvidia vs anthropic)
- Optional: Rogan-Gladen bias correction with 30–50 human-calibration examples

---

## Dependency Audit

### Adopt

| Package | Version | Why |
|---------|---------|-----|
| `instructor` | 1.15.1 | Eliminates fragile `_parse_reference_ideas()` regex dance; guarantees Pydantic-validated JSON from Anthropic/OpenAI; zero new heavy deps |
| `rapidfuzz` | 3.14.5 | Title-level fuzzy dedup (token_set_ratio); far faster than O(n²) TF-IDF cosine for short strings; MIT, C extension only |

### Already Available (no new deps needed)

| What | How |
|------|-----|
| Embedding cosine similarity | `embedder.py::embed_texts()` → `sklearn.metrics.pairwise.cosine_similarity` |
| Structured output validation | `pydantic` (already in stack) — add `IdeaSchema` model to validate extracted dicts |
| BM25 factualness check | `rank_bm25` (already in stack) |

### Skip

- `sentence-transformers` — cannot reuse Ollama-served embeddings; torch 2 GB install; no synergy
- `ragas` — hardcodes `langchain_openai` as required dep; conflicts with provider-agnostic architecture
- `deepeval` — 118 MB install, forces opentelemetry; GitHub issue #1815 is a known blocker
- `rouge-score` — inactive since 2022; in-house `rouge1_f1()` is equivalent; only worth adding for ROUGE-2/L (implement inline)
- `outlines` — constrained generation for local model inference; doesn't work with Anthropic/Ollama API layer

---

## Priority Matrix

| Priority | Change | Files | Expected Gain |
|----------|--------|-------|---------------|
| P0 | Replace `source_text[:6000]` with ChunkSplitter map-reduce extraction; each chunk emits `{ideas, confidence}` | `pipeline/ingest.py`, new `pipeline/extraction_dedup.py` | Eliminates truncation loss for long sources |
| P0 | Replace ROUGE-1 matching with embedding cosine similarity (threshold 0.78) in consensus eval | `evals/extraction_consensus.py`, `evals/metrics.py` | ROUGE-1 has ρ = −0.058 with human judgments; embedding cosine is the validated replacement |
| P1 | Add gleaning loop: one extra "what did you miss?" LLM turn after main extraction | `pipeline/ingest.py` | GraphRAG's primary recall driver; low effort |
| P1 | Post-extraction dedup: embed all concept titles, merge pairs with cosine sim > 0.85 | `pipeline/extraction_dedup.py` | Eliminates "attention mechanism" / "self-attention" duplicates |
| P1 | Remove fixed `max_concepts=N`; replace with quality-floor instruction | `pipeline/ingest.py` (prompt) | Prevents padding/truncation of ideas |
| P2 | Add `instructor` for structured JSON output | `evals/extraction_consensus.py`, `pipeline/ingest.py` | Eliminates fragile regex JSON parsing |
| P2 | Classify-then-extract: fast type classification before main extraction | `pipeline/ingest.py` | Improves precision for diverse source types |
| P3 | Uniqueness + factualness scores in `ExtractionConsensusResult` | `evals/extraction_consensus.py` | Surfaces redundancy and hallucination separately |
| P3 | Add `rapidfuzz` for title-level dedup signal | `evals/metrics.py` | Better short-string matching than bag-of-words cosine |

---

## Sources

- microsoft/graphrag: https://github.com/microsoft/graphrag
- google/langextract: https://github.com/google/langextract
- getzep/graphiti: https://github.com/getzep/graphiti
- ISE-FIZKarlsruhe/concept_extraction (ConExion): https://github.com/ISE-FIZKarlsruhe/concept_extraction — arXiv 2504.12915
- LITECOST (CoST): https://openreview.net/pdf?id=faECRsdRav
- APIE (active few-shot): https://arxiv.org/html/2508.10036v1
- ICAT coverage metrics: https://aclanthology.org/2025.findings-acl.693.pdf
- GenRES multi-dim eval: https://ar5iv.labs.arxiv.org/html/2402.10744
- DeCE (decomposed precision/recall): https://aclanthology.org/2025.emnlp-industry.136.pdf
- LLM×MapReduce: https://aclanthology.org/2025.acl-long.1341v2.pdf
- KGGen (entity clustering): https://papers.neurips.cc/paper_files/paper/2025/file/2b368455e832d2b1a60bcad8c4c6481f-Paper-Conference.pdf
- LLM judge bias correction: https://arxiv.org/pdf/2511.21140
- instructor PyPI: https://pypi.org/project/instructor/
- rapidfuzz PyPI: https://pypi.org/project/RapidFuzz/
