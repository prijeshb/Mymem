# MyMem — Project Context

## Stack
- Python 3.11+ (strict mypy), FastAPI, Typer/Rich CLI
- React 18 + TypeScript frontend (Vite, Tailwind CSS v3)
- SQLite (sqlite-vec for RAG), markdown files for wiki
- LLM providers: Ollama (default), Anthropic, OpenAI, Groq, NVIDIA, Gemini, OpenRouter
- Testing: pytest + pytest-asyncio + pytest-cov (≥ 80% required)

## Current Branch
- `V1-0010` — compounding ingest (ADR-011) + graph re-key. master is at the full project + README.

## Architecture Decisions

| ADR | Decision | Status |
|-----|----------|--------|
| ADR-001 | RAG chunking strategy | Accepted |
| ADR-002 | Extraction eval strategy (dual-LLM consensus) | Accepted |
| ADR-003 | Wiki storage format (MD over HTML) | Accepted |
| ADR-004 | External integrations (Obsidian, NotebookLM, Notion) | Accepted |
| ADR-005 | Agent decomposition strategy (4 agents + 2 subagents) | Accepted |
| ADR-006 | Extraction quality improvements | Accepted |
| ADR-007 | Lightweight entity layer instead of full GraphRAG | Accepted |
| ADR-008 | Graph implementation decisions (storage, resolution tiers, thresholds, hooks) | Accepted |
| ADR-009 | Social source readers (X syndication API + nitter fallback, Reddit .json) | Accepted |
| ADR-010 | Free-tier provider routing (NVIDIA primary, per-task models, cross-provider rate-limit swap) | Accepted |
| ADR-011 | Compounding ingest (atomic propositions + ADD/MERGE/SUPERSEDE/NOOP + provenance) | Proposed |
| ADR-012 | Quota-aware free-tier routing (cooldown registry, token-bucket, multi-key, degrade-to-local) | Proposed |
| ADR-013 | Stable page identity (opaque ULID `id` vs slug vs title; resolution + redirects) — prerequisite for ADR-011 | Accepted |
| ADR-014 | Page identity implementation decisions (mint_id home, auto-mint choke point, exact resolution, scope fence) | Accepted |
| ADR-015 | Compounding ingest implementation decisions (Phase 1: span grounding substring+fuzzy, blank-not-drop, persist in Phase 2) | Accepted |

## Completed Features

| Feature | Module | Notes |
|---------|--------|-------|
| CLI (ingest/query/lint/serve/introspect) | `mymem/cli.py` | DONE |
| Wiki page management | `mymem/wiki/` | DONE |
| Multi-LLM router + fallback chain | `mymem/pipeline/router/` | DONE |
| RAG store + embedder (nomic-embed-text 768-dim) | `mymem/rag/` | DONE |
| FastAPI + React SPA | `mymem/web/`, `frontend/` | DONE |
| Eval framework (chunking, wiki quality, retrieval, RAGAS-lite) | `mymem/evals/` | DONE |
| Extraction consensus eval (dual-LLM, cosine matching) | `mymem/evals/extraction_consensus.py` | DONE |
| Obsidian vault integration | `mymem/cli.py` (obsidian subcommand) | DONE |
| NVIDIA provider | `mymem/pipeline/llm.py` | DONE |
| Evals UI (extraction consensus history table) | `frontend/src/pages/EvalsPage.tsx` | DONE — fixed stale build |
| Source reading extracted to Strategy pattern | `mymem/pipeline/readers.py` | DONE |
| LLM provider refactored to Strategy/Bridge pattern | `mymem/pipeline/llm.py` | DONE |
| Evaluator[T] Generic ABC for eval framework | `mymem/evals/_base.py` | DONE |
| Provider credentials abstraction | `mymem/pipeline/router/_credentials.py` | DONE |
| Map-reduce extraction for long sources | `mymem/pipeline/ingest.py` | DONE |
| Idea dedup + ranking (cosine sim > 0.85) | `mymem/pipeline/ingest.py` | DONE |
| Evals API endpoints (/api/evals/extraction, /api/evals/summary) | `mymem/web/routes/api.py` | DONE |
| Eval suite summary grid (cards, staleness, never-run states) | `frontend/src/components/EvalSuiteGrid.tsx` | DONE |
| Eval run trigger (POST /api/evals/run + UI button, RAGAS flag) | `mymem/web/routes/api.py`, `EvalsPage.tsx` | DONE |
| Grades for wiki_quality + chunking summaries | `mymem/evals/ingest_quality.py`, `chunking.py` | DONE |
| Verbatim source spans + grounding (compounding ingest P1) | `mymem/pipeline/ingest.py` | DONE — ADR-015 D1-D3 |
| Bi-temporal claims store (compounding ingest P2) | `mymem/knowledge/claims.py` (`data/claims.db`) | DONE — 100% cov, ADR-015 D4-D7 |
| Reconcile decision core (compounding ingest P3a) | `mymem/pipeline/reconcile.py` | DONE — 100% cov, ADR-015 D8-D12 |
| Claim retrieval (compounding ingest P3b) | `mymem/knowledge/retrieval.py` | DONE — 100% cov |
| Retrieve→decide→apply orchestrator + ingest wiring (P3c) | `mymem/pipeline/compounding.py`, `ingest.py` | DONE — 100% cov |
| Claims-section wiki rendering + sync (P3 D13) | `mymem/knowledge/render.py`, `ingest.py` | DONE — 100% cov, ADR-015 D13-D14 |
| Decision-agreement eval / ship gate (P3 D15) | `mymem/evals/decision_agreement.py` | DONE — 100% cov, ADR-015 D15-D16 |
| Decision-eval live capture + background wiring (P3 D17) | `mymem/pipeline/compounding.py`, `ingest.py`, `evals/decision_agreement.py` | DONE — ADR-015 D17 |
| Cross-page claim retrieval — global vec index (D19) | `mymem/knowledge/claim_index.py`, `retrieval.py`, `compounding.py`, `cli.py` | DONE — 100% cov, ADR-015 D19 |

