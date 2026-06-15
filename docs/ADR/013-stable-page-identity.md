# ADR 013: Stable Page Identity (id vs slug vs title)

## Status: Proposed (prerequisite for ADR-011)

Context: today a single string does three jobs. `WikiPage.slug` is `slugify(self.title)`
(`mymem/wiki/types.py:99`), and that slug is simultaneously the page's **identity** (graph
mentions key on it, the index, RAG `source_slug`, and ADR-011's proposed `claims` rows), its
**addressing** (the `<slug>.md` filename and `/wiki/:slug` URL), and a shadow of its **display**
title. Because identity is derived from a mutable human label, *renaming a page changes its
identity* — the file moves, every `[[Old Title]]` link breaks, and graph mentions / claims orphan.
It is also why ingest can only overwrite-by-slug (`ingest.py:315`): there is no stable handle to
recognize "same concept, drifted surface form," so `Self-Attention` and `Self Attention Mechanism`
fork into two pages — the page-level version of the entity-explosion ADR-008 solved for entities.

This ADR separates the three jobs so a stable identity exists for ADR-011's MERGE/SUPERSEDE loop.
Each section: what we chose, alternatives, pros/cons, and when to revisit.

---

## D1. Separate identity from display and addressing (surrogate key)

**Chosen:** every page gets a **stable opaque `id`** minted once and never changed. `id` becomes the
primary key everywhere (graph mentions, claims, index, RAG source). `title` is a mutable display
property; `slug` is mutable addressing.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Opaque stable id** (chosen) | Rename-safe identity; enables reliable MERGE/SUPERSEDE; mirrors Notion (UUID + title property) and DB surrogate keys | One-time migration; an extra frontmatter field | ✅ |
| Title/slug as natural key (current) | Zero work; human-readable key | Mutable → identity breaks on rename; forks surface variants; blocks compounding ingest | ❌ (the problem) |
| Content-hash id | Deterministic, no minting | Changes when the page content changes — i.e. *not* stable; defeats the purpose | ❌ |

**Revisit when:** never expected — a surrogate identity is the durable choice. (Format may evolve; see D2.)

---

## D2. id format = ULID (lexicographically sortable, URL-safe, minted once)

**Chosen:** ULID (Crockford base32, 26 chars, time-ordered). Stored as a string in frontmatter.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **ULID** (chosen) | Time-sortable (creation order falls out for free), URL-safe, no hyphens, collision-safe, `python-ulid` is tiny (MIT) | One small dependency | ✅ |
| `uuid4` (stdlib) | No dependency | Not sortable; hyphens; opaque-but-random | ◑ acceptable fallback |
| Auto-increment int | Compact | Needs a central counter (a DB); not portable across machines/merges; leaks count | ❌ |
| `nanoid`/short id | Short | Higher collision risk; not time-ordered | ❌ |

**Revisit when:** adding the `python-ulid` dep is unwanted → fall back to stdlib `uuid4` (or `uuid7`
once stable) behind the same `mint_id()` seam. The format is isolated to one function.

---

## D3. Frontmatter is the source of truth; the title→id index is derived

**Chosen:** `id` lives in each page's YAML frontmatter (travels with the file, git-friendly,
Obsidian-portable). A resolution index mapping `title | alias | slug → id` is **derived** by scanning
frontmatter (optionally cached in SQLite), never the source of truth.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Frontmatter source + derived index** (chosen) | Portable; survives manual edits, git moves, external tools; index is rebuildable | Index can go stale → must be rebuildable cheaply | ✅ |
| SQLite as the source of truth for id | Fast lookups | Identity lost if the DB is deleted/out of sync with files; not portable | ❌ |
| id only in a separate manifest file | Central | Single point of failure; diverges from files | ❌ |

**Revisit when:** scan cost at large page counts hurts → persist the derived index in `data/pages.db`
and invalidate on file mtime (same pattern as RAG/graph stores).

---

## D4. Keep slug as filename + URL; redirect on rename

