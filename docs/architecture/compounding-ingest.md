# System Design: Compounding Ingest

**PRD:** docs/PRD/compounding-ingest.md · **ADR:** docs/ADR/011-compounding-ingest-and-provenance.md
**Research:** docs/research/knowledge-moat-and-free-tier-routing.md

## Overview

Convert the ingest pipeline from "extract → write/overwrite pages" into "extract atomic propositions
(with spans) → retrieve similar existing knowledge → LLM decides ADD/MERGE/SUPERSEDE/NOOP → apply,
recording provenance and confidence." Contradicted claims are superseded (bi-temporal), never deleted.
This closes the compounding loop so each ingestion improves the existing body instead of accreting.

## Architecture (data flow)

```
 raw source
    │  read_source() (readers.py / social_readers.py)        [unchanged]
    ▼
 security scan + sanitize                                    [unchanged]
    │
    ▼
 EXTRACT  ── Map/Merge/Verify ──►  propositions[]            [Phase 1: + source_span per proposition]
    │                               {text, source_span, title-hint, domain, tags, importance}
    ▼
 for each proposition:
    ├─ embed + retrieve top-k similar existing claims/pages (sqlite-vec)   [Phase 3]
    ├─ DECIDE (router.call task="reconcile")  →  ADD | MERGE | SUPERSEDE | NOOP   [Phase 3]
    │
    ├─ ADD       → new claim row + (new or appended) wiki page
    ├─ MERGE     → enrich existing page body; append source; bump confidence
    ├─ SUPERSEDE → set valid_to/superseded_by on old claim; add new claim     [Phase 4 bi-temporal]
    └─ NOOP      → record corroboration (confidence ↑), no page rewrite
    │
    ▼
 write claims → data/claims.db  (provenance: source_id + span, confidence, temporal)   [Phase 2]
 write/patch wiki page  (write_page)                          [reuses existing]
 update index.md, log.md, curiosity, analytics                [unchanged]
 fire-and-forget: RAG index, graph extract, extraction eval, DECISION eval   [+ decision eval]
```

## Components

### Backend modules

| Module | Change |
|---|---|
| `mymem/wiki/identity.py` *(new, ADR-013)* | `mint_id()` (ULID), the derived `title|alias|slug → id` index, and `resolve_to_id()` reusing the entity resolver. Prerequisite for the decision pipeline |
| `mymem/pipeline/ingest.py` | Replace overwrite-by-slug (`is_update = page_path.exists()`) with: resolve title/alias → stable `page_id`, then the decision pipeline; orchestrate retrieve → decide → apply |
| `mymem/pipeline/ingest.py` `IdeaSchema` | Add `source_span: str = ""` (back-compatible default); optional `claims: list[ClaimSchema]` |
| `mymem/pipeline/reconcile.py` *(new, <300 lines)* | Pure decision logic: build the candidate prompt, parse ADD/MERGE/SUPERSEDE/NOOP, apply rules. `llm_fn`/`router` injected — no LLM in tests |
| `mymem/knowledge/claims.py` *(new)* | Claims store (sqlite): create/read/supersede/delete-by-source; bi-temporal fields; `delete_source()`-style cascade |
| `mymem/pipeline/query.py` | (Phase 4) surface low-confidence/ superseded claims in answers; full RRF is ADR-008 Phase 3 |
| `mymem/pipeline/lint.py` | Report weakly-grounded + contradicted claims (pure Python, stays 100%) |

### Database schema — `data/claims.db`

Claims reference their page by the **stable `page_id`** (ADR-013), never the mutable slug — a rename
or surface-form merge must not orphan provenance. `claims.id` is the claim's own surrogate key;
`page_id` is the page's ULID from frontmatter.

```sql
CREATE TABLE claims (
  id           INTEGER PRIMARY KEY,      -- the claim's own id
  page_id      TEXT NOT NULL,            -- stable page ULID (ADR-013), NOT the slug
  text         TEXT NOT NULL,            -- the atomic proposition
  source_id    TEXT NOT NULL,            -- raw/ filename or URL
  source_span  TEXT NOT NULL DEFAULT '', -- verbatim substring grounding the claim
  confidence   REAL NOT NULL DEFAULT 1.0,
  valid_from   TEXT NOT NULL,            -- ISO date; bi-temporal "valid time"
  valid_to     TEXT,                     -- NULL = currently valid
  superseded_by INTEGER,                 -- FK claims.id; set on SUPERSEDE
  created      TEXT NOT NULL             -- ISO datetime; "transaction time"
);
CREATE INDEX idx_claims_page   ON claims(page_id);
CREATE INDEX idx_claims_source ON claims(source_id);
CREATE INDEX idx_claims_active ON claims(valid_to) WHERE valid_to IS NULL;
```

(Embeddings for similarity reuse the existing sqlite-vec store; claims link to chunks by `page_id`.
Renames never touch this table — only the derived title/slug→id index changes.)

## Data flow — the decision step

1. Proposition `p` extracted with `source_span`.
2. Embed `p.text`; retrieve top-k similar **active** claims/pages (sqlite-vec, `valid_to IS NULL`).
3. Build a decision prompt: `p` + the k candidates (text + page + confidence).
4. `router.call(task="reconcile")` returns, per candidate, one of:
   - **ADD** — no equivalent exists → new claim + page bullet.
   - **MERGE** — augments an existing claim/page → enrich page body, append `source_id`, `confidence += δ`.
   - **SUPERSEDE** — contradicts an existing claim → old claim `valid_to = p.valid_from`,
     `superseded_by = new.id`; new claim added.
   - **NOOP** — already represented → bump corroboration/confidence only.
5. Apply via `claims.py` + `write_page`; all writes immutable/new-object per project rule.

## Security considerations

- Source text already passes secret-scan + `sanitize_for_prompt`; spans are substrings of sanitized
  text. Decision prompt must also wrap candidate text in the sanitizer (defense in depth).
- No new external surface; `claims.db` is local. SUPERSEDE never deletes — auditability preserved.

## Performance considerations

- Extra cost = embed + 1 decision call per proposition batch. Mitigate: batch the decision over all
  candidates in one call; only retrieve-and-decide for the top propositions; cache embeddings.
- `idx_claims_active` keeps the "current view" query cheap.
- Decision task can route to the strongest available free model independently of `compile`.

## Testing strategy

- **Unit:** `reconcile.py` decision parsing + apply rules (synthetic candidates, mocked `llm_fn`);
  `claims.py` CRUD + supersede + cascade (real SQLite on `tmp_path`).
- **Integration:** ingest the same concept twice → assert MERGE enriched (not overwrote); ingest a
  contradiction → assert SUPERSEDE trail; assert no hard-delete.
- **Eval:** decision-agreement against a held-out judge in the existing background eval; ship-gates
  per PRD §Success Metrics.
- **No LLM in tests** — inject `router`/`llm_fn`; mock `router.call`.
