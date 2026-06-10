# PRD: Extraction Quality Improvements

## Problem Statement

MyMem's ingest pipeline extracts key ideas from source documents and writes them as wiki pages. Currently, extraction quality has three structural defects: (1) sources longer than ~4 pages are silently truncated to 6000 chars, losing the majority of the content; (2) the LLM sometimes extracts semantically overlapping concepts as separate ideas; (3) the consensus eval uses ROUGE-1, which has near-zero correlation with human judgment of concept coverage. The result is a wiki that is incomplete for long sources, noisy with duplicates, and evaluated with an unreliable metric.

## Goals

- G1: Extraction recall on sources > 6000 chars improves — all pages of a document contribute to extracted ideas, not just the first ~4 pages
- G2: Post-extraction deduplication eliminates semantically overlapping concepts (cosine sim > 0.85 within extracted set)
- G3: Consensus eval replaces ROUGE-1 with embedding cosine similarity — eval scores correlate with actual concept coverage
- G4: A gleaning loop adds one "what did you miss?" LLM turn — recall improves for all source lengths at low cost
- G5: Fixed `max_concepts=N` is removed — extraction completeness is driven by quality floor, not count ceiling

## Non-Goals

- Full ontology extraction (typed relationships) — covered by the planned ontology layer
- Live re-extraction of previously ingested sources — improvements apply to new ingests only
- UI changes to the evals dashboard — display changes are out of scope for this feature
- Replacing the consensus eval's dual-LLM architecture — the fire-and-forget background design stays

## User Stories

- As a user ingesting a long paper, I want all sections to contribute to the extracted wiki pages, so the wiki is complete rather than only covering the abstract and introduction
- As a user querying the wiki, I want distinct concepts as separate pages rather than the same idea under multiple slightly different titles
- As a developer running evals, I want consensus scores that actually predict when extraction missed something important
- As a user ingesting a source with 2 key ideas, I want exactly 2 ideas extracted, not padded to 5 with weaker supporting points

## Acceptance Criteria

- [ ] AC1: Ingesting a source > 6000 chars produces wiki pages from concepts across the full document, not only the first 6000 chars. Verified by ingesting a 10,000+ char source and checking that ideas from the latter half appear in the wiki.
- [ ] AC2: Post-ingest dedup pass runs for every ingest; no two wiki pages written in the same ingest have embedding cosine sim > 0.85 on their title+summary text.
- [ ] AC3: `ExtractionConsensusResult.consensus_score` is computed using embedding cosine similarity (threshold 0.78), not ROUGE-1 F1.
- [ ] AC4: The gleaning loop fires once after main extraction and appends any new ideas returned by the LLM.
- [ ] AC5: The extraction prompt no longer specifies a fixed `max_concepts`; it specifies minimum quality criteria instead.
- [ ] AC6: All existing tests in `test_extraction_consensus.py` still pass. New tests cover: map-reduce extraction (mocked splitter), dedup pass, gleaning loop (mocked second LLM turn), embedding-cosine matching.
- [ ] AC7: `pytest --cov=mymem/evals/extraction_consensus --cov=mymem/pipeline/ingest` ≥ 80% coverage on changed modules.

## Success Metrics

- Consensus eval PASS rate on a set of 10 manually ingested articles improves from baseline
- Mean number of duplicate concepts per ingest batch (cosine sim > 0.85 pairs) drops to near 0
- No regression in ingest speed: map-reduce adds LLM calls but they run against the existing `compile` model; wall-clock ingest time for a 2000-char source should not increase

## Timeline

- Research: done (this document)
- Development: 2–3 sessions
- Testing: included in development (TDD)

## Dependencies

- `mymem/rag/embedder.py` — `embed_texts()` function (exists, DONE)
- `scikit-learn` — `cosine_similarity` (already in `pyproject.toml`)
- `mymem/pipeline/splitter.py` — `ChunkSplitter` (exists, DONE)
- `instructor` (new dep, lightweight) — optional; improves JSON parsing robustness

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Gleaning loop doubles LLM calls per ingest | Medium | Low | Loop is capped at 1 extra turn; only fires if main extraction returns ≥ 1 idea |
| Embedding-cosine dedup has false positives (merges distinct concepts) | Low | Medium | Threshold 0.85 is conservative; log every dedup decision; threshold tunable in config.yaml |
| Map-reduce extraction increases latency for long sources | Medium | Low | Already fire-and-forget background; user sees no latency increase |
| Ollama offline → embed_texts() fails → dedup skipped | Medium | Low | Dedup is best-effort; ingest completes without it; log warning |
| ROUGE-1 removal breaks existing eval fixtures | Low | Low | Test fixtures use `score_consensus()` directly; update expected thresholds in test data |