**Chosen:** the `.md` filename and `/wiki/:slug` URL stay human-readable `slugify(title)`
(addressing layer). On rename, the page keeps its `id`; the file may be renamed and a **redirect**
(old slug → id) is recorded so old links/bookmarks resolve.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Human slug filename + redirects** (chosen) | Readable files/URLs preserved; rename never loses references; MediaWiki/Obsidian pattern | A redirect map to maintain | ✅ |
| `<id>.md` filenames | Rename = pure metadata change | Unreadable on disk; loses the "grep the wiki folder" ergonomics | ❌ |
| No redirects (hard rename) | Simplest | Old `[[links]]`/URLs 404 after rename | ❌ |

**Revisit when:** redirects accrue clutter → periodically garbage-collect redirects with no inbound links.

---

## D5. Wikilinks stay title/alias-based, resolved to id via the existing 3-tier resolver

**Chosen:** authors keep writing `[[Human Title]]`; resolution maps the link text → `id` through the
title/alias index, falling back to the entity layer's existing
`exact/alias → fuzzy+embedding → LLM judge` resolver (`mymem/graph/resolver.py`, ADR-008). Aliases
come from the already-planned `aliases:` frontmatter (graph PRD AC3).

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Title-based links + resolve to id** (chosen) | Human-friendly authoring; reuses resolver already built; handles surface variants | Resolution step on read | ✅ |
| Rewrite all links to `[[id]]` | Unambiguous | Hostile to humans editing markdown; loses readability | ❌ |
| Dual `[[id|Title]]` syntax | Explicit | Verbose; non-standard; manual upkeep | ❌ |

**Revisit when:** resolution ambiguity (two pages share a title/alias) is common → require disambiguation
at write time and surface it in `lint`.

---

## D6. Migration — idempotent, resumable id backfill (mirrors `mymem graph backfill`)

**Chosen:** a phased migration that reuses the ADR-008 Phase-1.5 backfill pattern (idempotent,
resumable, doubles as a repair command).

1. **Type + IO:** add `id: str` to `WikiPage`; the frontmatter reader loads it, the writer mints one
   (`mint_id()`) when absent. New pages get an id automatically from this point on.
2. **Backfill (`mymem pages backfill-ids`):** scan `wiki/`; for each page lacking `id`, mint a ULID
   and write it into frontmatter (idempotent — skips pages that already have one). Build/refresh the
   derived `title|alias|slug → id` index.
3. **Re-key references:** translate existing graph mentions (and any RAG `source_slug` rows) from slug
   → id via the index; keep a `slug → id` redirect map for any links that can't be rewritten.
4. **Cut over lookups:** resolution returns `id`; `slug_to_path()` stays for addressing only; ingest's
   "is this page new?" check becomes an `id` lookup (resolve title/alias → id) instead of
   `page_path.exists()`.
5. **Redirects:** on later renames, record old slug → id so prior references keep resolving.

| Alternative | Pros | Cons | Verdict |
|---|---|---|---|
| **Phased idempotent backfill** (chosen) | Safe, resumable, repairable; no big-bang; reuses proven graph-backfill code | Several steps | ✅ |
| One-shot rewrite of all files + DBs | Fast | Risky; not resumable; hard to verify | ❌ |
| Lazy id-on-next-write only | No migration command | Pages never re-ingested stay id-less indefinitely; index incomplete | ❌ (use backfill to converge) |

**Revisit when:** backfill proves slow at scale → batch frontmatter writes and persist the index per D3.

---

## Consequences

- **Positive:** rename-safe identity; ADR-011 MERGE/SUPERSEDE can reliably find "the same page";
  surface-form variants resolve to one page (page-level fix for the entity-explosion problem);
  human-readable files/URLs and markdown authoring are preserved; reuses the entity resolver.
- **Negative / accepted:** one small dependency (`python-ulid`, or stdlib fallback); a one-time
  migration; a derived index and redirect map to keep rebuildable.
- **Dependency:** **ADR-011 keys `claims.page_id` off this `id`, not the slug.** ADR-013 should land
  (at least Phase 1–2 of the migration) before ADR-011's claims store is built.
