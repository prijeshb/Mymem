# ADR 015: Compounding Ingest — Implementation Decisions

## Status: Accepted (V1-0010, in progress)

Implements ADR-011. Records decisions made while building the compounding-ingest
pipeline, phase by phase. Each section: what we chose, alternatives, pros/cons,
revisit-when.

---

## Phase 1 — Atomic propositions + verbatim source spans

### D1. Span grounding = normalized-substring then rapidfuzz fallback

**Chosen:** `_ground_span()` accepts a span if its whitespace/case-normalized form is a
substring of the normalized source; otherwise if `rapidfuzz.fuzz.partial_ratio ≥ 90`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Normalized substring + fuzzy ≥90** (chosen) | Accepts exact + reformatted quotes; fuzzy catches OCR/transcript drift; reuses the existing `rapidfuzz` dep (no new dependency) | One threshold to tune | ✅ |
| Exact substring only | Simplest, zero false-accepts | Rejects legitimate quotes with reflowed whitespace/casing → loses real spans | ❌ |
| Embedding-similarity grounding | Semantic | Pulls the embedder into extraction; overkill for "is this text present?" | ❌ |

**Revisit when:** false accepts/rejects show up in the extraction eval → tune `min_ratio`
(currently 90) or add a length floor.

### D2. Ungrounded spans are blanked, not dropped (idea is kept)

**Chosen:** when a span can't be grounded, `_ground_idea_spans` sets it to `""` but keeps
the idea.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Blank the span, keep the idea** (chosen) | Phase 1 is additive — idea recall can't regress vs the current pipeline; a missing span just means "no provenance yet" | An idea may carry no span | ✅ |
| Drop the whole idea when ungrounded | Forces provenance | Silently lowers recall; a hallucinated *span* doesn't make the *idea* wrong | ❌ (revisit if hallucinated ideas become a problem) |

**Revisit when:** the extraction-consensus eval shows ungrounded ideas correlate with bad
ideas → escalate from blank-span to drop-idea behind a flag.

### D3. Span lives on the idea through map/merge/verify; persistence is Phase 2

**Chosen:** Phase 1 grounds spans at the map stage (`_extract_chunk_ideas`). The span rides
on the idea dict; writing it to `data/claims.db` is Phase 2.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Ground at map, persist in Phase 2** (chosen) | Smallest shippable slice; foundation for the claims store; grounding happens closest to the source chunk | Merge LLM may not preserve spans yet (fixed in Phase 2) | ✅ |
| Persist spans to a store in Phase 1 | Provenance immediately | Needs the whole claims schema now — that's Phase 2's scope | ❌ |

**Revisit when:** Phase 2 (claims store) lands — make `_merge_ideas` preserve the
best-grounded span per merged concept.

---

## Phase 2 — Claims store (`data/claims.db`) + additive persistence

### D4. Claims live in their own `data/claims.db`, repository pattern

**Chosen:** a new `mymem/knowledge/claims.py` exposing module-level functions over a
dedicated `data/claims.db`, mirroring `graph/store.py` / `rag/store.py`. Plain SQLite
(no sqlite-vec) — claims carry no embeddings of their own.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Own claims.db, repo functions** (chosen) | Same pattern as graph/rag stores; callers depend on functions not schema; swappable/mockable; isolated blast radius | One more db file | ✅ |
| Add a `claims` table to `mymem.db` | One fewer file | Couples provenance to trace/cost db; mixes lifecycles (delete-by-source vs traces) | ❌ |
| Reuse sqlite-vec `rag.db` | Embeddings co-located | Claims ≠ chunks; bi-temporal columns don't belong on the vec store; Phase-3 retrieval reuses rag embeddings *by page_id* anyway | ❌ |

**Revisit when:** Phase 3 needs per-claim similarity — claims link to existing chunk
embeddings by `page_id`; only add a vec table here if claim-level vectors prove necessary.

### D5. Keyed on the stable page ULID, never the slug

