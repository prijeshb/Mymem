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

### D17. Live capture via typed AppliedDecision + generic save_run; drop trivial ADDs

**Chosen:** `reconcile_source_claims` now returns typed `AppliedDecision`s (proposition +
candidates + result + claim). `_eval_decision_agreement_background` (fire-and-forget, next
to extraction-consensus) builds cases via `cases_from_applied`, judges them with the shared
`_build_reference_llm`, and persists through the **generic `save_run("decision_agreement",
…)`** (no bespoke table). Trivial no-candidate ADDs are excluded — they weren't LLM
judgements, so counting them would inflate agreement.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **AppliedDecision return + generic save_run, drop trivial ADDs** (chosen) | Candidates captured at the one place that has them; the suite grid (`latest_summary`/`history_by_type`) picks up `decision_agreement` for free; metric reflects real judgements only | Return-type change rippled to compounding tests | ✅ |
| Bespoke `decision_agreement` table (like extraction_consensus) | Columnar queries | Premature — generic `eval_runs` already serves the grid; add a table only if per-comparison SQL is needed | ❌ Defer |
| Count every decision incl. no-candidate ADDs | Simplest capture | Inflates agreement with free ADDs the LLM never judged | ❌ |

Also extracted `_build_reference_llm` (provider/key plumbing) so extraction-consensus and
decision-agreement share one reference-LLM factory (DRY).

**Revisit when:** per-comparison analytics or trend charts need columnar storage → add a
`decision_agreement` table mirroring `extraction_consensus`.

---

## Cleanup — Split the 1258-line ingest.py

### D18. Split into focused modules; ingest.py stays the orchestrator with re-exports

**Chosen:** extract four siblings — `ingest_extract.py` (Map/Merge/Verify + span grounding),
`ingest_rag.py` (RAG index helpers), `ingest_claims.py` (claims persist + wiki sync),
`ingest_background.py` (graph + evals) — leaving `ingest.py` as `ingest_source` +
`IngestResult` + analytics (~480 lines). ingest.py re-exports the moved names (`__all__`)
so existing imports and the file's CLAUDE.md "< 300 lines" intent are both honored.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **4 siblings + re-export surface** (chosen) | One reason to change per module; ~1258→≤519-line files; behavior-preserving (851/851 green); imports/patches keep working via re-export | ingest.py still ~480 (the orchestrator is irreducibly long) | ✅ |
| Leave as one file | No churn | Violates the project's own size rule; hard to navigate | ❌ |
| Also split `ingest_source` body | Smaller orchestrator | The compile loop is one cohesive flow; splitting it hurts readability for little gain | ❌ Defer |

