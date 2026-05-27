# Plan: Avoid Duplicate Ingestion

**Date**: 2026-05-05
**Priority**: P2 (medium)

---

## Problem

The LLM wiki-generation pipeline has no deduplication. Submitting the same URL or
file twice runs the full LLM pipeline again — wasting cost and potentially creating
conflicting wiki pages. The RAG vector store already has dedup (`source_exists()`),
but the wiki generation layer has nothing.

### Current State

| Layer | Dedup? | How |
|-------|--------|-----|
| RAG vector store (`rag.db`) | ✅ Yes | `source_exists()` — checks by resolved file path |
| Wiki generation (`ingest_source`) | ❌ No | Full LLM pipeline runs every time |
| RAG for paste-text | ⚠ Partial | `source_id` is the temp filename — unstable across uploads |
| Upload endpoint (`/api/upload`) | ❌ No | New temp path on every upload, RAG key is always "new" |

---

## Approaches Considered

### A — Source manifest table in SQLite (recommended)

Add `ingested_sources` table to `mymem.db`:

```sql
CREATE TABLE ingested_sources (
    id           INTEGER PRIMARY KEY,
    source_key   TEXT UNIQUE,   -- URL or normalized file path / title
    content_hash TEXT,          -- SHA-256 of raw source text
    ingested_at  TEXT           -- ISO datetime
);
```

- Check `source_key` match → skip (same URL or filename)
- Check `content_hash` match → skip (same content under different name)
- `force=True` bypasses both checks
- `GET /api/ingested-sources` lets UI show ingestion history

**Pros**: fast (indexed), covers URL + file + paste-text, consistent with existing `mymem.db` pattern
**Cons**: one extra DB write per ingest

---

### B — Log.md parsing

Scan `log.md` for the source name before ingesting.

**Pros**: no schema change
**Cons**: O(n) on every ingest, fragile as log grows, no content-hash check

---

### C — Reuse RAG `source_exists`

Use `source_exists()` in the wiki pipeline too.

**Pros**: zero new code
**Cons**: doesn't cover URL sources (no RAG for YouTube/webpages); key is the temp path, unstable across uploads

---

## Decision: Approach A

SQLite source manifest is the correct solution. Small scope, fast lookups, covers all source types.

---

## Implementation Plan

### 1. `mymem/pipeline/source_registry.py` (new file, ~60 lines)

```python
class SourceRegistry:
    def record(db_path, source_key, content_hash) -> None
    def is_known(db_path, source_key, content_hash) -> bool   # key OR hash match
    def list_all(db_path) -> list[dict]
    def remove(db_path, source_key) -> None                   # allow re-ingest
```

### 2. `mymem/pipeline/ingest.py` — check before LLM loop

```python
# At the top of ingest_source(), after reading source text:
if SourceRegistry.is_known(db_path, source_key, content_hash):
    return IngestResult(skipped=True, skip_reason="already ingested (use force=True to re-run)")
```

Source key derivation:
- URL sources → use URL as-is
- File uploads → `Path(source).name` (filename, not temp path)
- Paste-text → SHA-256 of first 2000 chars (no stable name)

YouTube URL normalization: extract video ID and use `youtube:{video_id}` as key.

### 3. `mymem/web/routes/api.py` — two new endpoints

```
GET  /api/ingested-sources              → list all recorded sources
DELETE /api/ingested-sources/{key}      → remove entry (enables re-ingest without force)
```

### 4. Frontend — result card + history

- Show "Already ingested — skipped" banner with timestamp when `result.skipped == true`
- Add "Re-ingest" button that sends `force=true`
- Optional: add an "Ingestion History" panel on the Ingest page

---

## Edge Cases & Risks

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Same YouTube video via different URL form | Bypass key-based dedup | Normalize to `youtube:{video_id}` before storing |
| Pasted text has no stable name | Can't key by filename | Use SHA-256 of content as key |
| User edits source and re-ingests | Hash differs → allowed | Correct behaviour — updated content should re-ingest |
| Large wiki with many sources | `list_all` gets slow | Add `LIMIT`/pagination to list endpoint |
| `force=True` on API upload | No UI way to trigger | Add checkbox on Upload form |

---

## Acceptance Criteria

- [ ] Submitting the same URL twice returns `skipped=true` with a clear reason
- [ ] Uploading the same PDF filename twice is blocked (even if temp path differs)
- [ ] Pasting identical text twice is blocked via content hash
- [ ] `force=true` on any ingest endpoint bypasses all dedup checks
- [ ] `GET /api/ingested-sources` returns the ingestion history list
- [ ] `DELETE /api/ingested-sources/{key}` removes the entry so the source can be re-ingested normally
- [ ] No LLM calls made for skipped sources (zero cost waste)

---

## Files to Change

```
mymem/pipeline/source_registry.py   NEW
mymem/pipeline/ingest.py            +source_key derivation, +registry check
mymem/web/routes/api.py             +GET /api/ingested-sources
                                    +DELETE /api/ingested-sources/{key}
frontend/src/pages/IngestPage.tsx   +skipped banner, +Re-ingest button, +force flag
frontend/src/lib/api.ts             +getIngestedSources(), +deleteIngestedSource()
tests/test_source_registry.py       NEW — unit tests for SourceRegistry
tests/test_ingest.py                +dedup test cases
tests/test_web.py                   +endpoint tests
```

Estimated effort: ~3 hours.