**Chosen:** `claims.page_id` stores the page's ULID (ADR-013). Ingest mints/﻿resolves the
id *before* `write_page` (which would otherwise mint it internally and out of reach) so
the claim and page share one identity.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **page_id (ULID)** (chosen) | A rename or surface-form merge never orphans provenance; matches ADR-013 invariant | Must surface the id at write time | ✅ |
| `page_slug` | Directly readable | Slug is mutable → renames orphan every claim | ❌ |

**Revisit when:** never (this is the ADR-011/013 contract); if a page id is ever absent,
`write_page`'s auto-mint remains the fallback.

### D6. Bi-temporal columns now; SUPERSEDE/NOOP logic is Phase 3

**Chosen:** ship the full bi-temporal schema (`valid_from`/`valid_to`/`superseded_by`,
plus `created` transaction-time) and the primitives `supersede_claim` / `corroborate`
in Phase 2, but drive only naive-ADD persistence from ingest. The decision pipeline
(ADD/MERGE/SUPERSEDE/NOOP) that *calls* these primitives is Phase 3.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Full schema now, naive-ADD writes** (chosen) | No migration when Phase 3 lands; primitives unit-tested in isolation first; store is complete and 100%-covered | Bi-temporal columns sit unused until Phase 3 | ✅ |
| Add valid_to/superseded_by in Phase 3 | No dead columns | Schema migration on a populated db; supersede logic untested until then | ❌ |

**Revisit when:** Phase 3 swaps the naive-ADD call site for retrieve→decide→apply.

### D7. Per-source replace (`replace_source_claims`) for idempotent re-ingest

**Chosen:** ingest persists a source's claims via a transactional delete-then-insert
keyed on `source_id`. Re-ingesting a source rebuilds its claims instead of accreting
duplicates — the same idempotency the RAG store gives per source. Persistence is
best-effort (`_persist_claims` never raises) and gated on `db_path`, so it can't fail an
ingest or disturb the existing test suite.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Per-source replace** (chosen) | Idempotent without the decision pipeline; matches `rag/store.delete_source` re-index behavior; clean Phase-3 seam | Discards prior confidence/corroboration on re-ingest (acceptable pre-Phase-3) | ✅ |
| Append every ingest | Simplest | Unbounded duplicate accretion — the exact failure compounding ingest exists to kill | ❌ |
| Decision pipeline now | "Correct" end state | That *is* Phase 3; needs retrieval + reconcile.py | ❌ Defer |

**Revisit when:** Phase 3 lands — replace the blanket per-source rebuild with per-claim
ADD/MERGE/SUPERSEDE/NOOP so corroboration and supersede history survive re-ingest.

---

## Phase 3 — Reconcile pipeline (ADD/MERGE/SUPERSEDE/NOOP), built in parts

Shipped as 3a `pipeline/reconcile.py` (decision core) → 3b `knowledge/retrieval.py`
(candidate retrieval) → 3c `pipeline/compounding.py` + ingest wiring.

### D8. Candidate retrieval scoped to the proposition's page, in-Python cosine

**Chosen:** `retrieve_candidates` pulls the **active claims of the proposition's own page**
(`claims_for_page(page_id, active_only=True)`), embeds them with an injected `Embedder`,
and ranks by cosine in Python (no sqlite-vec claim index).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Same-page active claims + in-Python cosine** (chosen) | Re-ingest resolves to the same `page_id` (ADR-013), so the page's claims are exactly the MERGE/SUPERSEDE/NOOP targets; candidate set is tiny → cosine is cheap; no new schema | Misses cross-page semantic duplicates | ✅ |
| sqlite-vec claim index (global top-k) | Catches cross-page dupes | New vec table + embedding writes per claim; premature at current scale | ❌ Defer (D4) |
| Reuse rag chunk embeddings by page | No claim vectors | Chunks key on slug not ULID → needs slug→id join; indirection | ❌ |

**Revisit when:** cross-page contradiction/dedup matters, or one page's claim count grows
large enough that re-embedding per ingest is costly → add a sqlite-vec claim index.

