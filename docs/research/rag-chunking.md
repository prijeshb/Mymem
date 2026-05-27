# Research: RAG Chunking Strategy

Date: 2026-05-05
Branch: V1-0002

---

## GitHub Findings

**Top implementations studied:**

| Library | Approach | Relevance |
|---------|----------|-----------|
| LangChain `MarkdownHeaderTextSplitter` | Splits on `#/##/###`, tracks header hierarchy as metadata | High — direct pattern to port |
| LangChain `ParentDocumentRetriever` | Child chunks (~400t) embedded; parent (~2000t) returned to LLM | High — parent-child pattern |
| LlamaIndex `MarkdownNodeParser` | Header-aware + size-limit fallback via `SentenceSplitter` | Medium — two-step approach |
| `MDKeyChunker` (GitHub) | Rich metadata extraction optimized for RAG | Medium — reference only |
| `md2chunks` (GitHub) | Context-enriched, structure-preserving for knowledge bases | Medium — reference only |

**Key patterns:**
- Header metadata tracked as chunk attributes (not just text)
- Dual-splitter: structure-aware first, then size-aware fallback
- Child-parent document linking for retrieval efficiency

---

## Web Research Findings

### Markdown/Header Chunking
- Split on `#`, `##`, `###` — H2 sections typically represent complete ideas
- Never split inside fenced code blocks
- Header-aware chunking: **87% accuracy vs 13%** for fixed-size on same corpus (clinical NLP, Nov 2025)

### Parent-Child (Small-to-Big) Chunking
- **Child chunks**: 200-300 tokens — embed for retrieval precision
- **Parent chunks**: 512-1024 tokens — return to LLM for context
- Two-tier store: child embeddings + parent text
- Consensus best practice for long-form wiki content

### Metadata to Embed With Each Chunk
Minimum required:
- `source_file`, `page_title`, `heading_path` (e.g. `"Attention > Multi-Head > Scaled Dot-Product"`)
- `domain`, `tags`, `created`, `updated`, `chunk_index`

Key insight from 2025 arXiv: **fuse metadata into the embedding** by prepending
`"{title} > {heading_path}: "` before the chunk text before embedding — not just storing as filter fields.
This increases intra-document cohesion.

### Recursive Chunking
- Separator priority: `\n\n` → `\n` → `. ` → ` `
- Avoids mid-sentence cuts that corrupt embeddings
- **Chroma 2024 benchmark**: 400 tokens, 10% overlap outperformed semantic chunkers on most metrics
- Aggressive overlap hurts storage efficiency without improving recall

### Failure Modes of Naive Fixed-Size Chunking
1. **Context fragmentation** — answers span two chunks, neither retrieves cleanly
2. **Table destruction** — headers in one chunk, values in next
3. **Embedding noise** — incomplete ideas → fuzzy vectors → retrieves adjacent chunks instead of answer
4. **Heading orphaning** — chunk starts mid-section with no heading context
5. **Code block splits** — signatures in one chunk, body in next

### Recommended Defaults for MyMem
| Parameter | Value |
|-----------|-------|
| Strategy | Markdown header split → recursive fallback |
| Child chunk size | 200-300 tokens |
| Parent chunk size | 512-1024 tokens |
| Overlap | 10% of child chunk size (20-30 tokens) |
| Embed level | Child chunks only |
| Return to LLM | Parent chunk |
| Metadata prefix | `"{title} > {heading_path}"` prepended before embedding |

---

## PyPI Dependency Audit

| Package | Weekly DLs | Deps | Verdict |
|---------|-----------|------|---------|
| `chonkie` | ~69K | 4 (light) | ✅ **Recommended** |
| `langchain-text-splitters` | ~7.7M | Heavy (langchain-core + langsmith) | ❌ Too heavy |
| `llama-index-core` | ~1M | Very heavy framework | ❌ Overkill |
| `semantic-text-splitter` | ~10K | Light (Rust binary) | ⚠️ Low adoption |
| `unstructured` | ~1.2M | Very heavy (torch, OCR stack) | ❌ Incompatible |

**Decision: `langchain-text-splitters`**
- `pip install langchain-text-splitters` (~7.7M weekly downloads)
- `MarkdownHeaderTextSplitter` — most battle-tested, best-documented header splitter available
- Extensive community examples and edge-case handling for markdown/wiki content
- `RecursiveCharacterTextSplitter` as fallback — same library, no extra dep
- MIT license, actively maintained, de-facto standard for this use case
- Tradeoff accepted: pulls `langchain-core` + `langsmith` as dependencies

---

## Decision

Implement a two-layer chunking system for MyMem wiki pages:

1. **`MarkdownHeaderTextSplitter` (langchain-text-splitters)** — split wiki `.md` files by headings, preserving header hierarchy as metadata
2. **`RecursiveCharacterTextSplitter`** — fallback for sections without headings
3. **Parent-child store** — embed 200-300 token child chunks; store 512-1024 token parent text
4. **Metadata prefix** — prepend `"{title} > {heading_path}"` before embedding

This is scoped to wiki pages on V1-0002. PDF-specific chunking upgrade is V1-0003.
