# RAG Improvement Opportunities

Code-grounded findings across `mymem/rag/` and `mymem/pipeline/query.py`.
Ordered by impact.

---

## 1. parent_text Not Used at Query Time (Bug / High Impact)

**File:** `mymem/pipeline/query.py:219`

```python
context.append((label, r.chunk.text))   # ← child chunk only
```

`parent_text` is stored in the DB precisely to give the LLM the full section context,
but `_fetch_rag_context` sends only the small child chunk to the LLM. The whole
parent-child strategy is bypassed at query time.

**Fix:**
```python
body = r.chunk.parent_text or r.chunk.text
context.append((label, body))
```

---

## 2. Domain Filter Not Applied to RAG Search (Inconsistency / High Impact)

**Files:** `mymem/pipeline/query.py:107-108, 213`

Wiki search respects `domain_filter`; the RAG vector search ignores it:

```python
# Wiki search — filtered
candidates = [e for e in candidates if e.domain == domain_filter]

# RAG search — domain_filter not passed
results = search_similar(db_path, query_vec, top_k=top_k)
```

A user querying "tech" domain gets wiki pages filtered to tech but RAG chunks from any domain.

**Fix:** Add `domain: str | None = None` to `search_similar()` and filter in SQL:
```sql
WHERE e.embedding MATCH ?
  AND k = ?
  AND (c.domain = :domain OR :domain IS NULL)
```
This requires a JOIN with `rag_chunks` before the vec0 MATCH (sqlite-vec supports this).

---

## 3. Zero-Vector Silent Failure (Reliability / Medium Impact)

**File:** `mymem/rag/embedder.py:54-55`

```python
results.extend([[0.0] * EMBED_DIM] * len(batch))
```

When Ollama is unavailable, chunks are stored with zero vectors. A zero vector has equal
cosine distance to everything — those chunks will appear in every search result,
polluting results silently with no user-visible signal.

**Fix:** Raise or return a partial result with a clear error. Do not index zero vectors.
```python
raise RuntimeError(f"Ollama embed failed for batch starting at {i}: {exc}")
```
Or, skip the batch and log the affected `source_path` so the user knows to re-index.

---

## 4. Duplicate Parent Sections in Search Results (Quality / Medium Impact)

**File:** `mymem/rag/store.py:search_similar`

If two child chunks from the same heading section both score in top-K, both are returned
separately with the same `parent_text`. The LLM sees the same section twice, wasting
context window and crowding out other sources.

**Fix:** Deduplicate results by `(source_slug, heading_path)` after retrieval,
keeping only the highest-scoring chunk per section:
```python
seen: set[tuple[str, str]] = set()
deduped = []
for r in results:
    key = (r.chunk.source_slug, r.chunk.heading_path or "")
    if key not in seen:
        seen.add(key)
        deduped.append(r)
```

---

## 5. Custom YAML Frontmatter Parser (Fragility / Low-Medium Impact)

**File:** `mymem/rag/wiki_chunker.py:49-70`

`_extract_frontmatter` is a hand-rolled parser that only handles `key: value` lines.
It silently drops multi-line values, nested keys, and quoted strings.
If a page title contains a colon (`title: "Rust: ownership"`) it mis-parses.

**Fix:** Use PyYAML (already a transitive dep via langchain):
```python
import yaml

def _extract_frontmatter(content: str) -> tuple[dict[str, str], str]:
    if not content.startswith("---"):
        return {}, content
    end = content.find("\n---", 3)
    if end == -1:
        return {}, content
    meta = yaml.safe_load(content[3:end]) or {}
    body = content[end + 4:].lstrip()
    tags = meta.get("tags", [])
    meta["tags"] = ",".join(tags) if isinstance(tags, list) else str(tags)
    return {k: str(v) for k, v in meta.items()}, body
```

---

## 6. No Content-Hash to Skip Unchanged Pages (Efficiency / Low Impact)

**File:** `mymem/rag/ingest.py:ingest_wiki_page`

Every `force=True` call deletes and re-embeds all chunks for a page, even if the
content didn't change. On a large wiki, a full re-index is expensive.

**Fix:** Store a SHA-256 hash of the file content in `rag_chunks` (one row per source).
At ingest time, compare current hash with stored hash; skip if equal.
```python
import hashlib
content_hash = hashlib.sha256(page_path.read_bytes()).hexdigest()
# Store in rag_chunks.content_hash; skip if unchanged
```

---

## 7. Large IN Clause in delete_source (Safety / Low Impact)

**File:** `mymem/rag/store.py:275-280`

```python
placeholders = ",".join("?" * len(chunk_ids))
conn.execute(f"DELETE FROM rag_embeddings WHERE chunk_id IN ({placeholders})", chunk_ids)
```

SQLite's default `SQLITE_LIMIT_VARIABLE_NUMBER` is 999. A source with 1000+ chunks
will silently fail or raise. Should use batched deletes or a subquery:

```python
conn.execute(
    "DELETE FROM rag_embeddings WHERE chunk_id IN (SELECT id FROM rag_chunks WHERE source_path = ?)",
    (source_path,),
)
```

---

## Summary Table

| # | Issue | File | Impact | Effort |
|---|-------|------|--------|--------|
| 1 | parent_text not used at query time | query.py:219 | High | Trivial (1 line) |
| 2 | Domain filter skips RAG search | query.py:213, store.py | High | Small |
| 3 | Zero-vector silent failure | embedder.py:55 | Medium | Small |
| 4 | Duplicate parent sections in results | store.py | Medium | Small |
| 5 | Custom YAML parser fragility | wiki_chunker.py | Low-Med | Small |
| 6 | No content-hash skip | ingest.py | Low | Medium |
| 7 | IN clause overflow | store.py:275 | Low | Trivial (1 line) |
