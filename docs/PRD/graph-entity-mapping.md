# PRD: Graph Entity Mapping (Entity Layer + Graph-Assisted Retrieval)

**Status:** Approved for development · **Priority:** P0 · **Branch:** V1-0007
**Research:** docs/research/graph-entity-mapping.md · **ADR:** docs/ADR/007-graph-entity-mapping.md

## Problem Statement

MyMem's wikilinks connect *pages*, not *entities*. The same person/project/concept appears
under different surface forms across pages with no edge between them ("LLM" vs "Large
Language Models" pages fragment the graph). This caps multi-hop question answering, makes
the graph view less useful, and blocks the enterprise story (canonical entities are the
spine of Glean/Atlassian-class knowledge systems).

## Goals

- G1: Every ingest extracts typed entities grounded to source spans, resolved against the
  existing entity catalog (no near-duplicate explosion)
- G2: Multi-hop retrieval recall@k improves measurably vs vector-only baseline (A/B eval)
- G3: Wikilink density improves: unlinked mentions of existing pages get detected (lint)
  and suggested at ingest
- G4: Entity layer visible in the UI (graph view + wiki page entity chips)

## Non-Goals

- Full GraphRAG: NO community detection, NO community summaries (ADR-007 — wiki pages
  already are curated summaries; index.md is the global layer)
- Relationship-triple extraction in v1 (co-mentions + wikilinks are the edges; typed
  ontology edges deferred to the planned ontology layer)
- Graph database (SQLite recursive CTEs + networkx in-memory suffice at 1–10k pages)
- Multi-user permissions on graph traversal (single-user now; design notes recorded for later)

## User Stories

- As a wiki owner, when I ingest a source mentioning "S. Chen" and a page about "Sarah Chen"
  exists, the entity resolves to the same canonical entity and the pages get connected
- As a question-asker, "how does X relate to Y?" pulls in pages connected through shared
  entities, not just vector-similar chunks
- As a wiki maintainer, `mymem lint` shows unlinked mentions — pages that name an existing
  page's title/alias without linking it
- As an enterprise evaluator, I can see canonical entities (people, projects, systems)
  extracted consistently across documents

## Acceptance Criteria

- [ ] AC1: Ingest extracts typed entities (closed type set: person, project, system,
  organization, concept) with description + source span, stored in `data/graph.db`
- [ ] AC2: 3-tier resolution (exact/alias → fuzzy+embedding → LLM judge) runs at write
  time; %-singleton-entities and entities-per-page tracked as explosion alarms
- [ ] AC3: Pages get `aliases: [...]` frontmatter (LLM-proposed, user-editable via existing
  EditMetaPanel)
- [ ] AC4: Query pipeline does 1-hop expansion over (wikilinks ∪ shared-entity edges),
  fused via RRF into existing hybrid retrieval
- [ ] AC5: Entity extraction eval (consensus + span-grounding) runs after ingest like
  extraction consensus does today; results in evals dashboard
- [ ] AC6: Multi-hop self-supervised eval set (KGQAGen pattern, 50–100 cases) with A/B
  runner: vector-only vs vector+graph
- [ ] AC7: Lint reports unlinked mentions (zero LLM cost, pure Python)
- [ ] AC8: All existing tests pass; new modules ≥80% coverage; lint stays 100%
- [ ] AC9: Existing wiki migrates via 3-tier backfill (structural seed → classify/alias
  batch → resumable full extraction via `mymem graph backfill`); Tier 1 is idempotent
  and doubles as a graph repair command

## Success Metrics (ship/no-ship gates)

1. Multi-hop recall@k: significant improvement in A/B
2. Single-hop retrieval eval: no regression (documented GraphRAG failure mode)
3. Ingest token cost: < +20% (router cost tracker)
4. Entity explosion: < 30% singleton entities after 50 ingests

## Timeline

- Research: DONE (2026-06-10)
- Phase 1 (entity store + extraction + resolution): ~3 sessions
- Phase 1.5 (migration: Tier-1 structural seed + Tier-2 classify/alias backfill): ~1 session
- Phase 2 (alias frontmatter + lint unlinked mentions): ~1 session
- Phase 3 (retrieval integration + RRF): ~2 sessions
- Phase 4 (evals + A/B): ~2 sessions
- Phase 5 (UI: graph overlay + entity chips): ~1–2 sessions

## Dependencies

- `networkx==3.6.1` (BSD-3, zero deps), `rapidfuzz==3.14.5` (MIT, zero deps)
- Existing: sqlite-vec, nomic-embed-text embedder, LLM router, eval framework
- NOTE: embeddings currently require local Ollama (`nomic-embed-text`) — only remaining
  local dependency; cloud embedding fallback is a separate task if needed

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Entity explosion (near-duplicate entities) | High | High | Write-time 3-tier resolution; closed type set; explosion metrics alarmed in evals |
| Single-hop retrieval regression | Medium | High | Regression gate in eval A/B; graph contributes via RRF (additive, can't displace strong vector hits entirely) |
| Ingest cost/latency increase | Medium | Medium | Entity extraction folded into existing extraction LLM call where possible; <20% gate |
| Hallucinated entities | Medium | Medium | Mechanical span-grounding check (entity name must fuzzy-match source span) |
| Embedding path requires local Ollama | Medium | Medium | Resolution degrades gracefully to exact+fuzzy tiers when embedder unavailable |
| Stale entities after page edits/deletes | Low | Medium | Mentions keyed by page slug; delete/archive hooks clean mentions (same pattern as RAG delete_source) |
