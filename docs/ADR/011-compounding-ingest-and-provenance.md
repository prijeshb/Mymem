# ADR 011: Compounding Ingest — Atomic Propositions, UPDATE Pipeline, Provenance

## Status: Proposed (target V1-0009)

Context: MyMem's moat is the maintained structure over sources, which only compounds if ingest
*mutates* existing knowledge. Today ingest overwrites by slug (`ingest.py:315`) — append-not-compound.
This ADR records the decisions for converting ingest into a compounding loop. See
docs/research/knowledge-moat-and-free-tier-routing.md for the survey behind each choice.

Each section: what we chose, the alternatives, pros/cons, and when to revisit.

---

## D1. Reconcile decision = LLM-chosen ADD / MERGE / SUPERSEDE / NOOP (Mem0 pattern)

**Chosen:** for each extracted proposition, retrieve top-k similar existing claims/pages and have the
LLM pick one operation per candidate — no separate classifier (Mem0's exact design).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **LLM ADD/MERGE/SUPERSEDE/NOOP** (chosen) | Reasons over semantic relationship; one mechanism; literal "every ingest improves the body" | Costs a decision call; quality depends on model | ✅ |
| Overwrite-by-slug (current) | Trivial | Destroys prior evidence; no compounding | ❌ (the problem) |
| Embedding-threshold auto-merge (no LLM) | Cheap, deterministic | Can't tell "contradicts" from "augments"; merges false positives | ❌ (use as pre-filter only) |
| Rule-based classifier | No LLM cost | Brittle; can't generalize across domains | ❌ |

**Revisit when:** decision-eval precision is too low on free-tier models → consider routing the
decision task to a stronger model or a fine-tuned small classifier.

---

## D2. Atomic propositions with verbatim source spans (Dense X + GraphRAG covariates)

**Chosen:** extraction emits atomic propositions, each carrying a verbatim `source_span`. Spans are
the grounding unit for provenance, dedup, and the reconcile decision.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Atomic propositions + spans** (chosen) | Higher idea density; enables provenance + mechanical anti-hallucination check | More extraction tokens; finer-grained storage | ✅ |
| Keep current paraphrased `evidence` | Already implemented | Not verbatim → can't ground/validate or cite precisely | ❌ |
| Full typed claim triples (subject/rel/obj) everywhere | Richest structure | ~2× extraction cost (GraphRAG turns it off by default) | Defer to optional pass |

**Anti-hallucination:** a proposition is rejected if its `source_span` doesn't substring/fuzzy-match
the (sanitized) source — same mechanical check pattern as the entity span-grounding in ADR-008.

**Revisit when:** an ontology layer needs typed triples → promote the optional claim pass to default.

---

## D3. Bi-temporal supersede — invalidate, never hard-delete (Zep/Graphiti)

**Chosen:** contradicted claims get `valid_to` + `superseded_by` set; rows are retained. The "current
view" is `valid_to IS NULL`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Bi-temporal invalidate** (chosen) | No data loss; auditable; a bad SUPERSEDE is recoverable; correct "current" view | Extra columns + a row per supersede | ✅ |
| Hard-delete/overwrite old claim | Simplest storage | A wrong merge is irreversible — unacceptable given LLM decision risk | ❌ |
| Append-only (keep both, no invalidation) | No deletes | "Current" view becomes ambiguous; contradictions accumulate | ❌ |

**Revisit when:** storage growth is a problem at scale → archive superseded claims to a cold table.

---

## D4. Storage — dedicated `data/claims.db`, reuse sqlite-vec for similarity

**Chosen:** a new `claims` table (own SQLite file) for provenance + temporal fields; similarity search
reuses the existing sqlite-vec store keyed by `page_slug`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Separate claims.db + reuse sqlite-vec** (chosen) | Clean separation; reuses embedder/store; matches `graph.db`/`rag.db` pattern | One more db file | ✅ |
| Put claims in `graph.db` | Co-located with entities | Mixes concerns; claims aren't entities | ❌ (revisit if they converge) |
| New vector index just for claims | Tailored | Duplicates the embedding pipeline | ❌ |

**Revisit when:** claims and entities prove to share most queries → consider merging the DBs.

---

## D5. Confidence + source-trust: store now, learn later

**Chosen:** every claim gets a `confidence` field = f(source-trust, corroboration count, recency),
with source-trust defaulting to 1.0 in v1. The KBT-style *learning* of source trust (and the usage
feedback loop) is reserved in schema but deferred.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Store confidence, defer trust learning** (chosen) | Unblocks lint/UI surfacing + merge tie-breaks now; avoids premature complexity | Trust is static until the follow-up | ✅ |
| Full KBT source-trust in v1 | Strongest tie-breaking | Joint inference is heavy; needs corpus signal we don't have yet | ❌ (fast-follow) |
| No confidence at all | Simplest | Loses the merge tie-breaker and the "weak claim" UX | ❌ |

**Revisit when:** enough corroboration data exists to make trust learning meaningful (post-v1).

---

## D6. Scope fence — v1 is propositions → UPDATE → provenance only

**Chosen:** ship the compounding core; explicitly defer the multipliers.

- **Deferred to fast-follow:** drift-triggered re-summarization + `lint --consolidate`; usage feedback
  loop (Axis 4); source-trust learning.
- **Tracked elsewhere:** query-time RRF + small-to-big (Axis 3) folds into ADR-008 Phase 3.
- **Not building:** full ontology/typed-relationship graph (remains the planned ontology layer).

**Revisit when:** core ships green (merge precision + no recall regression + <+20% cost), then pull the
next multiplier by leverage order from the research doc §3.
