# ADR 008: Graph Entity Layer — Implementation Decision Record

## Status: Accepted (documents decisions shipped in V1-0007, Phases 1 + 1.5)

This is the implementation-level companion to [ADR-007](007-graph-entity-mapping.md)
(which decided *lightweight entity layer over full GraphRAG*). Each section records one
decision: what we chose, the alternatives, pros/cons, and when to revisit.

---

## D1. Storage: separate `data/graph.db` (SQLite)

**Chosen:** a dedicated SQLite file next to `mymem.db` / `rag.db` / `evals.db`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Separate graph.db** (chosen) | Follows house one-concern-per-db convention; safe to delete/rebuild independently (backfill is repair); no migration risk to traces/cost data | One more file; cross-db joins impossible (not needed — joins happen in Python) | ✅ |
| Tables inside mymem.db | One file; could JOIN with traces | Couples graph rebuilds to the traces db; backfill "wipe and reseed" risks the wrong tables | ❌ |
| Embedded graph DB (kuzu) | Real graph queries (Cypher) | **Project archived — company shut down** (dependency audit); overkill at 1–10k nodes | ❌ |
| Neo4j / FalkorDB | Mature graph features; what Graphiti requires | Server dependency contradicts SQLite-only constraint; deployment burden for a personal tool | ❌ |

**Revisit when:** node count approaches 100k or multi-hop Cypher-style queries become
common enough that recursive CTEs hurt.

---

## D2. Repository style: module-level functions + frozen dataclasses

**Chosen:** `upsert_entity(db_path, ...)`, `find_entity(...)` etc. — not a `GraphStore` class.
*(Deviation from the architecture doc, which sketched a class.)*

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Module functions** (chosen) | Matches `rag/store.py` and `evals/store.py` exactly — one pattern to learn; trivially testable with `tmp_path`; no connection-lifetime state to manage | Re-opens a connection per call (negligible for SQLite at this scale); `db_path` repeated in every signature | ✅ |
| `GraphStore` class holding a connection | Connection reuse; methods group naturally | Breaks house convention; held connections complicate Windows file locking in tests; invites hidden state | ❌ |
| SQLAlchemy / ORM | Migrations, typed queries | Heavy dependency for 3 tables; codebase is raw-SQL everywhere else | ❌ |

**Consistency beat the marginally "nicer" class design** — the repo's strongest convention
is that stores look alike.

---

## D3. Entity resolution: 3-tier sequential, deterministic-first, LLM-last

