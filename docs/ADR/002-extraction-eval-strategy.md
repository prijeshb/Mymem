# ADR 002: Extraction Evaluation Strategy

## Status: Proposed

## Context

MyMem evaluates retrieval quality, wiki page richness, and answer faithfulness — but not the extraction step itself. Changes to the extraction prompt, `max_concepts`, or model have no measurable quality signal.

We need to choose:
1. What constitutes "ground truth" for good extraction
2. Whether to use a dataset, a second LLM, or both
3. Which models to use and how to keep them independent
4. Whether eval is on-demand or automatic

## Decision

**Dual-track: automatic multi-LLM consensus (background, after every ingest) + human review that grows a YAML regression dataset over time.**

Both tracks use Anthropic cloud models only (no Ollama). Pipeline uses `claude-sonnet-4-6` for extraction; reference uses `claude-haiku-4-5`. They are always different models to ensure independence.

## Rationale

### Why two different LLMs instead of a frozen dataset?

A frozen hand-curated dataset requires upfront manual work per source (20–30 min to annotate 10 cases). The multi-LLM approach generates a signal on every single ingest with zero manual effort — if two models independently identify the same concept, it almost certainly is an important idea.

The frozen dataset still gets built — but organically through the human review track, not as a prerequisite.

### Why Anthropic cloud models (not Ollama)?

The user's stack uses Anthropic cloud models for production extraction. Using local Ollama models as the reference would introduce a different quality gap (small local model vs. large cloud model) that conflates model capability differences with extraction quality differences. Two Anthropic models of different sizes are more comparable and their agreement is a cleaner signal.

### Why haiku as reference?

- Cheapest Anthropic model — ~$0.001 per ingest at current pricing
- Genuinely different architecture and size from Sonnet — provides independent perspective
- Fast — adds < 2s to background task
- Already in the router fallback chain — no new configuration

### Why background fire-and-forget instead of blocking?

Ingest latency matters for the user experience. The consensus eval is diagnostic, not gating — a low score doesn't stop the wiki page from being written. Same pattern as `_rag_index_wiki()` which is already fire-and-forget.

### Why grow the YAML dataset through review instead of building it upfront?

Upfront annotation requires knowing in advance which sources to annotate. With the background track running on every ingest, low-scoring sources surface automatically — the human review queue is pre-populated with the cases that most need attention.

## Alternatives Considered

1. **Hand-curated YAML dataset only** — rejected: too slow to build; doesn't scale to new sources automatically
2. **Self-supervised (re-ingest, check consistency)** — rejected: circular; validates consistency not quality
3. **Ollama model as reference** — rejected: user stack is cloud-only; mixing local/cloud conflates capability gap with extraction quality
4. **Same Anthropic model as both pipeline and reference** — rejected: same model with same prompt will produce identical output; no signal
5. **Full RAGAS framework** — rejected: OpenAI-first, heavy; we already have RAGAS-lite that uses our router and this is a different concern (extraction, not retrieval)

## Consequences

- **Positive:** Every ingest produces a quality signal with zero extra user effort
- **Positive:** Gaps are surfaced automatically — no need to know in advance what to test
- **Positive:** Regression dataset grows through use, not through a separate annotation sprint
- **Negative:** Haiku API cost per ingest (~$0.001); negligible but non-zero
- **Negative:** Two models from the same provider still share some training biases; not fully independent
- **Negative:** If Anthropic API is down, the background eval silently fails (logged, not blocking)
- **Risk:** Consensus score inflation if both models share extraction biases toward certain concept types (e.g. both over-extract technical terms, both under-extract emotional/philosophical nuance)
