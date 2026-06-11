# System Design: Graph Entity Mapping

**PRD:** docs/PRD/graph-entity-mapping.md · **ADR:** docs/ADR/007-graph-entity-mapping.md

## Overview

A lightweight entity layer on SQLite that grounds LLM-extracted entities against the
existing wiki page catalog, connects pages through shared entities, and feeds a 1-hop
graph expansion into the existing hybrid retrieval. No community detection, no graph DB,
two new zero-dep packages (networkx, rapidfuzz). Ports: Graphiti's 3-tier resolution,
LightRAG's extraction prompt shape, Obsidian's alias-linking heuristics.

## Architecture Diagram

```
INGEST (extended)
  source ──► read ──► scan ──► LLM extract ideas ──► compile pages
                                  │ (same call, extended prompt)
                                  ▼
                       typed entities (name, type, desc, span)
                                  │
                                  ▼
                       ┌─ 3-TIER RESOLUTION ────────────────┐
                       │ 1. exact: slugify(name) + aliases   │ entity_resolver.py
                       │ 2. fuzzy: rapidfuzz token_sort      │
                       │    + sqlite-vec cosine (thr ~0.85)  │
                       │ 3. LLM judge: batched borderlines   │
                       └────────────┬───────────────────────┘
                                    ▼
                       graph.db: entities / aliases / mentions
                                    │
                                    ▼ (background, fire-and-forget)
                       entity eval: consensus + span-grounding

QUERY (extended)
  question ──► hybrid keyword+vector (existing) ──► top-k pages/chunks
                                  │
                                  ▼
                       1-hop expansion: wikilinks ∪ shared-entity edges
                       (SQLite CTE; optional PPR via networkx)
                                  │
                                  ▼
                       RRF fusion ──► LLM synthesis (existing)

LINT (extended)
  wiki pages ──► title+alias catalog ──► word-boundary regex scan
            ──► "unlinked mention" report (zero LLM)
```

## Components

### Backend — new modules

```
mymem/graph/
  store.py        GraphStore — entities/aliases/mentions/edges CRUD + delete_page() cleanup
  resolver.py     EntityResolver — 3-tier resolution (Strategy per tier; degrades gracefully
                  when embedder unavailable: skip tier 2 cosine, keep rapidfuzz)
  extractor.py    extract_entities() — extends ingest extraction; prompt emits
                  entity<|#|>name<|#|>type<|#|>description tuples + gleaning pass
  expand.py       expand_neighbors(slugs, hops=1) — recursive CTE over wikilinks ∪
                  shared-entity edges; rrf_fuse(rankings) helper
  linker.py       suggest_links(page_body, catalog) — Obsidian-style deterministic
                  alias/title matcher (word boundaries, capitalization-aware)

mymem/evals/
  entity_eval.py  EntityExtractionEval(Evaluator[T]) — consensus + span-grounding;
                  MultiHopRetrievalEval — KGQAGen-style A/B
```

### Database Schema — `data/graph.db`

```sql
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical   TEXT NOT NULL UNIQUE,      -- normalized canonical name
    type        TEXT NOT NULL,             -- person|project|system|organization|concept
    description TEXT NOT NULL DEFAULT '',  -- accumulated, LLM-summarized when >N fragments
    page_slug   TEXT,                      -- linked wiki page if entity has its own page
    created     TEXT NOT NULL,
    updated     TEXT NOT NULL
);
CREATE TABLE aliases (
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    alias       TEXT NOT NULL,
    UNIQUE(entity_id, alias)
);
CREATE TABLE mentions (
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    page_slug   TEXT NOT NULL,
    span        TEXT NOT NULL DEFAULT '',  -- grounding evidence from source
    source_id   TEXT NOT NULL DEFAULT '',
    created     TEXT NOT NULL
);
CREATE INDEX idx_mentions_page ON mentions(page_slug);
CREATE INDEX idx_mentions_entity ON mentions(entity_id);
-- entity-name embeddings live in existing rag.db sqlite-vec table (new collection)
-- shared-entity edge = two pages joined through mentions; computed via SQL, not stored
```

### API

| Endpoint | Description |
|----------|-------------|
| `GET /api/entities` | List entities (filter by type), mention counts |
| `GET /api/entities/{id}` | Entity detail: aliases, mentions, connected pages |
| `GET /api/graph?layer=entities` | Existing graph endpoint gains entity-edge layer |
| `GET /api/lint` | Gains `unlinked_mentions` section |
| `GET /api/evals/entities` | Entity eval runs (same pattern as /api/evals/extraction) |

### Frontend