**Patch-target rule (recorded so it isn't re-discovered):** a monkeypatched function keeps
working at `mymem.pipeline.ingest.X` **only if its caller also lives in ingest.py** and calls
it unqualified (e.g. `_rag_index_wiki`). When the caller moves too, patch where it's now
looked up — tests updated to `ingest_claims._build_claim_embedder` and
`ingest_background._build_reference_llm`. mypy error count unchanged (9, all pre-existing,
relocated with their code).

**Revisit when:** `ingest_source` itself grows past ~300 lines → extract the per-idea
compile loop into a `_compile_page` helper.

---

## D8 realized — Cross-page claim retrieval (global vector index)

### D19. Global sqlite-vec claim index; embedder in compounding, not retrieval

**Chosen:** add a `claim_vec` sqlite-vec table (cosine metric) inside claims.db
(`knowledge/claim_index.py`). Retrieval (`retrieve_candidates`) is now a thin adapter that
takes a **precomputed query vector** and searches the index *globally* (all pages), returning
reconcile `Candidate`s. The embedder moved up into `compounding.reconcile_source_claims`,
which embeds each proposition once, retrieves, decides, applies, and keeps the index in sync
(index new ADD/SUPERSEDE claims; de-index superseded ones). A `backfill_claim_index` +
`mymem claims backfill-index` CLI vectorizes pre-D19 claims.

This realizes the trigger deferred in **D8** (same-page retrieval) and **D4** ("add a vec
table only if claim-level vectors prove necessary").

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Global sqlite-vec claim index, embed in compounding** (chosen) | Catches MERGE/SUPERSEDE across pages, not just within one; O(log n) KNN vs re-embedding the corpus per prop; retrieval becomes pure/trivially testable (vector in); reuses the sqlite-vec dep | Must keep the index in sync on add/supersede; needs a backfill for old claims | ✅ |
| Keep same-page in-Python cosine (D8 original) | No index to maintain | Cross-page dups/contradictions are never caught — always ADD; re-embeds same-page claims every prop | ❌ Superseded |
| Re-embed all active claims per proposition | No persistent index | O(all claims) embeddings per prop — the cost D4 warned about | ❌ |

**Sub-decisions:**
- **Cosine metric** (`distance_metric=cosine`) so `similarity = 1 - distance` maps directly
  to the existing `min_similarity` threshold (verified: identical→0, orthogonal→1).
- **Active-filter in Python** after an over-fetched KNN (mirrors `rag/store.py`), since vec0
  can't push the `valid_to IS NULL` join filter into the MATCH query.
- **Index lives in claims.db** (one file, joined to `claims`), accessed by a vec-loading
  connection; `claims.py`'s plain connections never touch the virtual table.
- **De-index on SUPERSEDE** keeps the index lean; correctness doesn't depend on it (the
  active-filter already excludes superseded claims) — `delete_source` vec cleanup is deferred.

**Revisit when:** claim count makes per-prop embedding the bottleneck → batch-embed a source's
propositions up-front (loses intra-batch indexing) or precompute on write; or when
`delete_source` leaves enough dead vectors to hurt recall → add vec cleanup there.

---

## D11 realized — Render page body FROM claims (opt-in)

### D20. Opt-in `body_from_claims`: render the body from claims, behind a config flag

**Chosen:** the D11 end-state ("page reflects its active claims") ships as an **opt-in**
`pipeline.body_from_claims` flag (default off). When on, `_sync_claims_sections` renders each
touched page's body via a new pure `render_page_body(title, claims, see_also=…)` instead of
appending the D13 `## Knowledge Claims` section. The renderer is deterministic (heading +
active claims as confidence bullets + struck-through SUPERSEDE trail + preserved `## See Also`
wikilinks) and returns `""` when there are **no active claims** — so the caller keeps existing
prose and an empty/down-embedder ledger can never wipe a page. The wikilinks of the prior body
are re-emitted into See Also so the knowledge graph survives the switch from prose.

This realizes the trigger deferred in **D11** and **D13** ("a 'page reflects its claims' slice
renders the body *from* active claims, replacing LLM re-compilation").

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Opt-in flag, deterministic renderer, prose-safe** (chosen) | Default behavior byte-identical to D13 (flag off); MERGE/SUPERSEDE now drive the visible body, not just a section; pure/100%-tested renderer; graph preserved via See Also passthrough; no-active-claims guard means it can't gut a page | Two render modes coexist until the flag becomes default; body drops LLM prose nuance the claims don't capture | ✅ |
| Flip the behavior unconditionally | One mode, simpler | Large, irreversible change to page-write semantics across every existing page; risky with no escape hatch (D11 flagged "large, error-prone") | ❌ |
| Render body from claims at compile time (no post-pass) | One write per page | Claims don't exist yet at compile time (compounding runs after the loop) — needs a major reorder; the D14 ordering constraint still holds | ❌ |

**Sub-decisions:**
- **Reuses the post-compounding sync seam** (`_sync_claims_sections`, D14) — it already re-reads
  and re-writes each touched page after claims exist, with `stamp_updated=False`. No new pass.
- **Flag threaded by injection**, not read from global config in the pipeline: `ingest_source`
  takes `body_from_claims: bool = False`; CLI and all three API call sites pass
  `settings.pipeline.body_from_claims`. Matches the codebase's router/embedder injection style.
- **Scope fence:** the CLI ingest path still doesn't pass `db_path`, so claims (and therefore
  this flag) are a no-op there today — wiring CLI claims persistence is a separate slice, not
  folded in here.

**Revisit when:** the flag has run live long enough to trust → make `body_from_claims` the
default and fold the D13 section renderer into `render_page_body` (one mode). If dropping LLM
prose loses value, render claims *and* keep a compiled summary block instead of replacing it.

### D21. Flip `body_from_claims` to default-on (D20's revisit trigger fired)

**Chosen:** `PipelineConfig.body_from_claims` now defaults to **True** (V1-0011). The D11
end-state is the standard behavior: every ingested/re-compiled page renders its body FROM its
active claims. The D13 `## Knowledge Claims`-section mode remains reachable by setting
`pipeline.body_from_claims: false` in `config.yaml`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Flip config default to True, keep D13 as opt-out** (chosen) | Realizes the D11 end-state the project has been building toward; MERGE/SUPERSEDE drive every page body; no code-path churn (only the config default moves); escape hatch preserved | Pages with claims lose LLM prose nuance the claims don't capture | ✅ |
| Keep default off indefinitely | Zero behavior change | The whole compounding-ingest investment never becomes the default user experience | ❌ |
| Flip the function-param defaults too (`ingest_source`, `_sync_claims_sections`) | One value everywhere | Loses the explicit-injection seam D20 chose; churns direct callers/tests for no behavioral gain (call sites already pass the config value) | ❌ |

**Sub-decisions:**
- **Only the config default moved.** Function-param defaults stay `False` (explicit-injection
  style, D20) — live call sites already forward `settings.pipeline.body_from_claims`, so the
  config flip is sufficient and minimal.
- **Safety unchanged:** the no-active-claims guard in `render_page_body`/`_sync_claims_sections`
  still keeps existing prose, so a down embedder or empty ledger can never wipe a page.

**Revisit when:** users report lost prose nuance on claim-rendered pages → render claims *and*
keep a compiled summary block (the D20 fallback), or fold the D13 section renderer into
`render_page_body` as a single combined mode.
