# PRD: Extraction Quality Evaluation

**Status:** Proposed  
**Priority:** P1  
**Research:** docs/research/extraction-eval.md  
**Architecture:** docs/architecture/extraction-eval.md  
**ADR:** docs/ADR/002-extraction-eval-strategy.md

---

## Problem Statement

MyMem extracts 3 key ideas from every ingested source and writes wiki pages for each. There is currently no eval that answers: *were the right ideas extracted?* Prompt tweaks, `max_concepts` changes, and model swaps have no measurable quality signal — we cannot tell if they made things better or worse.

---

## Goals

- **G1:** After every ingest, silently compare the pipeline's extracted ideas against a second independent Anthropic model's extraction and record a consensus score
- **G2:** Surface gaps (ideas the reference found that the pipeline missed) for human review
- **G3:** Let human review annotations grow a YAML regression dataset over time — no upfront manual labelling required
- **G4:** Zero impact on ingest latency — background fire-and-forget, same pattern as wiki RAG indexing

---

## Non-Goals

- Blocking ingest on eval results
- Evaluating wiki page body quality — that's `ingest_quality.py`
- Evaluating retrieval or answer quality — that's `retrieval.py` and `ragas_lite.py`
- Real-time dashboard scores per page (stored in DB, reviewable on demand)

---

## User Stories

- As a developer tuning the extraction prompt, I want to run `mymem eval --review` and see which ingests had low consensus scores and what ideas were missed
- As a user who just ingested a source, the system silently runs a reference extraction in the background and stores whether the pipeline got the main thesis and how much it agreed with an independent model
- As a curator, I want to approve/reject ideas in the review interface to build a regression dataset that catches future regressions automatically

---

## Acceptance Criteria

- [ ] `mymem/evals/extraction_consensus.py` — reference extractor + consensus scoring implemented and tested
- [ ] Background eval fires after every `ingest_source()` call (fire-and-forget, never blocks)
- [ ] Pipeline model and reference model are always different Anthropic models (auto-swap if config sets both to same model)
- [ ] Consensus score, grade (PASS/WARN/FAIL), gaps, and false_positives stored in `evals.db`
- [ ] `mymem eval --review` CLI command shows recent runs sorted by worst score first
- [ ] Human review annotations written to `tests/eval_cases/extraction.yaml`
- [ ] `pytest` coverage ≥ 80% on new modules (router mocked, no live API calls in tests)
- [ ] Background task failure never propagates to ingest — isolated try/except with logging

---

## Success Metrics

- Background eval completes within 3 seconds of ingest finishing
- A deliberately degraded extraction prompt scores ≥ 0.25 lower consensus than the current prompt
- After 10 human review sessions, `extraction.yaml` has ≥ 10 approved cases for regression testing

---

## Timeline

- Research: done (2026-05-28)
- Development: ~2 sessions (TDD)
- Testing: included

---

## Dependencies

- Anthropic API key (already required by existing router)
- `claude-haiku-4-5-20251001` as default reference model (already in router fallback chain)
- Existing: `mymem/evals/metrics.py` (ROUGE-1 for consensus matching)
- Existing: `mymem/evals/store.py` (result persistence)
- Existing: `mymem/pipeline/router.py` (LLM calls)
- Existing: `mymem/security/sanitize.py` (prompt safety)

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Both Anthropic models agree because they share training data biases | Medium | Medium | Use haiku vs sonnet (different sizes/tuning); consensus still catches structural gaps |
| Background eval fails silently without being noticed | Low | Low | Log warnings to structured log; store `grade: ERROR` in evals.db |
| Haiku API cost adds up across many ingests | Low | Low | ~$0.001 per ingest at haiku pricing; budget impact negligible |
| Human review interface adds too much friction | Low | Medium | CLI-first, simple approve/skip flow; no forced interaction |
