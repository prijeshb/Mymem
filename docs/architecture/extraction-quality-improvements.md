# System Design: Extraction Quality Improvements

## Overview

Four targeted changes to the ingest → extraction → eval pipeline, in priority order: (1) map-reduce extraction over chunks so long sources are fully covered, (2) embedding-cosine consensus scoring replacing ROUGE-1, (3) a gleaning loop that adds one "what did you miss?" LLM turn, and (4) post-extraction semantic deduplication. No new background infrastructure — all changes slot into existing `ingest.py` and `extraction_consensus.py` with one new module (`extraction_dedup.py`).

---

## Architecture Diagram

```
Source text
    │
    ├─ len ≤ 6000 chars ──────────────────────────────┐
    │                                                  │
    └─ len > 6000 chars                               │
         │                                            │
         └── ChunkSplitter (existing)                 │
              splits into N overlapping chunks        │
                    │                                 │
              [Map] for each chunk:                   │
                LLM extract → [{idea, confidence}]    │
                    │                                 │
              [Merge] compile model merges chunk      │
                results, resolves by confidence       │
                    │                                 │
                    └─────────────────────────────────┘
                                │
                         raw_ideas: list[dict]
                                │
                         [Gleaning loop]
                          1 extra LLM turn:
                         "What did you miss?"
                         → append new ideas
                                │
                         gleaned_ideas: list[dict]
                                │
                   [extraction_dedup.py — new]
                   embed all idea titles+summaries
                   cluster cosine sim > 0.85
                   keep highest-confidence per cluster
                                │
                         final_ideas: list[dict]
                                │
              ┌─────────────────┴──────────────────┐
              │                                    │
         write wiki pages              [background eval]
                                   asyncio.ensure_future(
                                   _eval_extraction_background())
                                              │
                                    reference LLM re-extracts
                                              │
                                    _match_ideas() — NEW:
                                    embedding cosine sim ≥ 0.78
                                    (replaces ROUGE-1 F1)
                                              │
                                    ExtractionConsensusResult
                                    + uniqueness_score (new field)
                                    + factualness_scores (new field)
                                              │
                                    save_extraction_consensus()
```

---

## Components

### New Module: `mymem/pipeline/extraction_dedup.py`

Single function:

```python
async def dedup_ideas(
    ideas: list[dict],
    embedder: Callable[[list[str]], Awaitable[list[list[float]]]],
    threshold: float = 0.85,
) -> list[dict]:
    """
    Remove semantically redundant ideas.
    For pairs with cosine sim > threshold, keep the one with longer summary
    (proxy for higher information density).
    Returns new list — never mutates input.
    """
```

- Uses `embedder.py::embed_texts()` for vectors
- Uses `sklearn.metrics.pairwise.cosine_similarity` for pairwise matrix (already installed)
- Entropy gate: skip embedding for ideas with title < 4 tokens (too short to be meaningful dedup candidates)
- Returns a new list (immutable pattern); logs each merge decision at DEBUG level
- Gracefully degrades: if Ollama is offline, returns input unchanged with a warning log

### Modified: `mymem/pipeline/ingest.py`

**Change 1 — Map-reduce for long sources:**

```python
# Before (current):
source_preview = source_text[:6000]
ideas = await _extract_ideas(source_preview, ...)

# After:
if len(source_text) <= 6000:
    ideas = await _extract_ideas(source_text, ...)
else:
    ideas = await _extract_ideas_map_reduce(source_text, splitter, router, ...)
```

`_extract_ideas_map_reduce()`:
1. `ChunkSplitter.split(source_text)` → chunks
2. For each chunk: `_extract_ideas(chunk, ...)` → `[{...idea, confidence: int}]` (confidence added to prompt schema)
3. Merge: call `merge` model with all chunk idea-lists, confidence scores, instruction to deduplicate by concept identity and rank by confidence
4. Returns merged `list[dict]`

The extraction prompt gains a `confidence` field:
```
"confidence": integer 1-5 — how well this chunk's text supports this concept
  (5 = fully stated, 3 = inferred from context, 1 = only tangentially mentioned)
```