## Security Status
- **Last Audit**: 2026-06-11
- **Verdict**: PASS
- **Open Issues**: 0 critical, 0 high, 2 medium (SSRF localhost scope, rate limiting), 3 low
- **Fixed This Session**: none needed — scan clean
- **Compliance**: local-first tool, no PII handling

## Known Gaps

1. `mymem/evals/review.py` — human review CLI for extraction eval not built
2. SSRF: user-supplied URLs accepted without allowlist (acceptable for local deployment; document before network-exposing)
3. No rate limiting on write endpoints (acceptable for local deployment)

## Planned Features

### In Progress
- [ ] Stable page identity (ADR-013/014) — branch V1-0009 — status: Phase 0 core DONE
  - Done: `mint_id()` (ULID) + `WikiPage.id`; `write_page` auto-mints (+ `stamp_updated` flag);
    `read_page` loads id; `mymem/wiki/identity.py` (title|slug→id index + exact `resolve_to_id` +
    `backfill_page_ids`); `mymem pages backfill-ids` CLI. Live wiki migrated: 144/144 pages.
  - Fixed: re-ingest (`ingest.py`) and daily re-run (`introspect.py`) preserve the existing id
    instead of minting a new one — id is now stable across re-compilation. Regression test added.
  - Tests: 23 identity/regression tests, identity.py 100% cov; full suite 747 passed / 1 skipped.
  - Next in branch: ADR-011 Phase 1 (propositions) OR graph re-key slug→id (deferred — no rename
    surface yet, doesn't block claims). Deferred (ADR-014 D4): rename redirects; fuzzy wikilink→id.
  (store/extractor/resolver/backfill, `mymem graph backfill|stats` CLI, ingest hook,
  delete/archive cleanup); next: Phase 2 (lint unlinked mentions) + Phase 3 (retrieval RRF)
- [ ] Social source readers + free-tier routing — branch V1-0008 — status: develop (ADR-009, ADR-010)
  - `mymem/pipeline/social_readers.py`: X/Twitter via syndication API + nitter fallback,
    Reddit via `.json`, X Article title/preview handling; fixes single-idea extraction
  - Free-tier routing: NVIDIA primary, per-task models (heavy→NVIDIA, light→Groq),
    cross-provider `FreeTierFallbackChain` swaps on 429; `OPEN_ROUTER_API_KEY` alias fix
  - Tests: 36 social + 6 free-tier-chain (incl. live-validated golden token); docs/TESTING.md added

### Proposed
- [ ] Compounding ingest (knowledge-moat core) — branch V1-0010 — priority: P0 — Phases 1-3 DONE
  - Phase 1 DONE: extraction emits a verbatim `source_span` per idea, mechanically grounded
    against the source (`_ground_span` substring+rapidfuzz≥90, blank-not-drop); `IdeaSchema.source_span`;
    10 tests (test_propositions.py). ADR-015 D1-D3.
  - Phase 2 DONE: bi-temporal claims store `mymem/knowledge/claims.py` → `data/claims.db`
    (claims keyed on stable page ULID per ADR-013, verbatim provenance, confidence,
    valid_from/valid_to/superseded_by; supersede never hard-deletes). 100% cov.
    `_merge_ideas` recovers the best-grounded span the merge LLM drops (`_preserve_spans`).
    ADR-015 D4-D7.
  - Phase 3 DONE (built in parts): `pipeline/reconcile.py` (ADD/MERGE/SUPERSEDE/NOOP decision
    core, parse→ADD safe-default, 100% cov) → `knowledge/retrieval.py` (same-page active-claim
    retrieval, injected embedder, in-Python cosine, 100% cov) → `pipeline/compounding.py`
    (retrieve→decide→apply orchestrator, 100% cov). Ingest's `_persist_claims` now compounds
    per proposition with a naive-replace fallback when the embedder is down; `reconcile` task
    added to the router. ADR-015 D8-D12.
  - Phase 3 (cont.) DONE: `knowledge/render.py` renders a deterministic `## Knowledge Claims`
    section (active + struck-through superseded trail) synced into each touched page's markdown
    after compounding (`_sync_claims_sections`, idempotent, marker-delimited, prose kept). The
    MERGE/SUPERSEDE audit trail now shows in the wiki + Obsidian exports. 100% cov. ADR-015 D13-D14.
  - Decision-agreement eval DONE (ship gate, PRD §Success Metrics #1): `evals/decision_agreement.py`
    — held-out judge re-decides ADD/MERGE/SUPERSEDE/NOOP, label-agreement rate graded
    PASS≥0.80/WARN≥0.60; reuses reconcile prompt/parse (`RECONCILE_SYSTEM` made public); 100% cov.
    ADR-015 D15-D16.
  - Decision-eval now LIVE on ingest: `reconcile_source_claims` returns typed `AppliedDecision`s;
    `_eval_decision_agreement_background` (fire-and-forget) builds cases (`cases_from_applied`, drops
    trivial ADDs), judges via shared `_build_reference_llm`, persists `save_run("decision_agreement")`
    → shows in the eval suite grid. ADR-015 D17.
  - Cleanup DONE: split `pipeline/ingest.py` (1258 lines) into focused modules — `ingest_extract.py`
    (Map/Merge/Verify + spans), `ingest_rag.py`, `ingest_claims.py`, `ingest_background.py` (graph +
    evals); `ingest.py` is now the ~480-line orchestrator re-exporting the moved names. Behavior-
    preserving (851/851 green), ADR-015 D18.
  - Cross-page retrieval (D8/D19) DONE: global sqlite-vec claim index `knowledge/claim_index.py`
    (cosine, in claims.db); `retrieve_candidates` now searches ALL pages (vector in, thin adapter);
    `compounding` embeds each prop once, retrieves globally, and keeps the index in sync (index
    ADD/SUPERSEDE, de-index superseded); `backfill_claim_index` + `mymem claims backfill-index` CLI.
    100% cov on new modules. ADR-015 D19.
  - Next: render body FROM claims (D11 end-state); graph re-key slug→id.
  - Research: docs/research/knowledge-moat-and-free-tier-routing.md · PRD: docs/PRD/compounding-ingest.md
  - Architecture: docs/architecture/compounding-ingest.md · ADR-011 · claims key off stable `page_id`
  - Converts ingest from overwrite-by-slug (`ingest.py:315`) to atomic propositions (with verbatim
    source spans) → retrieve-similar → LLM ADD/MERGE/SUPERSEDE/NOOP → apply, with per-claim provenance
    + confidence in `data/claims.db` and bi-temporal supersede (never hard-delete)
  - Ship gates: merge-decision precision, no idea-recall regression, ingest cost < +20%
  - Fast-follows (deferred): drift-triggered re-summarize + `lint --consolidate`; usage feedback loop;
    KBT source-trust learning. Query-time RRF + small-to-big folds into ADR-008 Phase 3.
- [ ] Quota-aware free-tier routing — ADR-012 — priority: P1 — independent of compounding ingest
  - New `mymem/pipeline/router/_quota.py`: per-provider/account cooldown registry keyed off 429 +
    `Retry-After`, predictive token-bucket from `x-ratelimit-*`, multi-key rotation, latency-EWMA,
    degrade-to-Ollama on cost cap; pure `select_provider()` (no call-site changes); supersedes the
    static parts of ADR-010
- [x] Graph entity mapping Phase 1 core — PRD: docs/PRD/graph-entity-mapping.md
  - Typed entity extraction folded into ingest LLM call (person/project/system/org/concept + span)
  - 3-tier resolution: exact/alias → rapidfuzz+cosine → batched LLM judge (Graphiti pattern)
  - `data/graph.db`: entities/aliases/mentions; shared-entity edges join pages
  - 1-hop graph expansion + RRF fusion into existing hybrid retrieval
  - Alias frontmatter + deterministic unlinked-mention linter (Obsidian pattern)
  - Evals: entity consensus + span-grounding; KGQAGen-style multi-hop A/B; ship gates:
    multi-hop recall up, single-hop no regression, ingest cost < +20%
  - NOT building: community detection/summaries (ADR-007)
- [ ] Human review track for extraction eval (`mymem/evals/review.py`, `mymem eval --review`)
- [ ] Wire extraction consensus into `EvalReport` (`runner.py` surfaces consensus results)
- [ ] Ontology layer — typed relationships (is-a, part-of, contradicts, etc.)
- [ ] Agent decomposition (4 agents + 2 subagents per ADR-005)
- [ ] NotebookLM / Notion sync integrations (per ADR-004)

### Backlog
- [ ] Rate limiting middleware on write endpoints (before network exposure)
- [ ] URL allowlist / SSRF protection (before network exposure)
- [ ] MIME type validation on file upload

## Success Metrics

- Extraction consensus PASS rate on ingested articles (3 runs recorded: 2× WARN, 1× PASS)
- Mean duplicate concept pairs per ingest (target: near 0 after dedup)
- Wiki page coverage: ideas from full document via map-reduce (no longer limited to 6000 chars)
- Test suite: 861 passing / 1 skipped as of 2026-06-16 (compounding ingest Phases 1-3 + wiki claims section + decision-agreement eval + cross-page vector retrieval)