**Chosen:** exact/alias → fuzzy (+optional embedding on borderlines) → ONE batched LLM judge.
Ported from Graphiti (Zep), which arrived at this shape after "every resolution is an LLM
call" proved expensive-fast in production.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **3-tier escalation** (chosen) | ~90% of resolutions cost zero LLM tokens (measured: real-wiki seed used 0 LLM calls); each tier independently testable; degrades gracefully when embedder/router absent | Three code paths; thresholds need tuning | ✅ |
| LLM judges every entity | Highest accuracy ceiling; simplest code | Cost scales with catalog × ingest volume; latency; the exact failure mode Graphiti retired | ❌ |
| Embedding-similarity only | One mechanism; no thresholds × 2 | Requires embedder always up (it's the last local dependency); misses exact-alias free wins; cosine alone false-positives on short names | ❌ |
| MinHash/LSH over 3-gram shingles (Graphiti's later optimization) | O(1)-ish lookup at huge catalog sizes | Premature at 449 entities; rapidfuzz over the full catalog is already <1ms | Defer |

**Tier-3 guard worth recording:** the judge may *only confirm the candidate we offered* —
an answer naming any other entity is discarded. This kills the "judge hallucinates a
match" failure mode at the cost of never discovering non-candidate matches (acceptable:
candidates come from tier-2 scoring, which is the component we trust to rank).

---

## D4. Fuzzy metric: `token_sort_ratio` + punctuation-stripping + full-subset upgrade

**Chosen:** `fuzz.token_sort_ratio(processor=utils.default_process)`, upgraded to
auto-accept when a ≥2-token name is a **full token-subset** of a candidate
(`token_set_ratio == 100`).

Decided by measurement against the real wiki, not theory:

| Pair | token_sort | token_set | Decision driver |
|---|---|---|---|
| "Retrieval Augmented Generation" vs hyphenated form | 66.7 raw → **100 with processor** | — | processor is mandatory (punctuation killed the obvious match) |
| "Transactional Outbox" vs "…Outbox Pattern" | 83.3 (borderline) | **100** | subset upgrade promotes the most common wiki-link pattern to free auto-accept |
| "AI" vs "AI Impact on Business Moats" | low | **100 (!)** | forced the ≥2-token guard — single-token subsets false-accept |
| "AI Agents" vs "Shift from LLMs to Agents" | 47.1 | 80.0 | partial overlap stays below accept → judge decides, as designed |

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **token_sort + subset upgrade** (chosen) | Handles word order, punctuation, and short-forms; measured zero false accepts on real data | Two metrics to reason about | ✅ |
| `WRatio` (rapidfuzz's combined heuristic) | One call | Opaque internal weighting; measured 45.0 for "New Thing" vs "Beta" — noisier floor | ❌ |
| Plain `ratio` / Levenshtein | Simplest | Word-order sensitive; "Sara Chen"/"Chen Sara" fails | ❌ |
| Embedding cosine as primary | Semantic matches ("car"/"automobile") | Needs embedder up; slower; semantic ≠ same entity ("Project Phoenix 2023" vs "Phoenix DB" are *similar*, not identical) | ❌ (kept as borderline scorer only) |

**Thresholds** (`FUZZY_ACCEPT=92`, `FUZZY_BORDERLINE=70`) were fitted to measured pairs:
92 sits just above "ModelRoute"/"Model Router" (90.9 — genuinely ambiguous, should go to
judge); 70 just below "S. Chen"/"Sarah Chen" (75 — borderline, not new).
**Revisit:** these are constants, not config — promote to config.yaml if eval data shows
per-domain tuning is needed.

---

## D5. Hallucination control: mechanical span grounding

**Chosen:** every extracted entity must carry a verbatim span; entity passes only if name
OR span fuzzy-matches the source (`partial_ratio ≥ 80`). Pure Python, zero LLM cost.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Mechanical span check** (chosen) | Free; deterministic; testable; catches the literature's "hallucinated relations" failure mode at the door | Fuzzy threshold can pass near-miss hallucinations; paraphrased-but-real entities need the span to save them | ✅ |
| LLM verification pass | Catches subtle fabrications | Doubles extraction cost for a tail risk; LLM verifying LLM compounds bias | ❌ |
| No grounding (trust extraction) | Simplest | KG-construction eval literature scores "relevance" exactly because ungrounded entities are common | ❌ |

---

## D6. Extraction output format: JSON array + Pydantic validation

**Chosen:** JSON `[{name, type, description, span}]`, schema-validated per item, invalid
items skipped (never fatal).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **JSON + Pydantic** (chosen) | House style (ideas extraction is JSON); per-item skip tolerance; Pydantic gives typed errors | LLMs occasionally fence/wrap JSON (handled: think-block + fence stripping ported from `_parse_ideas`) | ✅ |
| LightRAG delimiter tuples (`entity<\|#\|>name<\|#\|>…`) | Robust against JSON syntax errors; streams well | Foreign to this codebase; custom parser to maintain; gleaning loop assumes it | ❌ |
| Structured-output API enforcement | Guaranteed parse | Not uniformly supported across the 6 providers the router fronts | Defer (worth it if Anthropic/OpenAI become primary) |

---

## D7. Ingest integration: fire-and-forget background task

**Chosen:** `asyncio.ensure_future(_graph_extract_background(...))` after ingest completes —
identical shape to the existing extraction-consensus eval hook.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Fire-and-forget** (chosen) | Ingest latency unchanged; graph failure can never break ingest (tested); pattern already proven by the eval hook; CLI already drains pending tasks | Graph lags ingest by seconds; no retry on failure (acceptable: `backfill` repairs) | ✅ |
| Synchronous in pipeline | Graph always current; errors surface immediately | Adds LLM round-trips to every ingest's critical path; a graph bug blocks ingestion | ❌ |
| Task queue (celery/arq) | Retries, observability | Infrastructure for a personal tool; contradicts zero-server constraint | ❌ |

---

## D8. Mention provenance + repair semantics

**Chosen:** every mention carries a `source_id`; Tier-1 seed wipes and rebuilds **only**
`tier1-*`-tagged mentions; ingest-derived mentions survive re-seeds.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **source_id tagging + selective wipe** (chosen) | `backfill` is safely re-runnable (idempotent, tested); doubles as drift repair; ingest data never lost | source_id is a convention, not a constraint | ✅ |
| Full wipe + rebuild on seed | Simplest | Destroys span-grounded ingest mentions that can't be regenerated from page text alone | ❌ |
| Versioned/temporal mentions (Graphiti's bi-temporal edges) | Audit history; "what did the graph believe on date X" | Schema + query complexity for a feature nothing consumes yet (YAGNI) | Defer until enterprise multi-user |

---

## D9. Entity lifecycle: refcount pruning, no soft delete

**Chosen:** an entity survives while it has mentions OR its own wiki page;
`delete_page()` removes both anchors and prunes orphans (and their aliases) in the same
transaction.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Refcount pruning** (chosen) | graph.db never accumulates garbage; deterministic; mirrors `rag.delete_source()` | Deleting the last referencing page loses the entity's accumulated description/aliases | ✅ |
| Soft delete (archived flag) | Restoring a page could restore its entities | Restore already triggers re-extraction on next touch; tombstones complicate every query | ❌ |
| Keep forever | No data loss ever | Singleton-rate alarm becomes meaningless as junk accumulates | ❌ |

---

## D10. Closed entity type set (5 types) over open vocabulary

**Chosen:** `person | project | system | organization | concept`, validated everywhere
from one tuple (`ENTITY_TYPES`); invalid types from any LLM are skipped, never stored.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Closed set** (chosen) | The single most effective anti-noise lever per the schema-constrained-extraction literature (LlamaIndex `SchemaLLMPathExtractor`, neo4j-graphrag schema modes); UI can color-code; queries can filter | Coarse — "framework" and "algorithm" both flatten to concept/system | ✅ |
| Open vocabulary | Expressive | Entity-type explosion mirrors entity explosion; nothing downstream can rely on types | ❌ |
| Per-domain type schemas | Precision per knowledge area | 10 domains × type sets = config sprawl before any eval justifies it | Defer (eval data may justify later) |

---

## D11. Deliberately deferred (recorded so they aren't re-litigated)

| Deferred item | Why deferred | Trigger to build |
|---|---|---|
| `networkx` + Personalized PageRank | Phase 3 retrieval feature; no consumer yet | Phase 3 (RRF fusion work) |
| Embedding tier wired into ingest hook | Embedder is the one local dependency; judge tier covers borderlines meanwhile | Cloud embedding path, or measured judge-cost pain |
| Alias write-back to page frontmatter | Phase 2 scope (EditMetaPanel integration) | Phase 2 |
| Typed relationship edges (`is-a`, `contradicts`…) | ADR-007: co-mentions + wikilinks ARE the edges for now; ontology layer is its own planned feature | Multi-hop eval shows co-mention edges insufficient |
| Stored `edges` table | Shared-entity edges are derivable by SQL join at query time; materializing is premature | Join shows up in query-latency profiles |

---

## Cross-cutting rationale

Three rules drove nearly every call above:

1. **Consistency over novelty** — every component copies an existing in-repo pattern
   (stores look like `rag/store.py`, hooks look like the eval hook, prompts parse like
   `_parse_ideas`). The next reader learns nothing new.
2. **Deterministic before probabilistic before LLM** — each resolution/grounding step
   exhausts the free option before spending tokens. Measured outcome: the full 135-page
   wiki migration ran with **zero LLM calls**.
3. **Every decision falsifiable** — thresholds came from measured pairs, not intuition;
   ship gates (multi-hop recall up, single-hop no regression, ingest cost < +20%) can
   still kill or amend any of this in the eval phase.