**Change 2 — Gleaning loop:**

```python
# After main extraction (single-pass or map-reduce result):
ideas = await _extract_ideas(source_text, ...)
gleaned = await _glean_ideas(source_text, existing_ideas=ideas, llm_fn=...)
ideas = ideas + [i for i in gleaned if i not in ideas]
```

`_glean_ideas()` sends a second LLM turn:
```
System: [same extraction system prompt]
User: [source text]
Assistant: [ideas already extracted as JSON]
User: "Review the source again. List any important concepts that were missed
       in the extraction above. If nothing was missed, return an empty JSON array []."
```
Returns empty list if LLM returns `[]` — capped at 1 gleaning turn.

**Change 3 — Remove fixed max_concepts:**

```python
# Before:
"Output only valid JSON array. Max {max_concepts} ideas."

# After:
"Output only valid JSON array. Include every concept that a knowledgeable reader
 must understand. Omit any concept that is: (a) already covered by another concept
 in your list, (b) only tangentially mentioned in the source, or
 (c) not supported by specific text in the source."
```

**Change 4 — Dedup pass:**

```python
# After gleaning, before writing wiki pages:
ideas = await dedup_ideas(ideas, embedder=embed_texts)
```

### Modified: `mymem/evals/extraction_consensus.py`

**Replace ROUGE-1 with embedding cosine:**

```python
# Before:
MATCH_THRESHOLD = 0.20  # ROUGE-1 F1

def _match_ideas(pipeline_ideas, reference_ideas):
    for p_idea in pipeline_ideas:
        best_score = max(rouge1_f1(_idea_text(p_idea), _idea_text(r)) for r in reference_ideas)
        ...

# After:
EMBED_MATCH_THRESHOLD = 0.78  # cosine similarity on nomic-embed-text

async def _match_ideas_semantic(
    pipeline_ideas: list[dict],
    reference_ideas: list[dict],
    embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]],
) -> list[IdeaMatch]:
    if not pipeline_ideas or not reference_ideas:
        return []
    p_texts = [_idea_text(i) for i in pipeline_ideas]
    r_texts = [_idea_text(i) for i in reference_ideas]
    p_vecs = await embed_fn(p_texts)
    r_vecs = await embed_fn(r_texts)
    sim_matrix = cosine_similarity(p_vecs, r_vecs)  # shape: (|pipeline|, |reference|)
    matches = []
    for idx, p_idea in enumerate(pipeline_ideas):
        best_idx = int(sim_matrix[idx].argmax())
        best_score = float(sim_matrix[idx, best_idx])
        matches.append(IdeaMatch(
            pipeline_title=str(p_idea.get("title", "")),
            reference_title=str(reference_ideas[best_idx].get("title", "")),
            rouge1_score=best_score,   # field repurposed — value is now cosine sim
            matched=best_score >= EMBED_MATCH_THRESHOLD,
        ))
    return matches
```

**Add uniqueness score to `ExtractionConsensusResult`:**

```python
@dataclass(frozen=True)
class ExtractionConsensusResult:
    ...
    uniqueness_score: float    # 1.0 - mean_pairwise_cosine_sim within pipeline ideas
                               # high = diverse ideas; low = redundant/overlapping
```

Fallback: if Ollama offline, fall back to ROUGE-1 matching with a warning log. This preserves all existing tests.

### Modified: `mymem/evals/metrics.py`

Add two pure functions (no I/O):

```python
def embedding_cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""

def mean_pairwise_cosine(vectors: list[list[float]]) -> float:
    """Mean pairwise cosine similarity within a set of vectors. Useful for uniqueness scoring."""
```

---

## Database Changes

`extraction_consensus` table gets one new column (backward-compatible):

```sql
ALTER TABLE extraction_consensus
ADD COLUMN uniqueness_score REAL NOT NULL DEFAULT 0.0;
```

`_ensure_consensus()` in `store.py` handles the migration via existing `PRAGMA table_info` pattern (same as `full_result_json` was added).

