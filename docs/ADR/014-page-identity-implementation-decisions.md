# ADR 014: Stable Page Identity — Implementation Decisions

## Status: Accepted (V1-0009, Phase 0)

Implements ADR-013. Records decisions made while building the stable-page-identity
foundation (`mint_id`, `WikiPage.id`, frontmatter I/O, resolution index, backfill).
Each section: what we chose, alternatives, pros/cons, and when to revisit.

---

## D1. `mint_id()` lives in `wiki/types.py`, not `wiki/identity.py`

**Chosen:** put `mint_id()` next to `slugify()` in `types.py`; `identity.py` is the
resolution/backfill facade only.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **`mint_id` in `types.py`** (chosen) | `page.py` (which mints on write) imports from `types` already; no cycle; both ID primitives live together | Minor deviation from the architecture doc that named `identity.py` | ✅ |
| `mint_id` in `identity.py` (as drafted) | One identity module | `identity.py` imports `page.list_pages/write_page` and `page.py` would import `identity.mint_id` → **circular import** | ❌ |

**Revisit when:** never expected — `types.py` is the canonical home for page primitives.

---

## D2. `write_page()` is the single auto-mint choke point

**Chosen:** `write_page()` mints an id when the page has none; no caller changes needed.
Existing pages converge via the explicit `backfill_page_ids()` command.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Auto-mint in `write_page`** (chosen) | Every new page gets an id with zero caller churn; one place to reason about; idempotent (only mints when empty) | A write is required before an id exists (fine — pages are always written) | ✅ |
| Mint in every `WikiPage(...)` constructor | id exists immediately | Every in-memory page (incl. throwaways/tests) gets an id; non-deterministic constructors; breaks equality | ❌ |
| Require callers to pass an id | Explicit | Churns every call site; easy to forget | ❌ |

**Revisit when:** a page must have an id before its first write (no current need).

---

## D3. Exact, normalization-insensitive resolution only (slugify-keyed index)

**Chosen:** `build_page_id_index()` keys on `slugify(title)` and `slugify(stem)`; `resolve_to_id()`
is an exact dict lookup on the normalized key. Fuzzy/embedding/LLM tiers and `aliases:` are deferred.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Exact normalized lookup** (chosen) | Deterministic, zero-cost, fully unit-testable, no LLM/embeddings; covers title↔slug + case/space variants | Won't catch genuine surface-form drift (e.g. "Self-Attention" vs "Scaled Dot-Product Attention") | ✅ for Phase 0 |
| Reuse the 3-tier entity resolver now (ADR-013 D5) | Catches drift | Pulls embeddings/LLM into a foundational module; only needed when ADR-011 actually merges | Defer |

**Revisit when:** ADR-011's reconcile step needs to match drifted surface forms → layer the entity
resolver (`graph/resolver.py`) behind `resolve_to_id` as a fallback, plus `aliases:` frontmatter.

---

## D4. Phase-0 scope fence — identity foundation only

**Chosen:** ship `id` + index + backfill. Do **not** re-key the graph or add redirects yet.

- **Deferred (recorded, not dropped):**
  - **Re-key graph mentions/entities slug → id** — `graph/store.py` still keys pages by
    `page.path.stem`. Migrating it is a separate step within V1-0009, sequenced before ADR-011's
    claims store so claims and graph share the same `page_id`.
  - **Rename redirects (ADR-013 D4)** — no rename surface exists yet; add when a rename action lands.
  - **Wikilink→id resolution at read time** — lands when ADR-011/query consumes identity (D3).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Foundation-only Phase 0** (chosen) | Small, fully-tested, low-risk; unblocks the next steps incrementally | Graph still slug-keyed until the follow-up | ✅ |
| Big-bang (id + graph re-key + redirects together) | One migration | Large, riskier diff touching graph store + claims at once | ❌ |

**Revisit when:** starting ADR-011 — re-key the graph first so both layers key off `page_id`.

---

## D5. `id` attribute name kept (frontmatter parity) with a `# noqa: A003`

**Chosen:** name the field `id` to match the `id:` frontmatter key and natural `page.id` access;
silence ruff's builtin-shadowing warning with a targeted `# noqa: A003`.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **`id` + noqa** (chosen) | `page.id` reads naturally; matches frontmatter + `claims.page_id` naming | One inline noqa | ✅ |
| Rename to `uid`/`page_id` on the dataclass | No shadowing | Mismatch between attribute and the `id:` frontmatter key; less natural | ❌ |

**Revisit when:** the project bans builtin-shadowing project-wide → rename behind the I/O boundary.

---

## D6. Graph re-key slug → id (the D4 deferral, realized in V1-0011)

**Chosen:** the entity graph now anchors pages on the stable `page_id` (ULID) instead of the
slug. `graph/store.py` renames its `page_slug` column to `page_id` on both `entities` and
`mentions` (and the dataclass fields / function params follow); the store stays
identity-agnostic — it persists whatever string key callers give it. Callers pass `page.id`:
`backfill.seed_from_wiki` (`page.id or page.path.stem` fallback for pre-id pages),
`ingest_background._graph_extract_background` (now takes `page_ids`, fed from `touched_pages`),
and the web delete/archive `_graph_cleanup` (`page.id`). Migration is two-step and idempotent:
`init_db` runs a structural `ALTER TABLE … RENAME COLUMN` for legacy DBs, then
`rekey_graph_page_ids()` (CLI: `mymem graph rekey`) converts existing slug values to ids via the
`wiki/identity` index. Unresolvable slugs are left in place (tolerated dangling anchors, never
deleted).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Rename column + value-migrate + callers pass `page.id`** (chosen) | Graph and claims now share `page_id`; survives a future rename; one clean store key; auto structural migration + explicit value re-key keep live DBs safe | Touches store + 2 callers + web cleanup + 5 test files; live DB needs a one-time `mymem graph rekey` | ✅ |
| Keep slug, add a parallel `page_id` column | No caller churn | Two keys to keep in sync; the slug key is the thing we wanted to stop depending on | ❌ |
| Defer until a rename surface exists | Zero work now | Leaves graph and claims keyed differently; re-opens the same migration later under more data | ❌ (user chose to realize it now) |

**Sub-decisions:**
- **Store query functions** (`pages_for_entity`, `entities_for_page`, `mentions_for_page`) return
  / accept ids now. Safe because retrieval RRF (the only would-be consumer) isn't wired yet
  (graph Phase 3) — no production caller depended on slug return values.
- **`/api/graph` untouched** — it builds the *wikilink navigation* graph from `list_pages()`,
  not the entity store; it already addresses pages by slug for the frontend.
- **Value migration needs `wiki/identity`**, which `store.py` (pure, no wiki import) can't call —
  so the slug→id conversion lives in `backfill.rekey_graph_page_ids`, not `init_db`.

**Live migration step:** run `mymem graph rekey` once on the existing `data/graph.db` to convert
slug anchors to ids (the structural rename happens automatically on next `init_db`).

**Revisit when:** a page-rename surface lands → confirm rename updates (or re-keys) graph anchors,
and add the rename redirects still deferred from D4.