### D9. Orchestrator in its own module to break the import cycle

**Chosen:** the retrieve→decide→apply loop lives in `pipeline/compounding.py`, importing
both `reconcile` and `retrieval`. `retrieval` imports `reconcile.Candidate`; putting the
loop in either module would cycle.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Separate `compounding.py`** (chosen) | No cycle; SRP — decision / retrieval / orchestration each isolated and 100%-tested; ingest stays thin | One more small module | ✅ |
| Loop inside `reconcile.py` | Fewer files | `reconcile` ↔ `retrieval` import cycle | ❌ |
| Move shared types to a `_types` module | Also breaks cycle | More churn than a 50-line orchestrator | ❌ |

**Revisit when:** never expected; if types proliferate, extract a `knowledge/_types.py`.

### D10. Safe degradation: parse→ADD, and naive-replace fallback

**Chosen:** two safety nets. (1) `parse_decision` degrades any unparseable / unknown /
target-less LLM reply to **ADD** — the only always-safe action. (2) `_persist_claims`
runs the compounding pipeline but, if the embedder/router is unavailable, falls back to
the Phase-2 idempotent `replace_source_claims`; the whole thing is best-effort and never
raises into ingest.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **parse→ADD + naive fallback, best-effort** (chosen) | Ingest never breaks on a bad decision or a down embedder; provenance still recorded; a wrong MERGE/SUPERSEDE (mutating/retiring real claims) is worse than a redundant ADD | A novel-but-actually-duplicate proposition can slip in as ADD when the LLM misfires | ✅ |
| Drop the proposition on ambiguity | No spurious ADDs | Silent recall loss — the exact regression Phase 1 D2 avoided | ❌ |
| Let failures propagate | Surfaces problems loudly | A flaky model would fail ingests outright | ❌ |

**Revisit when:** the decision eval shows ADD-on-misfire inflating duplicates → add a
post-hoc dedup/consolidate lint pass rather than making the hot path fail.

### D11. Page bodies still regenerated; MERGE-enriches-body deferred

**Chosen:** Phase 3 routes the **claims ledger** through the decision pipeline, but wiki
page bodies are still compiled+written by the existing ingest loop. claims.db is the
structured bi-temporal source of truth; the page is a regenerated human view.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Decisions drive claims; page regenerated** (chosen) | Smallest correct slice; the moat (provenance, corroboration, supersede history) is real now; no risky change to page-write semantics | Page body doesn't yet reflect MERGE (append-vs-overwrite) | ✅ |
| Make MERGE append to / patch page body now | Matches the end-state diagram | Turns overwrite-by-slug into structured patching — large, error-prone; belongs in its own slice | ❌ Defer |

**Revisit when:** a "page reflects its active claims" slice — render page body *from* the
active claims, replacing LLM re-compilation, so MERGE/SUPERSEDE show up in the wiki too.

### D12. `reconcile` task routed to the strongest model; LLM skipped when no candidates

**Chosen:** added a `reconcile` task to the router registry (strongest free model, like
`merge`). `reconcile()` short-circuits to ADD **without an LLM call** when retrieval
returns no candidates (every brand-new page) — the common case stays free.

**Revisit when:** decision quality needs a different model than `merge`, or a cheaper
model proves sufficient for the decision → retune `_CLOUD_DEFAULTS["reconcile"]`.

---

## Phase 3 (cont.) — Surfacing claims in the wiki

### D13. Deterministic "Knowledge Claims" section, not body-from-claims replacement

