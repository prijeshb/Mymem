# Research: Extraction Quality Evaluation

**Date:** 2026-05-28  
**Branch:** V1-0004  
**Status:** Complete — feeds into PRD and architecture docs

---

## Problem

MyMem's ingest pipeline extracts 3 "key ideas" from every source and writes wiki pages for each. We evaluate the *output pages* (richness score, retrieval recall, RAGAS faithfulness) but **never evaluate the extraction decision itself**:

- Were these the right 3 ideas to extract?
- Did the main thesis get captured?
- Are the 3 ideas distinct, or do they overlap?
- Would another informed reader have chosen different concepts?

This gap means prompt changes, `max_concepts` tuning, or model swaps have no measurable quality signal.

---

## What "Good Extraction" Means (User-Defined)

Four quality dimensions, in priority order:

1. **Thesis coverage** — the single most important idea in the source is always captured
2. **Distinctness** — the N ideas don't overlap; each covers a different aspect
3. **Searchability** — titles match what someone would type into a search bar
4. **Domain + tag accuracy** — classification matches a curator's expectation

For YouTube/video: ideas should replay the actual discussed topics (transcript-driven).  
For articles: titles should align with SEO-relevant, trending, or commonly-cited concepts.

---

## Chosen Approach: Multi-LLM Consensus + Human Review

### Core insight

If two independent LLMs — given the same source text — both extract the same concept, that concept is almost certainly a real main idea. Disagreement reveals either pipeline gaps or model-specific biases.

### Why not a hand-curated dataset first?

Hand-curating 10–20 test cases requires 20–30 minutes per source before you get any signal. The multi-LLM approach produces a signal on every ingest automatically. The curated dataset grows organically from human review of consensus failures — not as a prerequisite.

### Why not self-supervised (re-ingest, check consistency)?

Self-supervised validates consistency, not quality. A pipeline that consistently extracts the wrong ideas would score 100%.

---

## Model Pairing

Both models are Anthropic cloud (no Ollama — user stack is cloud-only):

| Role | Model | Rationale |
|------|-------|-----------|
| Pipeline extractor | `claude-sonnet-4-6` | Configured via `compile` task in router |
| Reference extractor | `claude-haiku-4-5-20251001` | Cheapest, fast, different architecture — already in fallback chain |

The two models must always differ. If `config.yaml` sets compile to haiku, the reference auto-upgrades to sonnet.

---

## Consensus Metric

**ROUGE-1 recall** between extracted summaries is the core matching signal:
- Two ideas match if `rouge1_f1(summary_A, summary_B) >= 0.25`
- `consensus_score = matched_ideas / max(len(A), len(B))`
- A special `main_thesis: true` flag in the reference output identifies the most important idea — checking whether the pipeline captured it is the single most important sub-metric

**Why ROUGE-1 over embeddings?**
- No transformer model needed (stays compatible with offline/local setup)
- ROUGE-1 already in `mymem/evals/metrics.py` — zero new dependencies for core matching
- For short concept summaries (2–3 sentences), lexical overlap is a strong signal

---

## Research Findings

### From evaluation frameworks research

- **RAGAS** — strong for RAG faithfulness/relevance, but OpenAI-first and designed for retrieval, not extraction
- **LLM-as-judge rubric** — 80–90% human agreement when rubric anchors are explicit; chain-of-thought before scoring catches systematic biases
- **Hybrid F1 with fuzzy matching** — ROUGE + partial credit for near-matches is the right approach for short concept titles
- **Keyphrase frequency** — TF-IDF of title vs source text + character length heuristic is a lightweight searchability proxy

### From dependency audit

| Need | Recommendation | Notes |
|------|---------------|-------|
| Title string similarity | `rapidfuzz` (~2 MB) OR existing ROUGE-1 | rapidfuzz for fuzzy; ROUGE-1 for summary matching |
| Keyphrase searchability | `yake` (< 100 KB) | Unsupervised, no GPU, actively maintained |
| LLM judge scaffolding | existing `ragas_lite.py` pattern | Extend with extraction-specific rubric |

**Not used:**
- `sentence-transformers` / `bertscore` — too heavy for offline use
- `deepeval` / full `ragas` — OpenAI-first
- `keybert` — inactive since 2024

---

## Human Review Track

The consensus report surfaces low-scoring ingests in a review queue. Human review:
- Confirms or rejects pipeline ideas
- Marks gaps that the reference caught but pipeline missed
- Annotations written to `tests/eval_cases/extraction.yaml`

Over time this YAML becomes a regression dataset that runs even without API calls (lexical-only mode).

---

## Prior Art

- RAGAS evaluation framework — ROUGE + LLM judge patterns
- DeepEval — rubric judge prompt design
- Keyphrase extraction evaluation (arXiv 2303.15422)
- Karpathy LLM wiki confidence scoring (basis for `confidence.py`)
- UDCG metric (arXiv 2510.21440) — already in `retrieval.py`
