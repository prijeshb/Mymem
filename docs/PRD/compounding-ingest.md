# PRD: Compounding Ingest (Atomic Propositions + UPDATE Pipeline + Provenance)

**Status:** Proposed · **Priority:** P0 · **Target branch:** V1-0009
**Research:** docs/research/knowledge-moat-and-free-tier-routing.md
**ADR:** docs/ADR/011-compounding-ingest-and-provenance.md
**Architecture:** docs/architecture/compounding-ingest.md

## Problem Statement

MyMem's moat is the *maintained structure* layered on sources (deduped/merged facts, the
entity+wikilink graph, provenance, contradiction resolutions, trust). That moat only compounds
if each ingestion **mutates** existing knowledge. Today ingest is append-not-compound: when a
page slug collides (`ingest.py:315`), the body is recompiled *fresh* and overwritten — the existing
page is never read or merged, so new evidence on a known concept **replaces** rather than enriches.
There is no per-claim provenance (verbatim source spans), no contradiction handling, and no
confidence/trust. The result is the append-only decay the project's own `lint` already detects
(orphans, dupes, stale stubs).

## Goals

- G1: Every ingest emits **atomic propositions**, each grounded to a **verbatim source span**.
- G2: Ingest becomes an **UPDATE pipeline** — for each proposition, retrieve top-k similar existing
  knowledge and have the LLM choose **ADD / MERGE / SUPERSEDE / NOOP** (Mem0 pattern), instead of
  overwrite-by-slug.
- G3: Every claim carries **provenance** (source file + span) and a **confidence** score; contradicted
  facts are **superseded, not deleted** (bi-temporal).
- G4: No regression in idea-recall vs the current Map/Merge/Verify path; ingest cost increase < +20%.

## Non-Goals

- Drift-triggered re-summarization and `lint --consolidate` (Axis 2 multiplier) — **fast-follow**, not v1.
- Query-time RRF + small-to-big (Axis 3) — tracked separately; folds into ADR-008 Phase 3.
- Usage feedback loop / source-trust learning (Axis 4 multiplier) — **fast-follow** (schema reserved now).
- Full ontology / typed relationship edges — remains the planned ontology layer.
- Any new LLM provider or routing change — see ADR-012 (independent).

## User Stories

- As a wiki owner, when I ingest a second article about a concept I already have, the existing page
  is **enriched** (new evidence merged, sources appended), not blindly overwritten.
- As a wiki owner, when a new source **contradicts** an existing claim, the old claim is marked
  superseded (with a trail), not silently lost.
- As a question-asker, answers cite **specific source spans**, and low-confidence claims are flagged.
- As a maintainer, I can see in `lint` which claims are weakly grounded or contradicted.

## Acceptance Criteria

- [ ] AC1: Extraction emits atomic propositions with a verbatim `source_span`; `IdeaSchema` extended
  (back-compatible) and validated. Span must mechanically substring/fuzzy-match the source.
- [ ] AC2: New `data/claims.db` (or table in an existing DB) stores claims: `id, page_slug, text,
  source_id, source_span, confidence, valid_from, valid_to, superseded_by, created`.
- [ ] AC3: Before writing a page, the pipeline retrieves top-k similar existing propositions/pages
  (sqlite-vec) and the LLM returns one decision per candidate: ADD / MERGE / SUPERSEDE / NOOP.
- [ ] AC4: MERGE enriches the existing page body (preserves prior evidence + appends new sources);
  SUPERSEDE sets `valid_to`/`superseded_by` on the old claim and never hard-deletes it.
- [ ] AC5: `confidence` = f(source-trust default, corroboration count, recency); surfaced in `lint`
  and grayed in the UI (reuse broken-link UX). Source-trust defaults to 1.0 (learning deferred).
- [ ] AC6: Provenance cascades on source removal — extend the RAG `delete_source()` pattern to claims.
- [ ] AC7: Decision step has a dedicated eval (held-out judge agreement on MERGE/SUPERSEDE) wired into
  the existing extraction-consensus background eval.
- [ ] AC8: All existing tests pass; new modules ≥80% coverage; `lint` stays 100%. No LLM in tests
  (inject `llm_fn` / mock `router.call`).

## Success Metrics (ship/no-ship gates)

1. **Merge precision:** ADD/MERGE/SUPERSEDE/NOOP decisions agree with a held-out LLM judge ≥ target%.
2. **No recall regression:** idea-recall vs current Map/Merge/Verify on the existing eval set.
3. **Cost gate:** ingest token cost increase < +20% (router cost tracker).
4. **Moat health:** after N ingests, duplicate-concept rate ↓ and wikilink density ↑ vs baseline
   (reuse `_record_ingest_analytics`).
5. **No data loss:** SUPERSEDE never hard-deletes; superseded claims remain queryable.

## Timeline

- Research: DONE (2026-06-15)
- Phase 1 (propositions + source spans in extraction): ~1–2 sessions
- Phase 2 (claims store + provenance + confidence): ~2 sessions
- Phase 3 (UPDATE decision pipeline ADD/MERGE/SUPERSEDE/NOOP): ~2–3 sessions
- Phase 4 (bi-temporal supersede + lint surfacing + UI graying): ~1–2 sessions
- Phase 5 (decision eval + ship-gate A/B): ~2 sessions

## Dependencies

- Existing: sqlite-vec store, nomic-embed-text embedder, LLM router, `mymem/graph/`, eval framework,
  `_record_ingest_analytics`, `delete_source()`.
- No new third-party packages anticipated (decision step uses the existing router).
- Embedding path still requires local Ollama `nomic-embed-text` (shared constraint with graph work).

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Bad SUPERSEDE deletes good knowledge | Medium | High | Bi-temporal invalidate (never hard-delete); keep spans; gate on merge-precision eval before ship |
| Free-tier model judgment too weak for merge decisions | Medium | High | Test the decision step specifically; allow routing the decision task to the strongest available model; fall back to ADD-only when low confidence |
| Ingest cost/latency increase | Medium | Medium | Batch decisions; retrieve-then-decide only for top candidates; <+20% gate |
| Provenance row growth | Low | Medium | SQLite scales; prune via `delete_source()` cascade |
| Hallucinated propositions | Medium | Medium | Mechanical span-grounding (proposition must match a source span) |
| Scope creep into Axis 3/4 | Medium | Medium | v1 is strictly propositions→UPDATE→provenance; multipliers are fast-follows |
