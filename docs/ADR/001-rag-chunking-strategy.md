# ADR 001: RAG Chunking Strategy for Wiki Pages

## Status: Proposed

## Context

MyMem's RAG system currently only indexes local PDFs. Wiki pages — the primary knowledge output
of the system — are never embedded. When users query, semantic search has zero coverage of the
wiki itself. Additionally, the existing PDF chunker uses naive fixed-size sliding windows with
no heading context or metadata, producing noisy embeddings and poor retrieval for structured docs.

## Decision

Implement **markdown/header chunking with parent-child retrieval and metadata-fused embeddings**
for wiki pages, using `chonkie` as the splitting library.

**Chunking flow:**
1. Split wiki `.md` file on headings (`#`, `##`, `###`) using `chonkie.MarkdownChunker`
2. For each heading section, produce a **child chunk** (200-300 tokens) and a **parent chunk** (512-1024 tokens, the full section)
3. Prepend `"{page_title} > {heading_path}: "` to child text before embedding
4. Store child embedding + parent text in `rag.db`
5. At query time: embed query → find top-K child chunks → return parent text to LLM

**New module:** `mymem/rag/wiki_chunker.py`

**Schema addition:** `rag.db` chunks table gets `heading_path`, `parent_text`, `chunk_type` columns.

**Trigger:** After every wiki page write in `ingest.py`, call `wiki_chunker.index_page()` async (best-effort).

## Rationale

- **Header chunking over fixed-size**: H2 sections represent complete ideas. Fixed-size splits orphan headings and fragment context — 87% vs 13% accuracy gap confirmed in literature (PMC 2025).
- **Parent-child over flat**: Small child chunks give precise embedding matches; large parent chunks give the LLM enough context. Flat chunking forces a choice between precision and context.
- **Metadata fusion in embedding**: Prepending title + heading path before encoding improves intra-document cohesion (arXiv 2025). Storing metadata only as filter fields misses this signal.
- **langchain-text-splitters**: `MarkdownHeaderTextSplitter` is the most battle-tested, best-documented markdown splitter (~7.7M weekly downloads). Extensive community examples and edge-case handling justify the `langchain-core` + `langsmith` dependency overhead.
- **Recursive fallback**: For sections without sub-headings, `RecursiveCharacterTextSplitter` (same library, separator priority: `\n\n` → `\n` → `. `) avoids mid-sentence cuts.

## Alternatives Considered

1. **chonkie** — lighter (4 deps) but lower adoption (~69K/wk vs 7.7M) and fewer community examples for edge cases. Rejected: langchain-text-splitters is the established standard.
2. **Flat fixed-size chunks with overlap** — current approach for PDFs. Rejected: heading orphaning, context fragmentation, no metadata signal.
3. **Semantic chunking** (embedding-based cluster splitting) — produces the most coherent chunks but requires an extra embedding pass per chunk during ingestion. Rejected for now: latency cost; revisit on V1-0005+.
4. **llama-index-core** — full framework with excellent node parsers. Rejected: overkill, very heavy dependency footprint.

## Consequences

**Positive:**
- Wiki pages become first-class RAG citizens — queries surface wiki knowledge alongside PDFs
- Retrieval precision improves: heading-scoped child chunks match queries tightly
- Retrieval context improves: parent chunks give LLM full section body
- Metadata (domain, tags, heading path) enables future filtered search

**Negative:**
- `rag.db` schema requires migration (new columns on chunks table)
- `chonkie` added to dependencies (4 new packages, minimal)
- Wiki ingest slightly slower — async index call after each page write

**Risks:**
- Hash-based deduplication needed to avoid duplicate chunks on wiki page updates
- `chonkie` API stability — pin version to avoid breaking changes