---

## Data Flow

1. User runs `mymem ingest <source>` or POST `/api/ingest`
2. `ingest_source()` reads source text
3. If `len(source_text) > 6000`: `_extract_ideas_map_reduce()` → chunk → map → merge
4. Else: `_extract_ideas()` single pass
5. Gleaning turn: one extra LLM call for missed concepts
6. `dedup_ideas()`: embed all, remove cosine sim > 0.85 pairs
7. Write wiki pages (unchanged)
8. Fire-and-forget: `_eval_extraction_background()` — reference LLM re-extracts, embedding-cosine matching, store result

---

## Security Considerations

- Source text passed to gleaning turn goes through existing `sanitize_for_prompt()` (same as main extraction)
- Dedup embedding calls use the same Ollama/local endpoint as the existing RAG embedder — no new external surface
- `extraction_dedup.py` has no I/O beyond the embedder — no new injection surface
- No new API endpoints

---

## Performance Considerations

- Map-reduce adds `ceil(len(source) / chunk_size)` LLM calls for long sources — all in the existing background `asyncio.ensure_future` context (does not block ingest response)
- Gleaning adds 1 LLM call per ingest; short sources (< 6000 chars) have no map-reduce cost
- Dedup embedding: `embed_texts([N titles+summaries])` is one Ollama batch call — typically < 100ms
- The consensus eval already runs in background; adding embedding calls there adds ~100ms per eval
- `sklearn.cosine_similarity` on a 5×5 matrix is negligible

**No changes to the hot path** — `ingest_source()` returns before any eval or dedup runs for the background eval track. The dedup pass runs inline (before wiki writes) but adds < 500ms for typical 5-concept extractions.

---

## API Contract Changes

None — no new endpoints. `GET /api/stats` and the evals UI already read from `evals.db`; the `uniqueness_score` field will appear automatically in `full_result_json`.

---

## Testing Strategy

### Unit tests (new in `tests/test_extraction_consensus.py`)

- `test_match_ideas_semantic_similar` — two semantically identical ideas with different wording match at cosine sim > 0.78
- `test_match_ideas_semantic_divergent` — unrelated ideas score < 0.78
- Mocked `embed_fn` that returns fixed vectors for deterministic tests

### Unit tests (new in `tests/test_extraction_dedup.py`)

- Dedup removes the more information-poor of two highly similar ideas
- Dedup is a no-op when all ideas are semantically distinct
- Offline embedder (raises exception) → dedup returns input unchanged
- Entropy gate: ideas with 1-2 token titles skip embedding comparison

### Unit tests (new in `tests/test_ingest_extraction.py`)

- Map-reduce: mocked splitter + mocked LLM → ideas from all chunks appear in output
- Gleaning loop: mocked LLM returns one new idea on gleaning turn → appended
- Gleaning loop: LLM returns `[]` → no ideas added
- Prompt no longer contains `max_concepts` string

### Integration tests

- `test_ingest.py` additions: ingest a mock 10,000-char source → verify wiki pages are written (ideas not truncated to first-chunk only)

### Coverage target

80%+ on `pipeline/ingest.py`, `evals/extraction_consensus.py`, `pipeline/extraction_dedup.py` (new)

---

## New Files

```
mymem/pipeline/extraction_dedup.py    # dedup_ideas() — post-extraction semantic dedup
tests/test_extraction_dedup.py        # unit tests for dedup module
```

## Modified Files

```
mymem/pipeline/ingest.py              # map-reduce, gleaning loop, dedup call, prompt change
mymem/evals/extraction_consensus.py   # embedding-cosine matching, uniqueness_score field
mymem/evals/metrics.py               # embedding_cosine_sim(), mean_pairwise_cosine()
mymem/evals/store.py                 # uniqueness_score column migration
tests/test_extraction_consensus.py    # new semantic matching tests
tests/test_ingest.py                 # map-reduce + gleaning integration tests
pyproject.toml                       # add instructor (optional: evals extra)
```
