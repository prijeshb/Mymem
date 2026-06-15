# Research & Vision: Knowledge-as-Moat (+ Free-Tier Routing)

> Status: **Research / vision doc** — no PRD or ADR yet. Decide deliverables after reading.
> Scope (per request): **lead with the knowledge-moat thesis** across all four axes
> (richer extraction · continuous re-compilation · query-time assembly · feedback/provenance);
> **routing is secondary** (survey + concrete redesign proposal at the end).
> Date: 2026-06-15 · Branch context: V1-0008.

---

## 0. The thesis, sharpened

> *"The knowledge we compile and feed the LLM is the moat; making every ingestion
> improve that body indefinitely is the durable advantage."*

**Verdict: the thesis is correct, but it needs one correction and one precondition.**

- **Correction — the moat is the *maintained structure*, not the compiled text.** Raw
  sources are commodity; anyone can re-scrape and re-compile them. What is path-dependent
  and expensive to reconstruct is the *structure layered on top*: deduped/merged atomic
  facts, the wikilink + entity graph, provenance edges, contradiction resolutions, source-trust
  scores, and the accumulating record of which knowledge produced good answers. A competitor
  can copy your sources; they cannot cheaply reproduce 10,000 LLM merge/supersede decisions
  and the usage signal behind them.

- **Precondition — it only compounds if ingestion *mutates* existing knowledge.** Every
  system in the state of the art that compounds (Mem0, Zep/Graphiti, Letta, GraphRAG) shares
  one trait: **new sources update/merge/invalidate existing knowledge, they don't just append.**
  Append-only knowledge bases *plateau and decay* — orphans, duplicates, stale stubs — which is
  exactly what MyMem's own `lint` already detects.

**This is the crux of the whole doc:** MyMem today is closer to append-than-compound (see §1).
The single highest-impact change is converting ingest from "write/overwrite pages" into an
**extract → retrieve-similar → ADD / MERGE / SUPERSEDE / NOOP** loop with per-claim provenance.
That one change is what makes the thesis *literally* true.

---

## 1. Where MyMem stands today (grounded audit)

What already exists is a strong **substrate** for the flywheel — but the loop isn't closed.

| Capability | Status in code | Verdict |
|---|---|---|
| Atomic markdown pages + `[[wikilinks]]` | `wiki/` + `wiki_chunker` parent-child | ✅ substrate present |
| Entity graph (typed entities, mentions, resolution) | `mymem/graph/` (ADR-007/008), ingest hook | ✅ substrate present |
| Vector RAG + `delete_source()` | `mymem/rag/` (sqlite-vec) | ✅ substrate present |
| Map → Merge → Verify extraction | `ingest.py:_extract_ideas_map_reduce` | ◑ good for recall; no provenance spans |
| **"Update" of an existing page** | `ingest.py:315` `is_update = page_path.exists()` | ❌ **slug-collision overwrite, not a merge** |
| Per-claim provenance / source spans | `IdeaSchema.evidence` = paraphrased quotes only | ❌ no verbatim span, no fact→source edge |
| Contradiction / bi-temporal handling | none | ❌ |
| Query fusion | `query.py:106–139` keyword `index.search` + RAG chunks **appended** | ❌ no RRF, no rerank, no small-to-big, no global/local |
| Usage feedback (what answered well) | none (curiosity decay is topic-weight only) | ❌ |

**The two load-bearing gaps:**

1. **Ingest overwrites instead of compounding.** When a slug collides, `ingest.py` recompiles
   the page body *fresh* from `_compile_prompt(title, summary, source_text, domain)` and writes
   it — the existing page is never read or merged (only its `created` date is preserved). New
   evidence on a known concept *replaces* rather than *enriches*. This is the append-not-compound
   failure mode the literature warns about.

2. **Query assembly is keyword-first append.** `index_mgr.search()` ranks by keyword over
   `index.md`, loads those page bodies, then *appends* top-k RAG chunks with no rank fusion,
   no reranking, and no parent-expansion. The pre-built moat is under-exploited at read time.

Everything below is about closing those two gaps and adding the compounding signals (provenance,
trust, usage) on top of the substrate that already exists.

---

## 2. The compounding flywheel

Each axis is a stage in one cycle. Run them in order and every ingestion makes the *next*
answer better:

```
   (1) EXTRACT richer signal              (2) RE-COMPILE into the body
   raw source → atomic propositions       for each proposition: retrieve
   + typed claims + "questions answered",  similar existing knowledge →
   each with a verbatim source span        LLM picks ADD / MERGE / SUPERSEDE / NOOP;
        │                                  invalidate (not delete) contradicted
        │                                  facts; re-summarize on drift trigger
        ▼                                        │
   pages get denser, more atomic,                ▼
   more linked                            graph connectivity ↑, page quality ↑,
        │                                 duplicates collapse, contradictions resolve
        ▼                                        │
   (3) ASSEMBLE at query time                    ▼
   global (index/domain summaries) vs      (4) FEEDBACK / PROVENANCE
   local (page + backlinks); small-to-big; every claim has source + confidence;
   hybrid + RRF + rerank                    trusted sources win merges;
        │                                   "used-in-good-answers" boosts
        ▼                                   retrieval weight
   better answer, fewer tokens ───────────────────►───────────────────┘  (feeds back to 2 & 3)
```

### Axis 1 — Richer extraction (more signal per source)

**Goal:** raise *idea-recall per document* without hallucinating.

**Prior art:**
- **Dense X / proposition chunking** — decompose text into *atomic, self-contained propositions*
  (one claim each). For a fixed word budget you fit ~10 propositions vs ~2 passages → far higher
  density of retrievable signal. ([Dense X writeup](https://clusteredbytes.pages.dev/posts/2024/llamaindex-dense-x-retrieval/))
- **Microsoft GraphRAG covariates** — extract **claims** as structured records: subject, object,
  type, status, time-bounds, **and a verbatim source-text span**. Off by default because it ~doubles
  extraction cost — a useful cost/value signal. ([GraphRAG dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/))
- **LlamaIndex `QuestionsAnsweredExtractor`** — per chunk, generate the questions it answers; a
  powerful query→content bridge. ([metadata extraction](https://docs.llamaindex.ai/en/stable/module_guides/indexing/metadata_extraction/))
- **Zettelkasten / evergreen notes** — atomic (one idea per note) + densely linked; atomicity
  "multiplies the number of connections in the network." ([Matuschak](https://notes.andymatuschak.org/Evergreen_notes_should_be_atomic))
- **Anti-hallucination = span grounding** — require each fact to carry its source span, then
  validate by attribution rather than trusting free generation.

**MyMem already does** Map→Merge→Verify, which is a solid recall mechanism. **Gaps:** ideas aren't
atomic propositions, and `evidence` is paraphrase, not a verbatim span.

**Adopt:**
- Add a **proposition layer**: the `compile`/extract model emits atomic facts, each with a verbatim
  `source_span`. Store as the smallest retrieval unit and the seed for page bullets.
- Extend `IdeaSchema` (`ingest.py`) with `source_span` and (optional pass) typed `claims`
  (subject/relation/object/status/time) → feed the existing `mymem/graph/` entity layer.
- Add a `questions:` frontmatter field per page (cheap; big query-time hit-rate win).
- Enforce **one-concept-per-page** (split omnibus pages) so wikilink density grows per ingest.

### Axis 2 — Continuous re-compilation (the "indefinitely improving" loop) — **load-bearing**

**Goal:** new sources *update/merge/invalidate* existing knowledge instead of overwriting it.

**Prior art:**
- **Mem0 — LLM-decided ADD / UPDATE / DELETE / NOOP.** For each candidate fact, retrieve the
  top semantically-similar *existing* memories and let the LLM (via function calling) pick the op.
  No separate classifier. **This is the single cleanest blueprint for "every ingestion improves the
  body."** ([Mem0 paper](https://arxiv.org/pdf/2504.19413))
- **Zep / Graphiti — bi-temporal graph with edge invalidation.** Track *valid time* and
  *transaction time*; a contradicting fact **invalidates** the old edge (sets `t_invalid`) rather
  than deleting it — preserves history, keeps the "current" view correct, avoids recompute.
  ([Zep paper](https://arxiv.org/pdf/2501.13956))
- **GraphRAG vs LightRAG — incremental update.** GraphRAG must sometimes recompute communities
  (expensive); **LightRAG set-merges** new entities/relations into the existing graph with no
  global rebuild. Design fork: prefer incremental merge, re-summarize only on a **drift trigger**.
  ([LightRAG](https://lightrag.github.io/), [GraphRAG #741](https://github.com/microsoft/graphrag/issues/741))
- **Letta/MemGPT — self-editing memory** (`memory_insert/replace/rethink`); every edit costs
  inference tokens (cost control matters for local-first). ([Letta](https://docs.letta.com/concepts/memory-management/))
- **Consolidation = dedup + merge + reflection** — collapse same-claim-different-wording; an arbiter
  reconciles conflicts; reflection generalizes accumulated facts into higher-level insights once
  evidence accrues. ([consolidation](https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation))

**Adopt (in priority order):**
- **Make ingest an UPDATE pipeline (Mem0).** Before writing a page, retrieve top-k similar existing
  propositions/pages from sqlite-vec and have the LLM choose **ADD / MERGE / SUPERSEDE / NOOP**.
  Replaces the `is_update = page_path.exists()` overwrite at `ingest.py:315`. *This is the #1 change.*
- **Bi-temporal claims (Zep).** Add `valid_from` / `valid_to` / `superseded_by:` to claims so
  contradicting sources invalidate rather than overwrite — keeps an audit trail.
- **Drift-triggered re-summarize (LightRAG + GraphRAG).** Set-merge entities immediately; only
  re-compile a page/topic when a threshold is crossed (≥N new sources touched it, or a contradiction
  logged). The cost valve that makes "indefinitely improving" affordable locally.
- **`mymem lint --consolidate`** as a scheduled pass: dedup near-duplicate pages, merge, re-summarize
  grown topics. Bounded cost (not per-ingest). Builds on existing `lint` orphan/stub detection.

### Axis 3 — Context assembly at query time (packing the moat into the prompt)

**Goal:** put the *right slice* of the compiled body into the window.

**Prior art:**
- **Context engineering — Write/Select/Compress/Isolate.** MyMem's pre-compiled wiki *is* the
  "Write" stage done at ingest; the moat is that **Select** reads pre-built pages instead of
  re-deriving. ([context engineering](https://rlancemartin.github.io/2025/06/23/context_engineering/))
- **GraphRAG global vs local search.** *Local* traverses an entity neighborhood (targeted lookups);
  *global* map-reduces over **community/summary nodes** for corpus-wide synthesis. Root summaries
  used **97% fewer tokens** than processing source text. ([GraphRAG QFS](https://arxiv.org/pdf/2404.16130))
- **Hybrid + RRF + rerank.** Fuse keyword + vector with **Reciprocal Rank Fusion** (sums reciprocal
  ranks; no score normalization needed), then optionally cross-encoder rerank the top-N.
  ([RRF](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/))
- **Small-to-big / auto-merging.** Retrieve small chunks for precision, feed the larger *parent* for
  context. MyMem's `wiki_chunker` parent-child chunks already enable this. ([auto-merging](https://developers.llamaindex.ai/python/examples/retrievers/auto_merging_retriever/))
- **RAPTOR** — recursive cluster+summarize tree; retrieve across abstraction levels. (+20% QuALITY.)

**MyMem today:** keyword-first (`index.search`) + RAG chunks *appended*, no fusion. **Under-exploits
the moat.**

**Adopt:**
- **Formalize fusion as RRF** in `query.py` (mix keyword-rank and vector-rank without normalization);
  add an optional local cross-encoder rerank on the fused top-N.
- **Small-to-big:** retrieve at proposition/section granularity, expand to parent page/section before
  synthesis (parent-child chunks already exist).
- **Two-mode query (global/local):** route "synthesis" questions to a global path that map-reduces
  over `index.md` + per-domain summary pages; route "specific" questions to local page+backlinks.
  Make `index.md` a first-class retrieval target.
- **Lazy hierarchical summaries (RAPTOR-lite):** treat existing `wiki/daily/` + per-domain pages as
  upper tree levels for cheap global answers.

### Axis 4 — Feedback / provenance (what turns a static wiki into a *learning* asset)

**Goal:** compound usefulness over time — the part most PKM tools skip.

**Prior art:**
- **Provenance / attribution** — link each factual statement to specific source span(s); per-sentence
  grounding-confidence flags weak claims. ([attribution](https://apxml.com/courses/getting-started-rag/chapter-4-rag-generation-augmentation/attributing-sources))
- **Knowledge-Based Trust (Google)** — estimate source trustworthiness from endogenous signals: a
  source with few false facts (judged against many sources) is trusted; jointly infers source-trust
  and fact-plausibility. ([KBT](https://www.researchgate.net/publication/272194238_Knowledge-Based_Trust_Estimating_the_Trustworthiness_of_Web_Sources))
- **Confidence scoring** — fact confidence = f(source authority, corroboration count, recency).
- **Learning from corrections + recency decay** — treat user edits as authoritative UPDATE/DELETE;
  weight retrieval by recency/importance/relevance. MyMem's curiosity decay (`exp(-0.1·days)`)
  already implements the math — extend it from topic-weights to *retrieval & trust*.

**Adopt:**
- **Per-claim provenance edges in SQLite:** every proposition → source file + span (GraphRAG model).
  Makes citations real; lets you recompute a page's grounding when a source is removed (extend the
  existing `delete_source()` to claims).
- **Source-trust score (KBT-lite):** rises when a source's facts are corroborated, falls when
  contradicted; used as the tie-breaker for MERGE/SUPERSEDE in Axis 2.
- **`confidence:` per claim**, surfaced in `lint` and grayed in the UI (reuse the broken-link UX).
- **Usage loop (the missing reinforcement):** log which pages/propositions appeared in *saved/edited*
  answers; boost their retrieval weight. This is what makes the wiki learn from how it's used.
- **User edits = ground truth:** `PATCH /api/page` edits pin values and raise confidence.

---

## 3. Prioritized roadmap (highest leverage first)

| # | Change | Axis | MyMem module | Why it's leverage | Rough effort |
|---|---|---|---|---|---|
| 1 | **Atomic propositions + verbatim source spans** | 1 | `ingest.py` `IdeaSchema`, extract prompt | The atom the whole flywheel needs (merge, provenance, dedup all require it) | M |
| 2 | **Mem0 UPDATE pipeline: ADD/MERGE/SUPERSEDE/NOOP** | 2 | `ingest.py:315` rewrite; sqlite-vec lookup | Makes "every ingestion improves the body" *literally true*; without it the thesis fails | L |
| 3 | **Per-claim provenance + confidence + source-trust** | 4 | `mymem/graph/`, new claims table | Cheap to store; the tie-breaker for #2 *and* input to real citations | M |
| 4 | **Bi-temporal invalidation for contradictions** | 2 | claims frontmatter / graph edges | Supersede-don't-delete: correctness without losing history | M |
| 5 | **Drift-triggered incremental re-summarize** | 2 | `ingest.py`, `lint --consolidate` | Cost valve that makes "indefinitely improving" affordable locally | M |
| 6 | **Hybrid + RRF + small-to-big at query** | 3 | `query.py:106–139` | Biggest answer-quality win per unit effort; parent-child chunks already exist | S–M |
| 7 | **Usage feedback loop (boost what answered well)** | 4 | `query.py` save path, curiosity.db | Reinforcement signal almost no PKM tool has; closes the flywheel | M |

**Non-negotiable core: #1 → #2 → #3** (extract atomically → update not append → track provenance).
#4–#7 are the multipliers that make it durable, affordable, and self-improving.

**Suggested ship-gates (evals first, per project rule):** reuse the existing extraction-consensus
eval. Gates: (a) merge precision — SUPERSEDE/MERGE decisions agree with a held-out judge ≥X%;
(b) no idea-recall regression vs current Map/Merge/Verify; (c) ingest cost increase < +20% (GraphRAG's
own claim-extraction budget signal); (d) multi-hop/global answer quality up, single-page no regression.

---

## 4. Free-tier routing (secondary) — survey + redesign proposal

ADR-010 already shipped a **static** cross-provider `FreeTierFallbackChain` (NVIDIA primary →
Groq → NVIDIA-alt → OpenRouter → Ollama floor). The improvement is to make it **quota-aware** so a
provider that's *currently* rate-limited is skipped *before* the 429, and load is spread across
per-account free buckets.

### How others do it (concrete mechanisms)

| System | Quota-aware routing | Fallback / load balance | Cost / latency | Health / circuit breaker |
|---|---|---|---|---|
| **LiteLLM Router** (OSS Python — closest analog) | per-deployment `rpm`/`tpm`, pre-call checks filter over-limit; respects `retry-after` on **429** | `fallbacks` list; `simple-shuffle` weighted; multi-key = multiple deployments same model name | `cost-based`, `latency-based` (moving avg + buffer), `usage-based-v2` (lowest TPM) | `CooldownCache`: cooldown when `fails > allowed_fails` (default 3, 5s); 401/404/408/429 = immediate cooldown |
| **OpenRouter** | auto-fallthrough on 429/5xx | `models[]` array; `provider.order/only`; default LB = inverse-square price weighting | `provider.sort: price/throughput/latency`; `:floor`/`:nitro`; `max_price` | considers outage history; soft perf filters deprioritize |
| **GitHub Copilot** | premium-request budget; degrade to included model on exhaustion | model picker / auto-select (10% discount) | per-model premium multiplier (0.9–27×) | graceful degrade, not hard fail |
| **Portkey** | conditional routing on metadata | weighted targets (normalized to 1.0); nestable fallbacks | conditional rules encode policy | retry up to 5× exp backoff |
| **Cloudflare / Vercel AI Gateway** | rate + budget limits with fallback output | ordered model fallback, each w/ own timeout+retry | analytics; default blends uptime+latency | retries ≤5, 100ms–5s, const/linear/exp |

**Key portable lessons:** (1) the dominant pattern is a **cooldown registry keyed off 429 +
`retry-after`** (LiteLLM/Helicone/OpenRouter all do it); (2) LiteLLM has *documented gaps* —
it drops `retry-after` on 502/503/504 ([#16286](https://github.com/BerriAI/litellm/issues/16286))
and ignores it in usage-based-v2 ([#7669](https://github.com/BerriAI/litellm/issues/7669)) — **parse
`retry-after` for all error classes** to avoid repeating them; (3) multi-key free-tier rotation is
just "multiple deployments under one model name"; (4) Copilot's "degrade to a free model when budget
spent" maps perfectly to **Ollama as MyMem's always-on zero-cost floor.**

### Proposed redesign — one new `mymem/pipeline/router/_quota.py`

In-process, no external gateway, fits the package's frozen-dataclass + injectable-`now` test style:

```python
@dataclass(frozen=True)
class ProviderState:
    cooldown_until: float = 0.0        # time.monotonic(); 0 = healthy
    consecutive_fails: int = 0

@dataclass(frozen=True)
class RateWindow:
    remaining_rpm: int | None = None   # from x-ratelimit-remaining-requests
    remaining_tpm: int | None = None   # from x-ratelimit-remaining-tokens
    reset_at: float | None = None      # from x-ratelimit-reset-*
```

- **Cooldown on 429:** parse `Retry-After` + `x-ratelimit-reset-*`; set
  `cooldown_until = now + max(retry_after, backoff)`. On 5xx: increment `consecutive_fails`,
  cool down only past `allowed_fails` (default 3) to avoid flapping. **Parse `retry-after` for 5xx
  too.** Use `time.monotonic()`.
- **Predictive token-bucket:** after each success, update `RateWindow` from response headers; a
  pre-call check skips a provider whose `remaining_rpm <= 0` or whose `remaining_tpm <` estimated
  tokens — *before* the 429. Ollama (local) always passes.
- **Multi-key/account rotation:** model multiple keys of one provider as separate "deployments"
  sharing a model name, each with its own `ProviderState`; weighted/round-robin over healthy ones.
- **Latency-EWMA preference:** `ewma = 0.3·sample + 0.7·prev`; among healthy providers prefer lowest
  EWMA, with a small buffer so others stay warm (spreads free-tier load).
- **Graceful degrade + cost cap:** session cost already tracked in `mymem.db`; when a budget is
  exceeded, restrict routing to Ollama (Copilot pattern).
- **Pure selection fn:** `select_provider(chain, now, registry) -> provider` — unit-testable with
  synthetic header dicts, no network. `_chain.py` consumes it; `_router.py` updates the registry from
  response headers after each call.

This upgrades ADR-010's static chain into a quota-aware one **without call-site changes** — it's a new
`IFallbackChain`/selection layer, same as the existing strategy seam.

---

## 5. Risks & open questions

| Risk | Note |
|---|---|
| **Cost of the UPDATE pipeline** | Each ingest now does extra retrieve + LLM merge calls. Mitigate: drift-trigger re-summarize, batch merges, run heavy passes on the free tier. Gate on "<+20% cost". |
| **LLM merge errors corrupt the moat** | A bad SUPERSEDE deletes good knowledge. Mitigate: bi-temporal invalidate (never hard-delete), keep source spans, eval merge precision before shipping. |
| **Local model judgment quality** | Mem0/Letta quality depends on the model. On free tiers (Llama-70B-class) merge decisions may be weaker than Claude. Test the decision step specifically. |
| **Provenance storage growth** | Per-claim spans multiply rows. SQLite handles it; prune via `delete_source()` cascade. |
| **Routing header variance** | Each free provider emits different `x-ratelimit-*` headers. Need a small per-provider header-parser map; fall back to reactive cooldown when headers absent. |
| **Scope creep** | Axes 1–4 are a multi-sprint program. Sequence strictly #1→#2→#3 first; everything else is optional multiplier. |

---

## 6. Recommended next steps

1. **Greenlight the core (#1–#3)** as the next feature branch — that trio is what makes the moat
   thesis literally true. If yes, I'll turn it into a **PRD + system design + ADR** (claim/provenance
   schema, the ADD/MERGE/SUPERSEDE/NOOP decision contract, eval ship-gates).
2. **Routing redesign** can be a separate, smaller ADR (`_quota.py`) — independent of the moat work,
   shippable anytime; supersedes the static parts of ADR-010.
3. **Query-time RRF + small-to-big (#6)** is the cheapest standalone win and pairs naturally with the
   existing graph retrieval (ADR-008 Phase 3 RRF is already planned) — consider folding them together.

---

## 7. Sources

**Routing:** LiteLLM [routing](https://docs.litellm.ai/docs/routing) · [#16286](https://github.com/BerriAI/litellm/issues/16286) · [#7669](https://github.com/BerriAI/litellm/issues/7669) · OpenRouter [provider selection](https://openrouter.ai/docs/guides/routing/provider-selection) / [fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks) · Copilot [premium requests](https://docs.github.com/en/billing/concepts/product-billing/github-copilot-premium-requests) / [multipliers](https://docs.github.com/en/copilot/reference/copilot-billing/model-multipliers-for-annual-plans) · Portkey [load balancing](https://docs1.portkey.ai/docs/product/ai-gateway/load-balancing) / [fallbacks](https://portkey.ai/docs/product/ai-gateway/fallbacks) · [Cloudflare AI Gateway](https://developers.cloudflare.com/ai-gateway/) · [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/models-and-providers/provider-options) · [Helicone](https://docs.helicone.ai/gateway/provider-routing)

**Knowledge moat:** GraphRAG [dataflow](https://microsoft.github.io/graphrag/index/default_dataflow/) / [QFS](https://arxiv.org/pdf/2404.16130) / [incremental #741](https://github.com/microsoft/graphrag/issues/741) · [Dense X](https://clusteredbytes.pages.dev/posts/2024/llamaindex-dense-x-retrieval/) · [LlamaIndex extractors](https://docs.llamaindex.ai/en/stable/module_guides/indexing/metadata_extraction/) / [auto-merging](https://developers.llamaindex.ai/python/examples/retrievers/auto_merging_retriever/) · [Evergreen notes](https://notes.andymatuschak.org/Evergreen_notes_should_be_atomic) · [Zep/Graphiti](https://arxiv.org/pdf/2501.13956) · [Mem0](https://arxiv.org/pdf/2504.19413) · [Letta](https://docs.letta.com/concepts/memory-management/) · [LightRAG](https://lightrag.github.io/) · [RAPTOR](https://arxiv.org/pdf/2401.18059) · [RRF](https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/) · [context engineering](https://rlancemartin.github.io/2025/06/23/context_engineering/) · [KBT](https://www.researchgate.net/publication/272194238_Knowledge-Based_Trust_Estimating_the_Trustworthiness_of_Web_Sources) · [consolidation](https://hindsight.vectorize.io/blog/2026/05/21/agent-memory-consolidation)
