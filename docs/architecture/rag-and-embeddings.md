# RAG & Embeddings — How It Works in MyMem

## What Is an Embedding?

An embedding is a fixed-size list of numbers (a vector) that captures the *meaning* of a piece of text. Similar texts produce numerically close vectors; unrelated texts land far apart.

```
"What is attention?"          → [0.12, -0.45, 0.87, ... 768 numbers]
"Self-attention mechanism"    → [0.14, -0.43, 0.89, ...]  ← close
"How to bake bread?"          → [-0.91, 0.23, -0.12, ...]  ← far
```

In MyMem: **nomic-embed-text** via Ollama produces **768-dimensional float vectors**
(`mymem/rag/embedder.py`). Texts are batched in groups of 32 per Ollama call.

---

## Index-Time Flow (Ingest)

```
Document (wiki page or PDF)
        │
        ▼
 [1. CHUNK]   Split into small pieces
        │     Wiki  → MarkdownHeaderTextSplitter by headings → RecursiveCharacterTextSplitter (~300 tokens)
        │     PDF   → sliding-window by page, 300-token chunks with 30-token overlap
        │
        ▼
 [2. PREFIX]  Prepend context before embedding (wiki only)
        │     "Page Title > Section Heading: {child chunk text}"
        │     Richer signal: the vector knows *where* in the doc this came from
        │
        ▼
 [3. EMBED]   embed_texts() → one float[768] per chunk
        │     Ollama nomic-embed-text, batch 32
        │
        ▼
 [4. STORE]   Insert into SQLite (mymem/rag/store.py), two tables:
              rag_chunks      — raw text + metadata (slug, heading, domain, tags, parent_text)
              rag_embeddings  — vec0 virtual table (chunk_id → float[768])
```

Files: `mymem/rag/ingest.py`, `mymem/rag/wiki_chunker.py`, `mymem/rag/pdf_parser.py`

---

## Parent-Child Chunking (Wiki Pages)

Wiki pages use a two-tier strategy defined in `mymem/rag/wiki_chunker.py`:

| Tier | Size | Role |
|------|------|------|
| **Child chunk** | ~300 tokens (1200 chars) | Embedded — small for *precise* vector matching |
| **Parent text** | up to 4096 chars | Full heading section — stored for *LLM context* at query time |

Why: a small chunk matches accurately; the full section gives the LLM enough surrounding
context to write a good answer. This avoids the precision-vs-context tradeoff.

```
Wiki page
  └─ ## Section A                        ← parent_text (full section, ≤4096 chars)
       ├─ child chunk 1 (embedded)
       └─ child chunk 2 (embedded)
  └─ ## Section B
       ├─ child chunk 3 (embedded)
       └─ child chunk 4 (embedded)
```

---

## Query-Time Flow (Retrieval)

```
User question
        │
        ▼
 [1. EMBED QUERY]    embed_query() → single float[768]
        │
        ▼
 [2. VECTOR SEARCH]  store.py:search_similar()
        │            sqlite-vec: WHERE embedding MATCH ? AND k = ?
        │            Returns top-K chunks sorted by cosine distance
        │
        ▼
 [3. MERGE CONTEXT]  query.py combines:
        │            - Wiki pages (keyword search via index.md)
        │            - RAG chunks (vector search via rag_embeddings)
        │
        ▼
 [4. LLM SYNTHESIZE] Router calls the qa model with full context
                     Citations: [[Wiki Title]] for wiki, [PDF: file p.N] for PDF
```

File: `mymem/pipeline/query.py`

---

## Storage Schema

```sql
-- Chunk metadata
CREATE TABLE rag_chunks (
    id           INTEGER PRIMARY KEY,
    source_path  TEXT,      -- absolute path to source file
    source_slug  TEXT,      -- slug derived from title
    chunk_index  INTEGER,   -- position within source
    page_num     INTEGER,   -- PDF page (NULL for wiki chunks)
    text         TEXT,      -- child chunk text (stored in DB)
    char_count   INTEGER,
    created_at   TEXT,
    heading_path TEXT,      -- e.g. "Introduction > Background"
    parent_text  TEXT,      -- full heading section for LLM context
    chunk_type   TEXT,      -- always "child"
    page_title   TEXT,
    domain       TEXT,
    tags         TEXT
);

-- Vector index (sqlite-vec virtual table)
CREATE VIRTUAL TABLE rag_embeddings USING vec0(
    chunk_id  INTEGER PRIMARY KEY,
    embedding FLOAT[768]
);
```

sqlite-vec's `MATCH` operator performs approximate nearest-neighbor search on the
stored float arrays — no external vector database needed, everything stays in `data/mymem.db`.

---

## Known Improvement Areas

See `docs/architecture/rag-improvements.md` for a prioritized list.
