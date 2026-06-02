# System Design: Extraction Quality Evaluation

## Overview

A dual-track system that evaluates whether MyMem's ingest pipeline extracts the right ideas from a source. Track 1 runs silently after every ingest: a second independent Anthropic model re-extracts ideas from the same source, and the two sets are compared by consensus. Track 2 is human review: the consensus report surfaces gaps for a human to approve/reject, and over time those annotations grow a frozen YAML dataset.

---

## Architecture Diagram

```
Ingest completes (ingest_source returns)
        │
        └──► asyncio.ensure_future(_eval_extraction_background())
                        │
                        ├──► Ideas A: already extracted by pipeline
                        │    (pipeline model = sonnet-4-6 or whatever compile resolves to)
                        │
                        └──► Ideas B: re-extract via reference model
                             (reference model = haiku-4-5, different from pipeline model)
                                        │
                                  compare A vs B
                                  ├─ consensus_score  (ROUGE-1 overlap of summaries)
                                  ├─ gaps             (ideas in B not matched in A)
                                  ├─ false_positives  (ideas in A not matched in B)
                                  └─ store in evals.db → "extraction_consensus" table


mymem eval --review                  (human review track)
        │
        └──► load recent runs from evals.db
             show per-source: pipeline ideas vs reference ideas vs gaps
             human marks: approve ✓ / reject ✗ / add missing idea +
             annotations → tests/eval_cases/extraction.yaml (grows over time)
```

---

## Model Configuration

```python
# Pipeline model: resolved from router via task="compile"
# e.g. claude-sonnet-4-6 (set in config.yaml anthropic_models.compile)

# Reference model: always a different model from pipeline
# Default: claude-haiku-4-5-20251001
# Configurable via config.yaml:
#   eval:
#     reference_model: claude-haiku-4-5-20251001

REFERENCE_MODEL = "claude-haiku-4-5-20251001"
```

The reference model must differ from the compile model. If `config.yaml` sets compile to haiku, the reference automatically upgrades to sonnet. This independence is the core signal — agreement between two different models = high-confidence idea.

---

## Reference Extractor Prompt

Stricter than the pipeline prompt — designed to be independent and analytical:

```
You are an independent knowledge evaluator.
Given a source document, identify the concepts a knowledgeable reader MUST understand.
Be strict: if two concepts overlap significantly, keep only the more distinct one.

For each concept output:
  "title": what someone would search for to find this concept (3-8 words, searchable)
  "summary": 2-3 sentences — core insight, key facts, why it matters
  "main_thesis": true if this concept captures the single main point of the source
  "tags": 2-4 lowercase tags
  "domain": one of spiritual|tech|finance|health|reminder|research|personal|creative|business|misc

Output only valid JSON array. Max {max_concepts} ideas.
```

Key differences from pipeline prompt:
- "independent knowledge evaluator" framing (not a wiki author)
- "Be strict: if two concepts overlap, keep only the more distinct one"
- `main_thesis: true/false` flag — tells us which idea the reference considers the main point
- "what someone would search for" — searchability baked into the title instruction

---

## Consensus Scoring

```python
@dataclass(frozen=True)
class IdeaMatch:
    pipeline_title: str
    reference_title: str
    rouge1_score: float      # how similar the summaries are
    matched: bool            # True if rouge1_score >= MATCH_THRESHOLD

MATCH_THRESHOLD = 0.25  # ROUGE-1 recall for two ideas to be considered "same concept"

@dataclass(frozen=True)
class ExtractionConsensusResult:
    source_id: str
    pipeline_model: str
    reference_model: str
    pipeline_ideas: list[dict]      # raw extracted ideas
    reference_ideas: list[dict]     # raw reference ideas
    matches: list[IdeaMatch]
    consensus_score: float          # matched / max(len(A), len(B))
    gaps: list[str]                 # reference titles not matched in pipeline
    false_positives: list[str]      # pipeline titles not matched in reference
    thesis_captured: bool           # did pipeline capture the idea flagged main_thesis=true?
    grade: str                      # PASS / WARN / FAIL
```

**Grade thresholds:**
- PASS: consensus_score ≥ 0.67 AND thesis_captured = True
- WARN: consensus_score ≥ 0.50 OR thesis_captured = True (but not both)
- FAIL: consensus_score < 0.50 OR thesis_captured = False

---

## Database Schema

New table in `data/evals.db`:

```sql
CREATE TABLE extraction_consensus (
    id          INTEGER PRIMARY KEY,
    run_at      TEXT NOT NULL,          -- ISO datetime
    source_id   TEXT NOT NULL,          -- source filename or URL
    source_type TEXT NOT NULL,
    pipeline_model  TEXT NOT NULL,
    reference_model TEXT NOT NULL,
    consensus_score REAL NOT NULL,
    thesis_captured INTEGER NOT NULL,   -- 0/1
    grade           TEXT NOT NULL,      -- PASS/WARN/FAIL
    gaps_json       TEXT NOT NULL,      -- JSON list of gap titles
    false_pos_json  TEXT NOT NULL,      -- JSON list of false positive titles
    full_result_json TEXT NOT NULL      -- full ExtractionConsensusResult as JSON
);
```

---

## Human Review Track

```bash
mymem eval --review
# Shows last 20 runs, sorted by consensus_score ASC (worst first)
# Output per run:
#   Source: my-article.md  [2026-05-28]  Score: 0.33  Grade: FAIL
#   Pipeline found:  [Transformer Architecture] [Attention Mechanism] [Positional Encoding]
#   Reference found: [Self-Attention Model] [Multi-Head Attention] [Feed-Forward Layers]
#   Gaps (missed):   [Feed-Forward Layers]
#   ✓ approve all  /  ✗ reject  /  + add idea  /  s skip

mymem eval --review <source_id>
# Review a specific source interactively
```

Approvals are written to `tests/eval_cases/extraction.yaml`:
```yaml
- source_id: "attention-is-all-you-need.pdf"
  source_type: "paper"
  approved_at: "2026-05-28"
  verified_ideas:
    - title: "Transformer Architecture"
      approved: true
    - title: "Feed-Forward Layers"
      approved: true
      note: "pipeline missed this — gap caught by reference extractor"
  main_thesis_captured: true
```

Over time this YAML becomes a regression dataset — new ingest runs can be checked against it.

---

## New Files

```
mymem/evals/
  extraction_consensus.py    # reference extractor, consensus scoring, ExtractionConsensusResult
  review.py                  # human review CLI output formatter + YAML annotation writer

tests/eval_cases/
  extraction.yaml            # grows over time via --review approvals (starts empty)
```

**Modified files:**
- `mymem/pipeline/ingest.py` — add `asyncio.ensure_future(_eval_extraction_background(...))` after ingest completes
- `mymem/evals/runner.py` — add `ExtractionConsensusResult` to `EvalReport`
- `mymem/evals/store.py` — add `save_extraction_consensus()` function
- `mymem/cli.py` — add `eval --review` subcommand

---

## Performance

- Reference extraction adds ~1–2s latency, runs fully in background (fire-and-forget)
- Does not block the ingest response — `ingest_source()` returns before this runs
- Same pattern as `_rag_index_wiki()` which is already fire-and-forget

---

## Security

- Source text passed to reference model goes through the existing `sanitize_for_prompt()` — same as pipeline
- No new security surface introduced
- `extraction.yaml` is size-capped at 512 KB before load