**Chosen:** a pure-Python `knowledge/render.py` renders a marked `## Knowledge Claims`
section (active claims + a struck-through Superseded subsection) that ingest syncs into
each touched page's markdown after compounding (`_sync_claims_sections`). The LLM-compiled
prose body is kept; the section is delimited by `<!-- claims:start/end -->` so it is
replaced idempotently on re-ingest.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Deterministic markdown section, prose kept** (chosen) | Surfaces the MERGE/SUPERSEDE audit trail in the durable markdown (so it flows to Obsidian exports + the SPA for free); pure/100%-tested; additive — no risk to the compile path; idempotent via markers | Page has both prose and a claims list (mild duplication) | ✅ |
| Replace body entirely from active claims | Single source of truth in the view | Drops LLM prose quality; large change to page-write semantics; risky | ❌ Defer (D11 end-state) |
| API endpoint + React panel only | Cleanest markdown | Durable markdown + exports miss the trail; needs frontend work; the data isn't where the reader/Obsidian looks | ❌ |

**Revisit when:** a "page reflects its claims" slice renders the body *from* active claims
(replacing LLM re-compilation) — then the prose and the section converge and this section
wrapper folds into that renderer.

### D14. Sync writes with stamp_updated=False; best-effort, gated on claims.db

**Chosen:** `_sync_claims_sections` re-reads each touched page, syncs the section, and
writes back with `stamp_updated=False` (the compile loop already stamped `updated=today`).
It no-ops when `claims.db` is absent and never raises into ingest.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **stamp_updated=False, best-effort** (chosen) | Doesn't double-bump or corrupt the real edit date; a sync failure can't break an ingest; cheap (one extra read+write per touched page) | A second write per page per ingest | ✅ |
| Inject the section during the main compile-write | One write | The claims don't exist yet at compile time (compounding runs after the loop) — would need a major reorder | ❌ |

**Revisit when:** touched-page counts get large enough that the extra write matters → batch
the section sync, or fold it into the compile-write once claims precede page writes.

---

## Phase 3 (cont.) — Decision-agreement eval (ship gate)

### D15. Judge reuses reconcile's prompt/parse; only the model differs

**Chosen:** `evals/decision_agreement.py` re-judges each pipeline decision with a held-out
LLM, reusing `reconcile.build_decision_prompt`, `RECONCILE_SYSTEM`, and `parse_decision`
(promoted from private). Agreement = decision *label* match; target agreement is tracked
separately for agreed non-ADD decisions. LLM injected, mirrors `extraction_consensus.py`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Reuse reconcile prompt/parse, swap model** (chosen) | The judge runs the *exact* task the pipeline ran, so disagreement reflects model judgement not prompt drift; DRY; parse degrades to ADD like production | Couples the eval to reconcile's prompt (intended) | ✅ |
| Bespoke judge prompt | Judge phrased independently | Prompt drift confounds the metric; duplicate parsing logic | ❌ |
| Compare against a hand-labeled gold set | No judge cost | No gold set exists; doesn't scale with a growing wiki (cf. retrieval-eval feedback) | ❌ Defer |

**Revisit when:** a curated gold set of decisions exists → add it as a second eval mode
alongside the judge.

### D16. Label-agreement thresholds PASS≥0.80 / WARN≥0.60; empty→WARN; live capture deferred

**Chosen:** grade on label-agreement rate — PASS ≥ 0.80, WARN ≥ 0.60, else FAIL; zero
cases grades WARN (nothing to certify, never a false PASS). The eval scores supplied
`DecisionCase`s; capturing real cases from live ingest (recording the candidates each
decision saw) is a follow-up so this slice stays a pure, 100%-tested metric.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **0.80/0.60, empty→WARN, capture later** (chosen) | Conventional agreement bands; empty can't masquerade as a pass; ships the metric now without touching the hot path's return types | Not yet wired to auto-run on real ingests | ✅ |
| Wire live capture now | End-to-end immediately | Changes `reconcile_source_claims` return + background-eval plumbing — a bigger, riskier slice | ❌ Defer |
| Single 0.5 cutoff | Simpler | No "needs-attention" middle band; binary gate hides borderline drift | ❌ |

**Revisit when:** wiring live capture — extend `reconcile_source_claims` to surface the
candidates per decision, build `DecisionCase`s in the background eval (next to
extraction-consensus), and persist results to `data/evals.db` for the suite grid. Tune
PASS/WARN once real agreement numbers exist (PRD leaves target% open).