- `GraphPage.tsx`: layer toggle (wikilinks / entities / both); entity edges styled differently
- `WikiPage.tsx` + `WikiSidePane.tsx`: entity chips with type badge; click → entity detail
- `EvalsPage.tsx`: tab or section for entity eval runs
- `EditMetaPanel.tsx`: edit `aliases` alongside domain/tags

## Data Flow (ingest)

1. Existing extraction LLM call extended → ideas + entity tuples
2. For each entity: resolve (tier 1 exact/alias → tier 2 rapidfuzz + cosine → tier 3 batched LLM)
3. Upsert entity, aliases, mention rows; embed new canonical names into sqlite-vec
4. Linker suggests [[wikilinks]] for resolved entities that have pages
5. Background eval: consensus + span-grounding → evals.db
6. Page delete/archive → `GraphStore.delete_page(slug)` cleans mentions (mirror RAG delete_source)

## Migration: Backfilling the Existing Wiki (~114 pages)

The existing wiki is migrated in three tiers — cheapest first, each tier independently
valuable. New ingests use the full pipeline from day one; backfill catches history up.

### Tier 1 — Seed from structure (deterministic, zero LLM, instant)

1. **Every existing page becomes an entity**: canonical = page title,
   `page_slug` = slug, aliases = `aliases` frontmatter if present (empty initially).
2. **Every existing wikilink becomes a mention**: `[[B]]` inside page A →
   mention(entity-of-B, page A). Reuses the same wikilink parser lint/graph use today.
3. **Broken wikilinks become unresolved entities** (no `page_slug`) — they are
   first-class entities that simply lack a page yet; surfaced in lint.

Result: the graph view's entity layer works immediately with exactly the connectivity
the wiki already encodes. Idempotent — safe to re-run.

### Tier 2 — Classify + alias bootstrap (cheap LLM, one batch)

- `classify`-task model (small/cheap) assigns each page-entity a type from the closed
  set (person/project/system/organization/concept) using title + domain + tags;
  default `concept` on low confidence.
- Same call proposes 0–3 aliases per entity ("LLM" for "Large Language Models").
  Aliases are written to page frontmatter so the user can edit them in EditMetaPanel.
- Batched ~20 entities per call → ~6 calls for the current wiki.

### Tier 3 — Full extraction backfill (compile LLM, resumable, optional)

`mymem graph backfill [--domain X] [--limit N]`

- Iterates pages, runs the same entity extraction used at ingest on each page body,
  resolves through the 3-tier resolver against the (now-seeded) catalog, writes
  mentions with spans.
- **Resumable**: `backfill_state(page_slug, content_hash, processed_at)` in graph.db —
  re-runs skip pages whose hash is unchanged (same `skip_unchanged` pattern as ingest).
- **Cost-gated**: runs through the router so SessionCostTracker applies; `--limit`
  bounds a session; recommended order is one domain at a time.
- **Eval-gated**: entity eval (consensus + span-grounding) runs per batch; if the
  singleton-entity rate alarm trips, stop and tune the resolver before continuing.

### Keeping it in sync after migration

- Page re-ingested/edited → delete its mentions by `page_slug`, re-extract
  (mirror of `rag.delete_source()` + re-index).
- Page deleted/archived → `GraphStore.delete_page(slug)` removes mentions; entity
  survives if other pages still mention it, else is pruned (refcount on mentions).
- Tier 1 re-seed runs harmlessly any time (idempotent upserts) — acts as a repair
  command if graph.db and wiki/ ever drift.

## Security Considerations

- Entity names/aliases are LLM output rendered in UI — React escapes by default; no
  dangerouslySetInnerHTML
- `graph.db` queries parameterized; order-by clauses allowlisted (same fix as evals store)
- Span text stored verbatim from source — passes through existing ingest security scan first
- Future multi-user: graph expansion must be ACL-filtered per traversal (recorded, not built)

## Performance Considerations

- Resolution tier 2 is one sqlite-vec query per new entity (~10–30 entities/ingest)
- Tier 3 LLM judge batched into ONE call per ingest (Graphiti pattern)
- 1-hop expansion: indexed SQL join, sub-ms at 10k pages; cap 100 neighbors
- networkx loaded on demand for PPR only; not in the hot path initially

## Testing Strategy

- `graph/store.py` — 100% (pure SQLite, mirror rag/store.py standard)
- `resolver.py` — inject fake embedder + fake llm_fn; test each tier + degradation path
- `linker.py` — pure Python, table-driven cases (word boundaries, case rules, aliases)
- `expand.py` — tmp_path SQLite fixtures; CTE correctness, neighbor caps
- Evals — fake llm_fn consensus; span-grounding is mechanical (no mock needed)
- API routes — TestClient, no server
